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
    from digitalio import DigitalInOut, Direction, Pull
    from adafruit_pn532.i2c import PN532_I2C # ¡IMPORTANTE! Re-agregado para el lector NFC
    NFC_REAL_MODE = True
except ImportError:
    print("⚠️ [MODO SIMULACIÓN] Hardware no detectado. GPIOs y NFC desactivados.")
    NFC_REAL_MODE = False

# ================= CONFIGURACIÓN =================
VM_IP = "34.55.59.16" # Tu IP Externa
API_URL = f"http://{VM_IP}:3000"
ID_ENDPOINT = f"{API_URL}/api/identify-student"
SRT_URL = f"srt://{VM_IP}:9000?mode=caller&latency=2000000"

VENDING_ID = "vm_001" 

# --- PINES GPIO ---
# Conecta el pin de señal (IN) de tu módulo de relé al pin GPIO 17.
# El relé controlará la cerradura electrónica.
RELAY_PIN = board.D17 if NFC_REAL_MODE else None

# Conecta el sensor final de carrera (COM y NC) al pin GPIO 27 y a GND.
# Este sensor detecta si la puerta está abierta o cerrada.
DOOR_SENSOR_PIN = board.D27 if NFC_REAL_MODE else None


# Estado Global
sio = socketio.Client()
is_streaming = False
stream_process = None
pn532 = None
relay = None
door_sensor = None
last_door_state = None # Para no imprimir mensajes repetidamente
waiting_for_door_open = False 

# ================= HARDWARE & UTILS =================

def init_hardware():
    """Inicializa el lector NFC, el relé y el sensor de puerta."""
    global pn532, relay, door_sensor
    if not NFC_REAL_MODE: return

    # 1. Inicializar NFC
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pn532 = PN532_I2C(i2c, debug=False)
        pn532.SAM_configuration()
        print("✅ [NFC] Lector NFC listo.")
    except Exception as e:
        print(f"❌ [NFC] Error al iniciar: {e}")

    # 2. Inicializar Relé (Cerradura)
    try:
        relay = DigitalInOut(RELAY_PIN)
        relay.direction = Direction.OUTPUT
        # Estado inicial: Relé activado = Puerta cerrada.
        # Asumimos que el relé se activa con un nivel ALTO (HIGH).
        # Si tu relé se activa con LOW, cambia 'True' a 'False'.
        relay.value = True 
        print("✅ [RELÉ] Módulo de cerradura listo (Cerrado).")
    except Exception as e:
        print(f"❌ [RELÉ] Error al iniciar: {e}")

    # 3. Inicializar Sensor de Puerta (Final de Carrera)
    try:
        door_sensor = DigitalInOut(DOOR_SENSOR_PIN)
        door_sensor.direction = Direction.INPUT
        # Habilitamos una resistencia PULL-UP interna.
        # Cuando la puerta se abra, el circuito se abrirá y el pin leerá HIGH.
        door_sensor.pull = Pull.UP
        print("✅ [SENSOR] Sensor de puerta listo.")
    except Exception as e:
        print(f"❌ [SENSOR] Error al iniciar: {e}")


def read_nfc():
    """Lectura no bloqueante para no congelar socketio"""
    if not pn532: return None 
    
    try:
        uid_bytes = pn532.read_passive_target(timeout=0.2) 
        if uid_bytes:
            return ':'.join(hex(b)[2:].zfill(2).upper() for b in uid_bytes)
    except:
        pass
    return None

def control_door_lock(state):
    """Controla la cerradura electrónica a través del relé."""
    if not relay: 
        print(f"🚪 [SIM] Cerradura en estado: {state.upper()}")
        return

    # Lógica de control:
    # 'open'   -> Relé DESACTIVADO (la cerradura se libera)
    # 'close'  -> Relé ACTIVADO (la cerradura se bloquea)
    if state == 'open':
        relay.value = False # Nivel BAJO para abrir
        print(f"🔓 [CERRADURA] >>> ABIERTA <<<")
    else:
        relay.value = True # Nivel ALTO para cerrar
        print("🔒 [CERRADURA] CERRADA.")

def check_door_status():
    """Verifica el estado de la puerta e imprime si cambia."""
    global last_door_state
    if not door_sensor: return

    # El sensor es Normalmente Cerrado (NC) con PULL_UP.
    # Puerta cerrada -> circuito cerrado -> pin a GND -> Lectura = False
    # Puerta abierta -> circuito abierto -> pin a VCC (por pull-up) -> Lectura = True
    door_is_open = door_sensor.value

    if door_is_open != last_door_state:
        if door_is_open:
            print("🚪 [SENSOR] La puerta ha sido ABIERTA.")
        else:
            print("🚪 [SENSOR] La puerta ha sido CERRADA.")
        last_door_state = door_is_open


# ================= GESTIÓN DE VIDEO (FFMPEG) =================

def start_ffmpeg():
    global stream_process
    if stream_process: return
    
    print(f"🎥 [FFMPEG] Iniciando transmisión hacia {VM_IP}...")
    cmd = [
        'ffmpeg', '-f', 'v4l2', '-framerate', '15', '-video_size', '640x480',
        '-i', '/dev/video0', '-f', 'lavfi', '-i', 'anullsrc',
        '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-preset', 'ultrafast',
        '-tune', 'zerolatency', '-g', '30', '-keyint_min', '30', '-b:v', '400k',
        '-bufsize', '400k', '-f', 'mpegts', SRT_URL
    ]
    stream_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)

def stop_ffmpeg():
    global stream_process
    if stream_process:
        print("🛑 [FFMPEG] Deteniendo transmisión...")
        try:
            os.killpg(os.getpgid(stream_process.pid), signal.SIGTERM)
            stream_process.wait()
        except: pass
        stream_process = None

# ================= SOCKET.IO EVENTOS =================

@sio.event
def connect():
    print("⚡ [SOCKET] Conectado al servidor.")

@sio.event
def disconnect():
    print("❌ [SOCKET] Desconectado.")

@sio.on('server_verified_video')
def on_server_auth(data):
    global waiting_for_door_open, is_streaming
    
    if data.get('machineId') == VENDING_ID and data.get('authorized'):
        if is_streaming and waiting_for_door_open:
            print("\n✅ [SERVIDOR] Video Verificado. Autorización recibida.")
            control_door_lock('open') # <--- ACCIÓN DEL RELÉ
            waiting_for_door_open = False
        else:
            print("\n⚠️ [SERVIDOR] Autorización recibida tarde. Ignorando.")

@sio.on('force_remote_close')
def on_close(data):
    global is_streaming
    if data.get('machineId') == VENDING_ID:
        print("\n🏁 [SERVIDOR] Fin de transacción.")
        control_door_lock('close') # <--- ACCIÓN DEL RELÉ
        stop_ffmpeg()
        is_streaming = False
        print("💤 Sistema listo para siguiente cliente (Enfriando 3s)...")
        time.sleep(3)

# ================= MANEJO DE SEÑALES (CTRL+C) =================
def signal_handler(sig, frame):
    print("\n👋 [SISTEMA] Apagando forzosamente...")
    control_door_lock('close')
    stop_ffmpeg()
    try:
        sio.disconnect()
    except: pass
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ================= BUCLE PRINCIPAL =================
def main():
    global is_streaming, waiting_for_door_open

    init_hardware() # Inicializa todo el hardware
    
    while not sio.connected:
        try:
            sio.connect(API_URL)
        except:
            print("Reconectando...")
            time.sleep(2)

    print("🟢 [SISTEMA] Escuchando tarjetas...")

    while True:
        try:
            sio.sleep(0.1) 
            check_door_status() # <--- LECTURA CONSTANTE DEL SENSOR

            if is_streaming:
                continue

            uid = read_nfc()
            
            if uid:
                print(f"\n💳 Tarjeta: {uid}. Verificando...")
                
                try:
                    res = requests.post(ID_ENDPOINT, json={"nfcUid": uid, "machineId": VENDING_ID}, timeout=5)
                    
                    if res.status_code == 200 and res.json().get('action') == "START_STREAM_ONLY":
                        print("📹 [API] Usuario OK. Iniciando video para verificación...")
                        
                        start_ffmpeg()
                        is_streaming = True
                        waiting_for_door_open = True
                        
                        print("⏳ Esperando confirmación de video del servidor...")
                        
                        start_wait = time.time()
                        while waiting_for_door_open:
                            sio.sleep(0.5) 
                            
                            if time.time() - start_wait > 60:
                                print("⚠️ [TIMEOUT] El servidor no confirmó el video.")
                                stop_ffmpeg()
                                is_streaming = False
                                break
                    else:
                        print(f"⛔ Denegado: {res.text}")
                        
                except Exception as e:
                    print(f"🔥 Error Red: {e}")
                
                sio.sleep(2)

        except Exception as e:
            print(f"Error Main Loop: {e}")
            sio.sleep(1)

if __name__ == "__main__":
    main()
