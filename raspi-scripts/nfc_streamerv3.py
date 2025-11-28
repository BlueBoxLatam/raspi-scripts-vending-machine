import requests
import time
import subprocess
import os
import socketio # 🆕 Librería para escuchar al servidor
import threading

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
API_URL = f"http://{VM_EXTERNAL_IP}:3000" # Base URL
CLOUD_FUNCTION_ENDPOINT = f"{API_URL}/api/identify-student"
SRT_PORT = 9000
SRT_URL = f"srt://{VM_EXTERNAL_IP}:{SRT_PORT}?mode=caller&latency=2000000"

VENDING_MACHINE_ID = "vm_001" 
LOCK_PIN = 17 

# Variables de Control de Estado
session_active = False
stop_event = threading.Event() # Bandera para detener el streaming
# =================================================

# Inicializar SocketIO
sio = socketio.Client()

# --- HARDWARE SETUP ---
PN532_READER = None
if NFC_REAL_MODE:
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        PN532_READER = PN532_I2C(i2c, debug=False)
        PN532_READER.SAM_configuration()
        print("[NFC] PN532 Listo.")
    except Exception as e:
        print(f"[ERROR NFC] {e}")
        NFC_REAL_MODE = False

# --- FUNCIONES ---
def format_uid(uid_bytes):
    uid_hex = uid_bytes.hex().upper() 
    return ':'.join(uid_hex[i:i+2] for i in range(0, len(uid_hex), 2))

def read_nfc_card_uid():
    print("\n[NFC] Esperando tarjeta...")
    if NFC_REAL_MODE and PN532_READER:
        while True:
            # Si hay sesión activa, NO leemos tarjetas, solo esperamos
            if session_active: 
                time.sleep(1)
                continue
                
            uid_bytes = PN532_READER.read_passive_target(timeout=0.5)
            if uid_bytes is not None:
                return format_uid(uid_bytes)
            time.sleep(0.1)
    else:
        # Simulación
        while session_active: time.sleep(1)
        time.sleep(2)
        return "53:CD:F5:58:A2:00:01"

def control_solenoid_lock(state):
    if state == 'open':
        print(f"🔓 [CERRADURA] >>> ABIERTA (Esperando cobro del operador) <<<")
    else:
        print("🔒 [CERRADURA] CERRADA.")

def start_video_stream():
    print(f"🎥 [VIDEO] Stream indefinido iniciado...")
    # Quitamos el flag -t para que sea infinito
    ffmpeg_command = [
        'ffmpeg', '-f', 'v4l2', '-framerate', '10', '-video_size', '1280x720',
        '-i', '/dev/video0', 
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p', '-vcodec', 'libx264', '-preset', 'veryfast',
        '-tune', 'zerolatency', '-b:v', '1000k', '-bufsize', '2000k',
        '-f', 'mpegts', SRT_URL
    ]
    try:
        # Ejecutamos sin esperar (Non-blocking)
        return subprocess.Popen(ffmpeg_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[ERROR VIDEO] {e}")
        return None

# --- SOCKET IO EVENTOS ---
@sio.event
def connect():
    print("⚡ [SOCKET] Conectado al Servidor de Control.")

@sio.event
def disconnect():
    print("🔌 [SOCKET] Desconectado.")

@sio.on('force_remote_close')
def on_remote_close(data):
    global session_active
    # Verificamos si el mensaje es para ESTA máquina
    if data.get('machineId') == VENDING_MACHINE_ID:
        print("\n🛑 [COMANDO RECIBIDO] El Operador finalizó la compra.")
        session_active = False # Esto romperá el bucle principal
        stop_event.set() # Despierta al hilo principal si está dormido

# --- MAIN LOOP ---
def main_loop():
    global session_active
    
    # Conectamos SocketIO
    try:
        sio.connect(API_URL)
    except Exception as e:
        print(f"⚠️ No se pudo conectar a SocketIO: {e}")

    control_solenoid_lock('close') 

    while True:
        # 1. Esperar Tarjeta
        student_uid = read_nfc_card_uid()
        if not student_uid: continue 
        
        # 2. Validar con API
        try:
            payload = { "nfcUid": student_uid, "machineId": VENDING_MACHINE_ID }
            res = requests.post(CLOUD_FUNCTION_ENDPOINT, json=payload, timeout=5)
            
            if res.status_code == 200:
                data = res.json()
                print(f"✅ [ACCESO] {data.get('studentName')} - Saldo: {data.get('balance')}")
                
                # INICIO DE SESIÓN
                session_active = True
                stop_event.clear()
                
                # A. Abrir Video (Infinito)
                stream_proc = start_video_stream()
                
                # B. Abrir Puerta
                control_solenoid_lock('open')
                
                # C. ESPERAR HASTA QUE EL OPERADOR COBRE
                # Este bucle mantiene la puerta abierta mientras session_active sea True
                print("⏳ Esperando orden de cierre del servidor...")
                while session_active:
                    # Esperamos eventos del socket (ver on_remote_close arriba)
                    # Usamos wait con timeout para no bloquear el CPU
                    stop_event.wait(timeout=1.0)
                
                # D. CIERRE DE SESIÓN (Al salir del while)
                print("🏁 Finalizando sesión...")
                control_solenoid_lock('close')
                
                if stream_proc:
                    stream_proc.terminate()
                    stream_proc.wait()
                    print("🎥 Video detenido.")
                
            else:
                print(f"⛔ Acceso Denegado: {res.text}")

        except Exception as e:
            print(f"🔥 Error Crítico: {e}")
            # Seguridad: cerrar puerta si falla algo
            control_solenoid_lock('close')
            session_active = False

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nApagando sistema...")
        control_solenoid_lock('close')
        sio.disconnect()