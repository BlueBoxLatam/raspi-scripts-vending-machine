import requests
import time
import subprocess
import os
import socketio
import signal
import sys
import threading

# --- HARDWARE IMPORTS ---
try:
    import RPi.GPIO as GPIO
    HARDWARE_MODE = True
except ImportError:
    print("⚠️ [MODO SIMULACIÓN] GPIO no detectado.")
    HARDWARE_MODE = False

# ================= CONFIGURACIÓN BLUE BOX =================
VM_IP = "api.grabbie.one"
API_URL = f"https://{VM_IP}"

# Configuración WebRTC (SRT -> MediaMTX)
# Apuntamos puerto 8890 publish:cam
SRT_URL = f"srt://{VM_IP}:8890?mode=caller&streamid=publish:cam"

VENDING_ID = "vm_001" 
LOCK_PIN = 17           # Pin GPIO del Cerrojo
TIMEOUT_SECONDS = 180   # 3 Minutos timeout

# Estado Global
sio = socketio.Client()
is_streaming = False
stream_process = None
last_activity_time = time.time()

# ================= HARDWARE SETUP =================

def init_hardware():
    if not HARDWARE_MODE: return
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LOCK_PIN, GPIO.OUT)
        GPIO.output(LOCK_PIN, GPIO.LOW) # Asegurar puerta cerrada
        print(f"✅ [GPIO] Pin {LOCK_PIN} configurado para cerrojo.")
    except Exception as e:
        print(f"❌ [GPIO] Error Hardware: {e}")

def set_lock(state):
    timestamp = time.strftime("%H:%M:%S")
    if state == 'open':
        print(f"\n🔓 [{timestamp}] >>> CERRADURA ABIERTA <<< (Acceso Permitido)")
        if HARDWARE_MODE: GPIO.output(LOCK_PIN, GPIO.HIGH)
    else:
        print(f"🔒 [{timestamp}] Cerradura Bloqueada.")
        if HARDWARE_MODE: GPIO.output(LOCK_PIN, GPIO.LOW)

# ================= VIDEO STREAMING =================

def start_ffmpeg():
    global stream_process, is_streaming
    
    if is_streaming and stream_process:
        if stream_process.poll() is None:
            return # Ya corre
    
    print(f"🎥 [FFMPEG] Iniciando stream WebRTC (SRT)...")
    
    cmd = [
        'ffmpeg', 
        '-f', 'v4l2', '-framerate', '24', '-video_size', '640x480', '-i', '/dev/video0', 
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',      
        '-g', '15', '-b:v', '600k', '-bufsize', '600k',          
        '-f', 'mpegts', SRT_URL
    ]
    
    stream_process = subprocess.Popen(cmd, preexec_fn=os.setsid)
    is_streaming = True
    
    if sio.connected:
        sio.emit('stream_status_change', {'machineId': VENDING_ID, 'status': 'online'})

def stop_ffmpeg():
    global stream_process, is_streaming
    if stream_process:
        print("🛑 [FFMPEG] Deteniendo transmisión...")
        try:
            os.killpg(os.getpgid(stream_process.pid), signal.SIGTERM)
            stream_process.wait()
        except Exception: pass
        
        stream_process = None
        is_streaming = False
        
        if sio.connected:
            sio.emit('stream_status_change', {'machineId': VENDING_ID, 'status': 'offline'})

# ================= WATCHDOG =================
def watchdog_loop():
    global is_streaming
    while True:
        time.sleep(5)
        if is_streaming:
            idle_time = time.time() - last_activity_time
            if idle_time > TIMEOUT_SECONDS:
                print(f"💤 [AUTO-SLEEP] Inactividad detectada. Apagando cámara.")
                stop_ffmpeg()

# ================= SOCKET EVENTS =================

@sio.event
def connect():
    print("⚡ [SOCKET] Conectado a Blue Box Server.")
    sio.emit('join_machine', {'id': VENDING_ID})
    if is_streaming:
        sio.emit('stream_status_change', {'machineId': VENDING_ID, 'status': 'online'})

@sio.event
def disconnect():
    print("❌ [SOCKET] Desconectado.")

# EVENTO CRITICO: Servidor manda abrir puerta tras validar QR en la nube
@sio.on('server_remote_unlock')
def on_remote_unlock(data):
    global last_activity_time
    
    machine_id = data.get('machineId')
    authorized = data.get('authorized')

    if machine_id == VENDING_ID and authorized:
        print(f"✅ [COMANDO] Apertura Remota Recibida.")
        
        # 1. Resetear Watchdog
        last_activity_time = time.time()
        
        # 2. Abrir Puerta
        set_lock('open')
        
        # 3. Prender Cámara (para que el operador vea)
        start_ffmpeg()

@sio.on('force_remote_close')
def on_close(data):
    if data.get('machineId') == VENDING_ID:
        print("\n🏁 [FIN] Cerrando puerta.")
        set_lock('close')
        # No matamos ffmpeg inmediatamente, dejamos watchdog

# ================= MAIN =================

def main():
    init_hardware()
    
    # Watchdog en background
    wd_thread = threading.Thread(target=watchdog_loop, daemon=True)
    wd_thread.start()

    # Loop de conexión resiliente
    while True:
        try:
            if not sio.connected:
                sio.connect(API_URL)
            sio.wait() # Bloquea aquí escuchando eventos
        except Exception as e:
            print(f"⚠️ Error Socket: {e}. Reintentando en 5s...")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Salida manual.")
        set_lock('close')
        stop_ffmpeg()
        if HARDWARE_MODE: GPIO.cleanup()
        sys.exit(0)
