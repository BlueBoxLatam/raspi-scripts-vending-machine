import requests
import time
import subprocess
import os
import socketio
import signal
import sys
import threading

# --- HARDWARE IMPORTS ---
# Intentamos importar librerías de Hardware. Si fallan, entramos en "Modo Simulación".
try:
    import board
    import busio
    from adafruit_pn532.i2c import PN532_I2C
    import RPi.GPIO as GPIO
    NFC_REAL_MODE = True
except ImportError:
    print("⚠️ [MODO SIMULACIÓN] Hardware (NFC/GPIO) no detectado. Usando modo virtual.")
    NFC_REAL_MODE = False

# ================= CONFIGURACIÓN BLUE BOX =================
VM_IP = "34.55.59.16" 
API_URL = f"http://{VM_IP}:3000"
ID_ENDPOINT = f"{API_URL}/api/identify-student"

# Configuración SRT (Baja Latencia)
SRT_URL = f"srt://{VM_IP}:9000?mode=caller&latency=2000000"

VENDING_ID = "vm_001" 
LOCK_PIN = 17           # Pin GPIO del Cerrojo Electrónico
TIMEOUT_SECONDS = 180   # 3 Minutos de inactividad apaga la cámara

# Estado Global
sio = socketio.Client()
is_streaming = False
stream_process = None
pn532 = None
waiting_for_server_unlock = False 

# Variable crítica para el Watchdog (Tiempo de última lectura NFC)
last_activity_time = time.time()

# ================= HARDWARE SETUP =================

def init_hardware():
    global pn532
    if not NFC_REAL_MODE: return

    # 1. Inicializar NFC
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        pn532.SAM_configuration()
        print("✅ [NFC] PN532 Inicializado.")
    except Exception as e:
        print(f"❌ [NFC] Error Hardware: {e}")

    # 2. Inicializar GPIO (Cerrojo)
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LOCK_PIN, GPIO.OUT)
        GPIO.output(LOCK_PIN, GPIO.LOW) # Asegurar puerta cerrada al inicio
        print(f"✅ [GPIO] Pin {LOCK_PIN} configurado para cerrojo.")
    except Exception as e:
        print(f"❌ [GPIO] Error Hardware: {e}")

def read_nfc_non_blocking():
    """Lee NFC sin bloquear el hilo principal"""
    if not NFC_REAL_MODE:
        # Simulación: En modo PC, no retornamos nada automático para no saturar
        # time.sleep(0.1)
        return None 
    
    try:
        # timeout=0.1 hace que no se congele esperando tarjeta
        uid_bytes = pn532.read_passive_target(timeout=0.1) 
        if uid_bytes:
            return ':'.join(hex(b)[2:].zfill(2).upper() for b in uid_bytes)
    except Exception:
        pass
    return None

def set_lock(state):
    """Controla el GPIO de la cerradura electrónica"""
    timestamp = time.strftime("%H:%M:%S")
    
    if state == 'open':
        print(f"\n🔓 [{timestamp}] >>> CERRADURA ABIERTA <<< (Acceso Permitido)")
        if NFC_REAL_MODE:
            GPIO.output(LOCK_PIN, GPIO.HIGH) # Activar Relé/Transistor
    else:
        print(f"🔒 [{timestamp}] Cerradura Bloqueada.")
        if NFC_REAL_MODE:
            GPIO.output(LOCK_PIN, GPIO.LOW)  # Desactivar

# ================= VIDEO STREAMING (FFmpeg) =================

def start_ffmpeg():
    global stream_process, is_streaming
    
    # Lógica de continuidad: Si ya corre, NO reiniciamos.
    if is_streaming and stream_process:
        if stream_process.poll() is None: # Verificar que el proceso sigue vivo
            print("⚡ [FFMPEG] El stream ya está activo. Reutilizando señal.")
            return
        else:
            print("⚠️ [FFMPEG] Proceso muerto detectado. Reiniciando...")

    print(f"🎥 [FFMPEG] Iniciando stream SRT hacia {VM_IP}...")
    
    # Comando optimizado para baja latencia (Zero Latency)
    cmd = [
        'ffmpeg', 
        '-f', 'v4l2', 
        '-framerate', '15',
        '-video_size', '640x480',
        '-i', '/dev/video0', 
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100', # Audio mudo dummy
        '-pix_fmt', 'yuv420p', 
        '-c:v', 'libx264',           
        '-preset', 'ultrafast',      
        '-tune', 'zerolatency',      
        '-g', '8',                   # Keyframe cada 0.5s (8 frames / 15 fps)
        '-keyint_min', '8',              
        '-bufsize', '600k',          
        '-f', 'mpegts', 
        SRT_URL
    ]
    
    # 'preexec_fn=os.setsid' permite matar todo el grupo de procesos después
    stream_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
    is_streaming = True
    
    # Avisar al servidor que la cámara despertó (UI Update)
    if sio.connected:
        sio.emit('stream_status_change', {'machineId': VENDING_ID, 'status': 'online'})

def stop_ffmpeg():
    global stream_process, is_streaming
    if stream_process:
        print("🛑 [FFMPEG] Deteniendo transmisión por inactividad...")
        try:
            os.killpg(os.getpgid(stream_process.pid), signal.SIGTERM)
            stream_process.wait()
        except Exception as e:
            print(f"Error matando ffmpeg: {e}")
        
        stream_process = None
        is_streaming = False
        
        # Avisar al servidor que la cámara duerme (UI Update)
        if sio.connected:
            sio.emit('stream_status_change', {'machineId': VENDING_ID, 'status': 'offline'})

# ================= WATCHDOG (Hilo de Supervisión) =================
def watchdog_loop():
    """
    Revisa en segundo plano si pasaron 3 minutos sin uso.
    Si es así, apaga la cámara para ahorrar datos y CPU.
    """
    global is_streaming
    print("👀 [WATCHDOG] Supervisor de inactividad iniciado.")
    
    while True:
        time.sleep(5) # Revisión cada 5 segundos
        
        if is_streaming:
            idle_time = time.time() - last_activity_time
            
            # Si pasaron más de TIMEOUT_SECONDS (180s) sin tarjetas nuevas
            if idle_time > TIMEOUT_SECONDS:
                print(f"💤 [AUTO-SLEEP] Inactividad detectada ({int(idle_time)}s). Apagando cámara.")
                stop_ffmpeg()

# ================= SOCKET.IO EVENTS =================

@sio.event
def connect():
    print("⚡ [SOCKET] Conectado al Servidor Blue Box.")
    sio.emit('join_machine', {'id': VENDING_ID})
    # Si nos reconectamos y estamos transmitiendo, avisar estado
    if is_streaming:
        sio.emit('stream_status_change', {'machineId': VENDING_ID, 'status': 'online'})

@sio.event
def disconnect():
    print("❌ [SOCKET] Desconectado.")

@sio.on('server_verified_video')
def on_server_auth(data):
    global waiting_for_server_unlock
    machine_id = data.get('machineId')
    
    # Solo abrimos si es para nosotros y está autorizado
    if machine_id == VENDING_ID and data.get('authorized'):
        # Solo abrimos si estábamos en medio de una transacción (estado de espera)
        if waiting_for_server_unlock:
            print(f"✅ [CONFIRMADO] Video verificado por servidor.")
            set_lock('open')
            waiting_for_server_unlock = False

@sio.on('force_remote_close')
def on_close(data):
    global waiting_for_server_unlock
    if data.get('machineId') == VENDING_ID:
        print("\n🏁 [FIN] Transacción completada.")
        set_lock('close')
        # NOTA IMPORTANTE: Ya NO paramos ffmpeg aquí. 
        # Dejamos que siga corriendo para el siguiente alumno.
        # El watchdog lo apagará si nadie más viene.
        waiting_for_server_unlock = False

# ================= MAIN LOOP =================

def main():
    global is_streaming, waiting_for_server_unlock, last_activity_time

    init_hardware()
    
    # Iniciar Watchdog en hilo independiente
    wd_thread = threading.Thread(target=watchdog_loop, daemon=True)
    wd_thread.start()

    # Conexión Socket Inicial
    while not sio.connected:
        try:
            sio.connect(API_URL)
        except Exception:
            print("Reintentando conexión socket...")
            time.sleep(2)

    print("\n🟢 [SYSTEM READY] Blue Box Vending v8 - Continuous Mode")

    try:
        while True:
            sio.sleep(0.1) # Mantiene vivo el socket client

            # Si estamos esperando confirmación del servidor, no leemos tarjetas nuevas
            if waiting_for_server_unlock:
                continue

            uid = read_nfc_non_blocking()
            
            if uid:
                # 1. RESET DEL RELOJ DE INACTIVIDAD
                last_activity_time = time.time()
                
                print(f"\n💳 Tarjeta Detectada: {uid}")
                
                # 2. INICIAR CAMARA (Si no estaba ya corriendo)
                # El delay aquí será 0s si ya estaba activa por el alumno anterior
                start_ffmpeg() 

                # 3. CONSULTA AL SERVIDOR
                try:
                    payload = {"nfcUid": uid, "machineId": VENDING_ID}
                    res = requests.post(ID_ENDPOINT, json=payload, timeout=5)
                    
                    if res.status_code == 200:
                        body = res.json()
                        if body.get('action') == "START_STREAM_ONLY":
                            print("📹 [ACCESO] Solicitando verificación visual...")
                            waiting_for_server_unlock = True
                            
                            # Timeout local de seguridad (por si se cae el internet en medio)
                            start_wait = time.time()
                            while waiting_for_server_unlock:
                                sio.sleep(0.2)
                                if time.time() - start_wait > 15: # 15s max espera
                                    print("⚠️ [TIMEOUT] Server no respondió a tiempo.")
                                    waiting_for_server_unlock = False
                                    break
                    else:
                        print(f"⛔ DENEGADO: {res.text}")
                        
                except Exception as e:
                    print(f"🔥 Error Red: {e}")
                
                # Evitar lecturas múltiples de la misma tarjeta seguidas
                sio.sleep(2) 

    except KeyboardInterrupt:
        print("\n👋 Apagando sistema...")
        set_lock('close')
        stop_ffmpeg()
        if NFC_REAL_MODE:
            GPIO.cleanup()
        sio.disconnect()
        sys.exit(0)

if __name__ == "__main__":
    main()