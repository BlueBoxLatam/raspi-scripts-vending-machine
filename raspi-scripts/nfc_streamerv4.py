import requests
import time
import subprocess
import os
import socketio
import threading
import signal

# Importaciones PN532
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
API_URL = f"http://{VM_EXTERNAL_IP}:3000"
CLOUD_FUNCTION_ENDPOINT = f"{API_URL}/api/identify-student"
SRT_PORT = 9000
SRT_URL = f"srt://{VM_EXTERNAL_IP}:{SRT_PORT}?mode=caller&latency=2000000"

VENDING_MACHINE_ID = "vm_001" 
LOCK_PIN = 17 

# Variables de Control
session_active = False
stop_event = threading.Event()
pn532 = None
# =================================================

sio = socketio.Client()

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
    else:
        print("🔒 [CERRADURA] CERRADA.")

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
            print("🎥 [VIDEO] FFmpeg detenido correctamente.")
        except Exception as e:
            print(f"⚠️ Error deteniendo video: {e}")

# --- SOCKET IO EVENTOS ---
@sio.event
def connect():
    print("⚡ [SOCKET] Conectado.")

@sio.event
def disconnect():
    print("🔌 [SOCKET] Desconectado.")

@sio.on('force_remote_close')
def on_remote_close(data):
    global session_active
    if data.get('machineId') == VENDING_MACHINE_ID:
        print("\n🛑 [ORDEN DE CIERRE RECIBIDA]")
        session_active = False 
        stop_event.set()

# --- MAIN LOOP ---
def main_loop():
    global session_active
    
    # 1. Iniciar Hardware
    init_nfc()
    
    # 2. Conectar Socket (Con reintentos)
    while True:
        try:
            sio.connect(API_URL)
            break
        except Exception:
            print("Retrying connection...")
            time.sleep(2)

    control_solenoid_lock('close') 

    while True:
        # Bucle de espera de tarjeta
        # Usamos sio.sleep para que el socket siga respondiendo heartbeats
        if not session_active:
            uid = read_nfc_safe()
            
            if uid:
                # --- NUEVA SESIÓN ---
                print(f"\n💳 Tarjeta detectada: {uid}")
                
                try:
                    payload = { "nfcUid": uid, "machineId": VENDING_MACHINE_ID }
                    res = requests.post(CLOUD_FUNCTION_ENDPOINT, json=payload, timeout=5)
                    
                    if res.status_code == 200:
                        data = res.json()
                        print(f"✅ Autorizado: {data.get('studentName')}")
                        
                        session_active = True
                        stop_event.clear()
                        
                        # Acciones
                        stream_proc = start_video_stream()
                        control_solenoid_lock('open')
                        
                        # --- BUCLE DE ESPERA DE CIERRE ---
                        print("⏳ Esperando al operador...")
                        while session_active:
                            # Importante: sio.sleep permite que lleguen los eventos del socket
                            sio.sleep(0.5) 
                        
                        # --- LIMPIEZA ---
                        control_solenoid_lock('close')
                        stop_video_stream(stream_proc)
                        
                        # Pausa de seguridad para que el sistema respire
                        print("💤 Enfriando sistema (2s)...")
                        sio.sleep(2)
                        
                    else:
                        print(f"⛔ Denegado: {res.text}")
                        sio.sleep(2)
                        
                except Exception as e:
                    print(f"🔥 Error API: {e}")
                    sio.sleep(1)

            else:
                sio.sleep(0.1) # Pequeña pausa para no quemar CPU buscando tarjeta

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nApagando...")
        control_solenoid_lock('close')
        sio.disconnect()