import requests
import time
import subprocess
import os

# Importaciones específicas para el PN532
try:
    import board
    import busio
    from digitalio import DigitalInOut
    from adafruit_pn532.i2c import PN532_I2C
    NFC_REAL_MODE = True
except ImportError:
    print("[ADVERTENCIA] Librerías PN532 no encontradas. Ejecutando en modo de simulación NFC.")
    NFC_REAL_MODE = False


# =================================================================
#                         CONFIGURACIÓN GLOBAL
# =================================================================

# 1. NUEVA IP EXTERNA DE LA VM (Actualizada)
VM_EXTERNAL_IP = "34.55.59.16"

# 2. Endpoint de TU servidor Node.js (Puerto 3000)
# Ya no usamos la Cloud Function, ahora vamos directo a tu VM
CLOUD_FUNCTION_ENDPOINT = f"http://{VM_EXTERNAL_IP}:3000/api/identify-student"

# 3. Configuración de Video SRT (Puerto 9000)
# Agregamos latency=2000000 (2 seg) para evitar cortes y pixelado
SRT_PORT = 9000
SRT_URL = f"srt://{VM_EXTERNAL_IP}:{SRT_PORT}?mode=caller&latency=2000000"

# 4. Duración de la transacción (Aumentada para compensar el búfer)
DURATION_SECONDS = 40

# ID único de esta heladera
VENDING_MACHINE_ID = "vm-school-001" 

# PIN de control para la cerradura (ejemplo: BCM pin 17)
LOCK_PIN = 17 
# =================================================================

# Inicialización del PN532
PN532_READER = None
if NFC_REAL_MODE:
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        PN532_READER = PN532_I2C(i2c, debug=False)
        PN532_READER.SAM_configuration()
        print("[NFC] PN532 inicializado correctamente (Modo I2C).")
    except Exception as e:
        print(f"[ERROR NFC] Fallo al inicializar PN532: {e}")
        NFC_REAL_MODE = False

def format_uid(uid_bytes):
    """Convierte bytes UID a formato hexadecimal con dos puntos (53:CD:F5...)."""
    uid_hex = uid_bytes.hex().upper() 
    return ':'.join(uid_hex[i:i+2] for i in range(0, len(uid_hex), 2))

def read_nfc_card_uid():
    """Lee UID o simula si no hay hardware."""
    print("\n[NFC] Esperando tarjeta NFC. Acerque su credencial...")

    if NFC_REAL_MODE and PN532_READER:
        while True:
            uid_bytes = PN532_READER.read_passive_target(timeout=0.5)
            if uid_bytes is not None:
                student_uid = format_uid(uid_bytes)
                print(f"[NFC REAL] Tarjeta detectada. UID: {student_uid}")
                return student_uid
            time.sleep(0.1)
    else:
        # Simulación
        time.sleep(3)
        simulated_uid = "53:CD:F5:58:A2:00:01"
        print(f"[NFC SIMULADO] Tarjeta detectada. UID: {simulated_uid}")
        return simulated_uid

def control_solenoid_lock(state):
    """Control de la cerradura (Simulado por ahora para seguridad)."""
    # Aquí descomentarías RPi.GPIO cuando conectes el relé real
    if state == 'open':
        print(f"[CERRADURA] >>> CERRADURA ABIERTA por {DURATION_SECONDS} segundos <<<")
    else:
        print("[CERRADURA] CERRADURA CERRADA.")

def start_video_stream():
    """Inicia streaming SRT optimizado para estabilidad."""
    print(f"\n[VIDEO] Iniciando streaming hacia {SRT_URL}...")
    
    ffmpeg_command = [
        'ffmpeg',
        '-f', 'v4l2',
        '-framerate', '10',          # 10 FPS para estabilidad
        '-video_size', '1280x720',   # Calidad HD
        '-i', '/dev/video0', 
        '-t', str(DURATION_SECONDS),
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p',
        '-vcodec', 'libx264',
        '-preset', 'veryfast',
        '-tune', 'zerolatency',
        '-b:v', '1000k',             # 1 Mbps bitrate
        '-bufsize', '2000k',         # Búfer local
        '-f', 'mpegts', 
        SRT_URL
    ]
    
    try:
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[VIDEO] Stream iniciado. PID: {process.pid}")
        return process
    except FileNotFoundError:
        print("[ERROR] FFmpeg no instalado.")
        return None
    except Exception as e:
        print(f"[ERROR] Falló al iniciar video: {e}")
        return None

def main_loop():
    print("=================================================================")
    print(f"       INICIANDO SISTEMA BLUE BOX - HELADERA {VENDING_MACHINE_ID}")
    print(f"       Conectado a Servidor: {VM_EXTERNAL_IP}:3000")
    print("=================================================================")
    
    control_solenoid_lock('close') 

    while True:
        # 1. Leer Tarjeta
        student_uid = read_nfc_card_uid()
        if not student_uid: continue 
        
        payload = { "nfcUid": student_uid, "machineId": VENDING_MACHINE_ID }
        
        # 2. Consultar al Servidor Node.js
        try:
            print(f"[CLOUD] Consultando autorización a: {CLOUD_FUNCTION_ENDPOINT}")
            response = requests.post(CLOUD_FUNCTION_ENDPOINT, json=payload, timeout=10)

            if response.status_code == 200:
                data = response.json()
                student_name = data.get('studentName', 'Desconocido')
                balance = data.get('balance', 0)
                
                print(f"\n[ACCESO OK] {student_name} (Saldo: {balance})")
                
                # 3. Iniciar Video y Abrir Puerta
                stream_process = start_video_stream()
                control_solenoid_lock('open')
                
                # 4. Esperar tiempo de compra
                time.sleep(DURATION_SECONDS)
                
                # 5. Cerrar todo
                control_solenoid_lock('close')
                if stream_process:
                    stream_process.terminate()
                    time.sleep(1)
                
                print("[TRANSACCION] Finalizada. Esperando siguiente...")

            elif response.status_code == 403:
                print(f"[ACCESO DENEGADO] {response.json().get('error')}")
            elif response.status_code == 404:
                print("[ACCESO DENEGADO] Estudiante no encontrado.")
            else:
                print(f"[ERROR SERVIDOR] Código {response.status_code}: {response.text}")
                
        except requests.exceptions.RequestException as e:
            print(f"[ERROR RED] No se pudo conectar al servidor: {e}")
        except Exception as e:
            print(f"[ERROR] Inesperado: {e}")
            
        time.sleep(3)
        print("-----------------------------------------------------------------")

if __name__ == "__main__":
    if not os.path.exists('/dev/video0'):
        print("[ADVERTENCIA] Cámara no detectada en /dev/video0")
    
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nApagando sistema...")
        control_solenoid_lock('close')