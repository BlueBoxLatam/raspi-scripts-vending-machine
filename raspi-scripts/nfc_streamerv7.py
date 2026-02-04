import requests
import time
import subprocess
import os
import socketio
import signal
import sys
import json

# --- HARDWARE IMPORTS (Safe Mode) ---
try:
    import board
    import busio
    from digitalio import DigitalInOut
    from adafruit_pn532.i2c import PN532_I2C
    NFC_REAL_MODE = True
except ImportError:
    print("⚠️ [MODO SIMULACIÓN] Hardware NFC no detectado. Usando modo virtual.")
    NFC_REAL_MODE = False

# ================= CONFIGURACIÓN BLUE BOX =================
VM_IP = "34.55.59.16" 
API_URL = f"http://{VM_IP}:3000"
ID_ENDPOINT = f"{API_URL}/api/identify-student"

# Configuración de Streaming (Baja Latencia)
# Enviamos via SRT al puerto 9000 del servidor (que debe convertir a HLS)
SRT_URL = f"srt://{VM_IP}:9000?mode=caller&latency=2000000"

VENDING_ID = "vm_001" 
LOCK_PIN = 17 

# Estado Global
sio = socketio.Client()
is_streaming = False
stream_process = None
pn532 = None
waiting_for_server_unlock = False 

# ================= HARDWARE =================

def init_hardware():
    global pn532
    if not NFC_REAL_MODE: return
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        pn532.SAM_configuration()
        print("✅ [NFC] PN532 Inicializado.")
    except Exception as e:
        print(f"❌ [NFC] Error Hardware: {e}")

def read_nfc_non_blocking():
    """Lee NFC sin bloquear el hilo principal (para que SocketIO fluya)"""
    if not NFC_REAL_MODE:
        # Simulación: Retorna un ID fake si presionas Enter (esto requeriría input async, 
        # por ahora retornamos None o hardcodeamos para pruebas si se desea)
        time.sleep(0.1)
        return None 
    
    try:
        # Timeout ultra-corto (0.1s) para dar tiempo al loop principal
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
        # Aquí iría: GPIO.output(LOCK_PIN, GPIO.HIGH)
    else:
        print(f"🔒 [{timestamp}] Cerradura Bloqueada.")
        # Aquí iría: GPIO.output(LOCK_PIN, GPIO.LOW)

# ================= VIDEO STREAMING (FFmpeg) =================

def start_ffmpeg():
    global stream_process
    if stream_process: return 
    
    print(f"🎥 [FFMPEG] Iniciando stream SRT hacia {VM_IP}...")
    
    # Comanda optimizado para Raspberry Pi Camera -> SRT
    # Ajustado para latencia mínima ("zerolatency")
    cmd = [
        'ffmpeg', 
        '-f', 'v4l2', 
        '-framerate', '15',          # FPS bajos para estabilidad
        '-video_size', '640x480',    # 480p es suficiente para el operador
        '-i', '/dev/video0', 
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100', # Audio mudo dummy
        '-pix_fmt', 'yuv420p', 
        '-c:v', 'libx264',           
        '-preset', 'ultrafast',      # Prioridad: Velocidad CPU
        '-tune', 'zerolatency',      # Prioridad: Latencia
        '-g', '30',                  # Keyframe cada 2s (30 frames / 15 fps)
        '-keyint_min', '30',         
        '-b:v', '600k',              # Bitrate controlado
        '-bufsize', '600k',          
        '-f', 'mpegts', 
        SRT_URL
    ]
    
    # "setsid" permite matar todo el árbol de procesos de ffmpeg después
    stream_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)

def stop_ffmpeg():
    global stream_process
    if stream_process:
        print("🛑 [FFMPEG] Deteniendo transmisión...")
        try:
            os.killpg(os.getpgid(stream_process.pid), signal.SIGTERM)
            stream_process.wait()
        except Exception as e:
            print(f"Error matando ffmpeg: {e}")
        stream_process = None

# ================= SOCKET.IO EVENTS =================

@sio.event
def connect():
    print("⚡ [SOCKET] Conectado al Servidor Blue Box.")
    # Nos identificamos como máquina (aunque el servidor no valida esto estrictamente aún)
    sio.emit('join_machine', {'id': VENDING_ID})

@sio.event
def disconnect():
    print("❌ [SOCKET] Desconectado.")

@sio.on('server_verified_video')
def on_server_auth(data):
    """
    HOOK CRÍTICO: Recibimos esto cuando el dashboard ya ve el video.
    Aquí se mide la latencia final del "Handshake".
    """
    global waiting_for_server_unlock, is_streaming
    
    machine_id = data.get('machineId')
    
    if machine_id == VENDING_ID and data.get('authorized'):
        if is_streaming and waiting_for_server_unlock:
            print(f"✅ [CONFIRMADO] El servidor verificó el video.")
            set_lock('open')
            waiting_for_server_unlock = False # Handshake completado
        else:
            print(f"⚠️ Autorización ignorada (No estamos esperando apertura).")

@sio.on('force_remote_close')
def on_close(data):
    global is_streaming, waiting_for_server_unlock
    if data.get('machineId') == VENDING_ID:
        print("\n🏁 [FIN] Transacción completada.")
        set_lock('close')
        stop_ffmpeg()
        is_streaming = False
        waiting_for_server_unlock = False
        print("💤 Enfriando sistema (3s)...")
        time.sleep(3)

# ================= MAIN LOOP =================

def main():
    global is_streaming, waiting_for_server_unlock

    init_hardware()
    
    # Reintento de conexión infinito
    while not sio.connected:
        try:
            sio.connect(API_URL)
        except Exception:
            print(f"Reconectando a {API_URL}...")
            time.sleep(2)

    print("\n🟢 [SYSTEM READY] Esperando tarjetas NFC...")

    try:
        while True:
            # 1. Mantener Socket Vivo
            sio.sleep(0.1) 

            # 2. Si ya estamos ocupados, ignorar lector
            if is_streaming:
                continue

            # 3. Leer NFC
            uid = read_nfc_non_blocking()
            
            if uid:
                print(f"\n💳 Tarjeta Detectada: {uid}")
                
                try:
                    # REQUEST: T0 (Inicio Handshake)
                    # El servidor marcará el timestamp de recepción como T0
                    payload = {"nfcUid": uid, "machineId": VENDING_ID}
                    
                    res = requests.post(ID_ENDPOINT, json=payload, timeout=5)
                    
                    if res.status_code == 200:
                        body = res.json()
                        
                        if body.get('action') == "START_STREAM_ONLY":
                            print("📹 [ACCESO OK] Iniciando Video... Esperando confirmación visual.")
                            
                            # START VIDEO
                            start_ffmpeg()
                            is_streaming = True
                            waiting_for_server_unlock = True
                            
                            # WAIT LOOP (Seguridad)
                            # Esperamos max 60s a que el socket 'server_verified_video' llegue
                            start_wait = time.time()
                            while waiting_for_server_unlock:
                                sio.sleep(0.5) # Permitir recibir eventos
                                if time.time() - start_wait > 60:
                                    print("⚠️ [TIMEOUT] Servidor no confirmó video.")
                                    stop_ffmpeg()
                                    is_streaming = False
                                    waiting_for_server_unlock = False
                                    break
                            
                        else:
                            print(f"⚠️ Respuesta extraña: {body}")
                    
                    else:
                        print(f"⛔ DENEGADO: {res.text}")
                        
                except Exception as e:
                    print(f"🔥 Error de Red: {e}")
                
                # Anti-rebote simple
                sio.sleep(2)

    except KeyboardInterrupt:
        print("\n👋 Apagando...")
        set_lock('close')
        stop_ffmpeg()
        sio.disconnect()
        sys.exit(0)

if __name__ == "__main__":
    main()