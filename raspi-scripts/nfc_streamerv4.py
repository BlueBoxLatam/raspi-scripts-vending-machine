import requests
import time
import subprocess
import os
import socketio  # Librería para escuchar al servidor
import threading
import signal

# --- Importaciones PN532 ---
try:
    import board
    import busio
    from digitalio import DigitalInOut
    from adafruit_pn532.i2c import PN532_I2C
    NFC_REAL_MODE = True
except ImportError:
    print("[ADVERTENCIA] Librerías PN532 no encontradas. Modo Simulación.")
    NFC_REAL_MODE = False

# ================= CONFIGURACIÓN =================
VM_EXTERNAL_IP = "34.55.59.16"
API_URL = f"http://{VM_EXTERNAL_IP}:3000" # Base URL
CLOUD_FUNCTION_ENDPOINT = f"{API_URL}/api/identify-student"
SRT_PORT = 9000
SRT_URL = f"srt://{VM_EXTERNAL_IP}:{SRT_PORT}?mode=caller&latency=2000000"

VENDING_MACHINE_ID = "vm_001"
LOCK_PIN = 17

# --- Variables de Control de Estado ---
session_active = False
stop_event = threading.Event()
pn532 = None

# --- Inicializar SocketIO ---
sio = socketio.Client()

# ================= FUNCIONES HARDWARE =================

def init_nfc():
    """Inicializa el lector NFC de forma segura"""
    global pn532
    if not NFC_REAL_MODE: return True
    
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        pn532.SAM_configuration()
        print("[NFC] Hardware inicializado.")
        return True
    except Exception as e:
        print(f"[ERROR NFC] No se pudo iniciar: {e}")
        return False

def read_nfc_safe():
    """Lectura no bloqueante"""
    global pn532
    if not NFC_REAL_MODE:
        sio.sleep(2)
        # Retorno simulado para pruebas
        return "53:CD:F5:58:A2:00:01" 
    
    try:
        # Intentamos leer sin bloquear mucho tiempo
        uid_bytes = pn532.read_passive_target(timeout=0.5)
        if uid_bytes:
            return ':'.join(hex(b)[2:].zfill(2).upper() for b in uid_bytes)
    except OSError:
        print("⚠️ Error I2C en NFC. Reiniciando hardware...")
        init_nfc()
    except Exception as e:
        print(f"⚠️ Error lectura: {e}")
    
    return None

def control_solenoid_lock(state):
    if state == 'open':
        print(f"🔓 [CERRADURA] >>> ABIERTA <<<")
        # Aquí iría el código GPIO real, ej: GPIO.output(LOCK_PIN, GPIO.HIGH)
    else:
        print("🔒 [CERRADURA] CERRADA.")
        # Aquí iría el código GPIO real, ej: GPIO.output(LOCK_PIN, GPIO.LOW)

# ================= FUNCIONES VIDEO =================

def start_video_stream():
    print(f"🎥 [VIDEO] Iniciando FFmpeg...")
    ffmpeg_command = [
        'ffmpeg', '-f', 'v4l2', '-framerate', '10', '-video_size', '1280x720',
        '-i', '/dev/video0',
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p', '-vcodec', 'libx264', '-preset', 'ultrafast', # Ultrafast para menos CPU
        '-tune', 'zerolatency', '-b:v', '800k', # Bitrate un poco más bajo para estabilidad
        '-f', 'mpegts', SRT_URL
    ]
    # Usamos preexec_fn para poder matar el proceso limpiamente después
    return subprocess.Popen(ffmpeg_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)

def stop_video_stream(process):
    if process:
        try:
            # Matamos todo el grupo de procesos de FFmpeg
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait()
            print("👾 [VIDEO] FFmpeg detenido correctamente.")
        except Exception as e:
            print(f"⚠️ Error deteniendo video: {e}")

# ================= SOCKET IO EVENTOS =================

@sio.event
def connect():
    print("⚡ [SOCKET] Conectado.")

@sio.event
def disconnect():
    print("🔌 [SOCKET] Desconectado.")

@sio.event
def on_remote_close(data):
    global session_active
    if data.get('machineId') == VENDING_MACHINE_ID:
        print("\n🛑 [ORDEN DE CIERRE RECIBIDA]")
        session_active = False
        stop_event.set()

@sio.event
def on_open_door(data):
    """Nueva función para abrir la puerta remotamente."""
    if data.get('machineId') == VENDING_MACHINE_ID and session_active:
        print("✅ [HANDSHAKE] Video verificado. Abriendo puerta.")
        control_solenoid_lock('open')

# ================= LOOP PRINCIPAL =================

def main_loop():
    global session_active
    
    # 1. Iniciar Hardware
    init_nfc()
    
    # 2. Conectar Socket (Con reintentos)
    while not stop_event.is_set():
        try:
            sio.connect(API_URL)
            break
        except Exception as e:
            print(f"Retrying connection... ({e})")
            time.sleep(2)
            
    control_solenoid_lock('close')
    
    while not stop_event.is_set():
        if not session_active:
            uid = read_nfc_safe()
            
            if uid:
                print(f"\n💳 Tarjeta detectada: {uid}")
                
                try:
                    payload = { "nfcUid": uid, "machineId": VENDING_MACHINE_ID }
                    res = requests.post(CLOUD_FUNCTION_ENDPOINT, json=payload, timeout=8)
                    
                    if res.status_code == 200:
                        data = res.json()
                        
                        # --- NUEVO FLUJO DE HANDSHAKE ---
                        if data.get('status') == 'PENDING_VIDEO':
                            sessionId = data.get('sessionId')
                            print(f"⏳ [HANDSHAKE] Estado pendiente. ID de Sesión: {sessionId}")
                            
                            session_active = True
                            
                            # 1. Avisar al servidor que estamos listos
                            sio.emit('rpi_ready', {'sessionId': sessionId})
                            
                            # 2. Iniciar SOLO el video
                            stream_proc = start_video_stream()
                            
                            # 3. NO ABRIR LA PUERTA AÚN
                            print("⏳ Esperando verificación de video del servidor...")
                            
                            # Bucle de espera de cierre (o hasta que se complete el handshake)
                            while session_active:
                                sio.sleep(0.5)
                            
                            # --- LIMPIEZA POST-SESIÓN ---
                            control_solenoid_lock('close')
                            stop_video_stream(stream_proc)
                            
                            print("💤 Enfriando sistema (2s)...")
                            sio.sleep(2)
                        else:
                            print(f"🤔 Respuesta inesperada del servidor: {data}")
                            sio.sleep(2)
                    else:
                        print(f"⛔ Denegado: {res.text}")
                        sio.sleep(2)
                        
                except Exception as e:
                    print(f"🔥 Error API: {e}")
                    sio.sleep(1)
        else:
            sio.sleep(0.1)

# ================= EJECUCIÓN =================

if __name__ == "__main__":
    def signal_handler(sig, frame):
        print("\nApagando sistema...")
        stop_event.set()
        if sio.connected:
            sio.disconnect()
        control_solenoid_lock('close')
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        main_loop()
    except Exception as e:
        print(f"Error fatal en main loop: {e}")
    finally:
        print("Saliendo.")
        if sio.connected:
            sio.disconnect()
        # Nota: Aquí no tenemos acceso directo a 'stream_proc' si falló fuera del scope,
        # pero el signal_handler y la limpieza interna deberían cubrir la mayoría de casos.