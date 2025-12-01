import requests
import time
import subprocess
import os
import socketio
import signal
import sys

# Importaciones condicionales para hardware
try:
    import board
    import busio
    from digitalio import DigitalInOut
    from adafruit_pn532.i2c import PN532_I2C
    NFC_REAL_MODE = True
except ImportError:
    print("⚠️ [MODO SIMULACIÓN] Hardware NFC no detectado.")
    NFC_REAL_MODE = False

# ================= CONFIGURACIÓN =================
VM_IP = "34.55.59.16" # Tu IP Externa
API_URL = f"http://{VM_IP}:3000"
ID_ENDPOINT = f"{API_URL}/api/identify-student"
SRT_URL = f"srt://{VM_IP}:9000?mode=caller&latency=2000000"

VENDING_ID = "vm_001" 
LOCK_PIN = 17 

# Estado Global
sio = socketio.Client()
is_streaming = False
stream_process = None
pn532 = None
waiting_for_door_open = False # Nuevo estado intermedio

# ================= HARDWARE & UTILS =================

def init_nfc():
    global pn532
    if not NFC_REAL_MODE: return
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        pn532.SAM_configuration()
        print("✅ [NFC] Hardware listo.")
    except Exception as e:
        print(f"❌ [NFC] Error: {e}")

def read_nfc():
    """Lectura no bloqueante para no congelar socketio"""
    if not NFC_REAL_MODE:
        # Simulación: Retorna un ID cada 10 segundos aprox si se descomenta
        # return "53:CD:F5:58:A2:00:01" 
        return None 
    
    try:
        # Timeout muy bajo para liberar el hilo rápido
        uid_bytes = pn532.read_passive_target(timeout=0.2) 
        if uid_bytes:
            return ':'.join(hex(b)[2:].zfill(2).upper() for b in uid_bytes)
    except:
        pass
    return None

def set_lock(state):
    # Aquí iría tu código GPIO
    if state == 'open':
        print(f"🔓 [CERRADURA] >>> ABIERTA <<<")
    else:
        print("🔒 [CERRADURA] CERRADA.")

# ================= GESTIÓN DE VIDEO (FFMPEG) =================

def start_ffmpeg():
    global stream_process
    if stream_process: return # Ya está corriendo
    
    print(f"🎥 [FFMPEG] Iniciando transmisión hacia {VM_IP}...")
    cmd = [
        'ffmpeg', '-f', 'v4l2', '-framerate', '15', '-video_size', '1280x720',
        '-i', '/dev/video0', 
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p', '-vcodec', 'libx264', 
        '-preset', 'ultrafast', '-tune', 'zerolatency', '-b:v', '600k',
        '-f', 'mpegts', SRT_URL
    ]
    # Usamos preexec_fn=os.setsid para poder matar todo el grupo de procesos luego
    stream_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)

def stop_ffmpeg():
    global stream_process
    if stream_process:
        print("🛑 [FFMPEG] Deteniendo transmisión...")
        try:
            os.killpg(os.getpgid(stream_process.pid), signal.SIGTERM)
            stream_process.wait()
        except:
            pass
        stream_process = None

# ================= SOCKET.IO EVENTOS =================

@sio.event
def connect():
    print("⚡ [SOCKET] Conectado al servidor.")
    sio.emit('join_operator') # Solo por si acaso, aunque aquí somos "máquina"

@sio.event
def disconnect():
    print("❌ [SOCKET] Desconectado.")

# ESTE ES EL EVENTO CLAVE QUE ESPERAMOS DEL SERVIDOR
@sio.on('server_verified_video')
def on_server_auth(data):
    global waiting_for_door_open
    if data.get('machineId') == VENDING_ID and data.get('authorized'):
        print("\n✅ [SERVIDOR] Video Verificado. Autorización recibida.")
        set_lock('open')
        waiting_for_door_open = False # Salimos del estado de espera

@sio.on('force_remote_close')
def on_close(data):
    global is_streaming
    if data.get('machineId') == VENDING_ID:
        print("\n🏁 [SERVIDOR] Fin de transacción.")
        set_lock('close')
        stop_ffmpeg()
        is_streaming = False
        print("💤 Sistema listo para siguiente cliente (Enfriando 3s)...")
        time.sleep(3) # Pausa de seguridad

# ================= MANEJO DE SEÑALES (CTRL+C) =================
def signal_handler(sig, frame):
    print("\n👋 [SISTEMA] Apagando forzosamente...")
    set_lock('close')
    stop_ffmpeg()
    try:
        sio.disconnect()
    except:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ================= BUCLE PRINCIPAL =================
def main():
    global is_streaming, waiting_for_door_open

    init_nfc()
    
    # Conexión inicial robusta
    while not sio.connected:
        try:
            sio.connect(API_URL)
        except:
            print("Reconectando...")
            time.sleep(2)

    print("🟢 [SISTEMA] Escuchando tarjetas...")

    while True:
        try:
            # 1. Mantenemos la conexión viva y procesamos eventos
            sio.sleep(0.1) 

            # 2. Si ya estamos en una sesión, no leemos tarjetas
            if is_streaming:
                continue

            # 3. Leer Tarjeta
            uid = read_nfc()
            
            if uid:
                print(f"\n💳 Tarjeta: {uid}. Verificando...")
                
                try:
                    # A. Petición HTTP síncrona (rápida)
                    res = requests.post(ID_ENDPOINT, json={"nfcUid": uid, "machineId": VENDING_ID}, timeout=5)
                    
                    if res.status_code == 200:
                        body = res.json()
                        
                        if body.get('action') == "START_STREAM_ONLY":
                            print("📹 [API] Usuario OK. Iniciando video para verificación...")
                            
                            # B. Iniciar Video
                            start_ffmpeg()
                            is_streaming = True
                            waiting_for_door_open = True
                            
                            # C. Bucle de Espera NO BLOQUEANTE
                            # Esperamos a que llegue el evento 'server_verified_video'
                            print("⏳ Esperando confirmación de video del servidor...")
                            
                            start_wait = time.time()
                            while waiting_for_door_open:
                                sio.sleep(0.5) # CRUCIAL: Permite recibir el evento del socket
                                
                                # Timeout de seguridad (30s)
                                if time.time() - start_wait > 30:
                                    print("⚠️ [TIMEOUT] El servidor no confirmó el video.")
                                    stop_ffmpeg()
                                    is_streaming = False
                                    break
                            
                        else:
                            print(f"⚠️ Respuesta inesperada: {body}")
                    
                    else:
                        print(f"⛔ Denegado: {res.text}")
                        # Pequeño beep de error o luz roja aquí
                        
                except Exception as e:
                    print(f"🔥 Error Red: {e}")
                
                # Pausa para no leer la misma tarjeta dos veces seguidas inmediatamente
                sio.sleep(2)

        except Exception as e:
            print(f"Error Main Loop: {e}")
            sio.sleep(1)

if __name__ == "__main__":
    main()