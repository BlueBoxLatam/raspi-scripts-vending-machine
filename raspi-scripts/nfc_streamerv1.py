import requests
import time
import subprocess
import os

# Importaciones específicas para el PN532
try:
    # Intenta importar la librería de Adafruit para el PN532
    import board
    import busio
    from digitalio import DigitalInOut
    from adafruit_pn532.i2c import PN532_I2C
    NFC_REAL_MODE = True
except ImportError:
    # Modo de simulación si la librería no está instalada o no es una Raspberry Pi
    print("[ADVERTENCIA] Librerías PN532 no encontradas. Ejecutando en modo de simulación NFC.")
    NFC_REAL_MODE = False


# =================================================================
#                         CONFIGURACIÓN GLOBAL
# =================================================================

# 1. Endpoint de la Cloud Function para validar el estudiante
CLOUD_FUNCTION_ENDPOINT = "https://nfc-identify-student-502601695815.us-central1.run.app"

# 2. IP Externa de la VM de Compute Engine (SERVIDOR DE VIDEO)
# La IP que aparece en tu imagen: 34.55.59.162
VM_EXTERNAL_IP = "34.55.59.162"

# 3. Puerto y Protocolo para el streaming de video (Debe coincidir con la regla de Firewall)
SRT_PORT = 9000
SRT_URL = f"srt://{VM_EXTERNAL_IP}:{SRT_PORT}?mode=caller"

# Duración de la grabación y apertura de cerradura (en segundos)
DURATION_SECONDS = 30
# ID único de esta heladera (se usa en el payload del JSON)
VENDING_MACHINE_ID = "vm-school-001" 

# Configuración de hardware (Ajustar según tu conexión)
# PIN de control para la cerradura (ejemplo: BCM pin 17)
LOCK_PIN = 17 
# =================================================================

# Inicialización del PN532 (si estamos en modo real)
PN532_READER = None
if NFC_REAL_MODE:
    try:
        # Inicialización I2C (ajustar si usas SPI o UART)
        i2c = busio.I2C(board.SCL, board.SDA)
        
        # El PN532 se configura para que NO espere un chip de seguridad SAM.
        PN532_READER = PN532_I2C(i2c, debug=False)
        PN532_READER.SAM_configuration()
        print("[NFC] PN532 inicializado correctamente (Modo I2C).")
    except Exception as e:
        print(f"[ERROR NFC] Fallo al inicializar PN532: {e}")
        NFC_REAL_MODE = False


# Función para limpiar el UID y formatearlo como un string de dígitos
def format_uid(uid_bytes):
    """Convierte un array de bytes UID a un string hexadecimal sin separadores."""
    # Ejemplo de bytes: b'\x53\xcd\xf5\x58\xa2\x00\x01'
    # Resultado esperado: "53cdf558a20001"
    
    # Convierte el array de bytes a una cadena hexadecimal
    uid_hex = uid_bytes.hex().upper() 
    return uid_hex

# Función principal para la lectura NFC
def read_nfc_card_uid():
    """Lee el UID de una tarjeta NFC usando el sensor PN532 o simula si no está disponible."""
    print("\n[NFC] Esperando tarjeta NFC. Acerque su credencial...")

    if NFC_REAL_MODE and PN532_READER:
        # --- Lógica de Lectura Real ---
        # Bucle hasta que se encuentre una tarjeta
        while True:
            # wait_for_tag busca una etiqueta y devuelve el UID como un array de bytes
            uid_bytes = PN532_READER.read_passive_target(timeout=0.5)
            
            if uid_bytes is not None:
                # El UID es un array de bytes (ej. b'\x53\xcd\xf5\x58\xa2\x00\x01')
                student_uid = format_uid(uid_bytes)
                print(f"[NFC REAL] Tarjeta detectada. UID: {student_uid}")
                return student_uid
            
            time.sleep(0.1) # Pausa breve para no sobrecargar el CPU
            
    else:
        # --- Lógica de Simulación (Fallback) ---
        time.sleep(3)  # Simula el tiempo que toma leer la tarjeta
        # UID de prueba basado en el formato que mencionaste (limpio y sin separadores)
        simulated_uid = "53CDF558A20001" 
        print(f"[NFC SIMULADO] Tarjeta detectada. UID: {simulated_uid}")
        return simulated_uid


# Función de simulación para el control de la cerradura (Reemplazar con control GPIO real)
def control_solenoid_lock(state):
    """Simula o controla la cerradura electromagnética (Requiere librería RPi.GPIO)."""
    
    # En la implementación real, usarías RPi.GPIO (si está configurado)
    # try:
    #     import RPi.GPIO as GPIO
    #     GPIO.setmode(GPIO.BCM)
    #     GPIO.setup(LOCK_PIN, GPIO.OUT)
    #     if state == 'open':
    #         GPIO.output(LOCK_PIN, GPIO.HIGH)
    #     else:
    #         GPIO.output(LOCK_PIN, GPIO.LOW)
    # except Exception:
    #     pass # Si GPIO no está disponible, solo imprime
        
    if state == 'open':
        print(f"[CERRADURA] >>> CERRADURA ABIERTA por {DURATION_SECONDS} segundos <<<")
    else:
        print("[CERRADURA] CERRADURA CERRADA.")


# Función para iniciar el streaming de video con FFmpeg (SRT/UDP)
def start_video_stream():
    """Inicia el streaming de video de la webcam usando FFmpeg y SRT."""
    print(f"\n[VIDEO] Iniciando streaming hacia {SRT_URL}...")
    
    # Comando FFmpeg para capturar, codificar a H.264 y transmitir por SRT.
    # NOTA: Asegúrate de que tu webcam sea /dev/video0
    ffmpeg_command = [
        'ffmpeg',
        '-f', 'v4l2',
        '-i', '/dev/video0', 
        '-t', str(DURATION_SECONDS),
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p',
        '-vcodec', 'libx264',
        '-preset', 'veryfast',
        '-tune', 'zerolatency',
        '-b:v', '800k', # Bitrate de video (800 kbps para mantener baja la carga).
        '-f', 'mpegts', # Formato de contenedor (compatible con SRT).
        SRT_URL
    ]
    
    try:
        # Usamos subprocess.Popen para que el stream corra en segundo plano
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[VIDEO] Stream iniciado. PID: {process.pid}")
        return process
    except FileNotFoundError:
        print("[ERROR] FFmpeg no se encontró. Asegúrate de que esté instalado.")
        return None
    except Exception as e:
        print(f"[ERROR] Falló al iniciar el stream: {e}")
        return None

# Función principal
def main_loop():
    print("=================================================================")
    print(f"       INICIANDO SISTEMA BLUE BOX - HELADERA {VENDING_MACHINE_ID}")
    print("=================================================================")
    
    # Inicialización de la cerradura (Cerrada por defecto)
    control_solenoid_lock('close') 

    while True:
        # 1. Esperar la tarjeta NFC
        student_uid = read_nfc_card_uid()
        
        if not student_uid:
            continue # Si no lee, vuelve a esperar
        
        # 2. Construir el payload para la Cloud Function
        # CORREGIDO: Usamos "nfcUid" en lugar de "uid" para coincidir con el backend.
        payload = {
            "nfcUid": student_uid, # <-- ¡CLAVE CORREGIDA!
            "machineId": VENDING_MACHINE_ID
        }
        
        # 3. Llamar a la Cloud Function (Autenticación y Balance)
        try:
            print(f"[CLOUD] Enviando UID ({student_uid}) a la Cloud Function: {CLOUD_FUNCTION_ENDPOINT}")
            # NOTA: Cloud Functions sin autenticación requieren un POST sin headers de Auth.
            response = requests.post(CLOUD_FUNCTION_ENDPOINT, json=payload, timeout=10)

            if response.status_code == 200:
                # Acceso concedido
                result = response.json()
                student_name = result.get('studentName', 'Estudiante Desconocido')
                print(f"\n[ACCESO OK] Estudiante {student_name} identificado y autorizado.")
                
                # 4. Iniciar el ciclo de transacción (Cerradura + Video)
                stream_process = start_video_stream()
                control_solenoid_lock('open')
                
                # 5. Esperar la duración de la transacción
                time.sleep(DURATION_SECONDS)
                
                # 6. Finalizar la transacción
                control_solenoid_lock('close')
                
                # 7. Detener el stream de video
                if stream_process and stream_process.poll() is None:
                    print("[VIDEO] Deteniendo el stream de video...")
                    stream_process.terminate()
                    # Esperar brevemente a que el proceso termine
                    time.sleep(1)
                
                print("[TRANSACCION] Ciclo completado. Listo para el siguiente estudiante.")

            elif response.status_code == 401:
                # No autorizado (saldo insuficiente o tarjeta no válida)
                error_msg = response.json().get('error', 'No autorizado').capitalize()
                print(f"[ACCESO DENEGADO] {error_msg}")
            else:
                # Otros errores de la Cloud Function
                print(f"[ERROR CLOUD] Fallo en el servidor ({response.status_code}): {response.text}")
                
        except requests.exceptions.RequestException as e:
            print(f"[ERROR CLOUD] No se pudo conectar a la Cloud Function: {e}")
        except Exception as e:
            print(f"[ERROR] Ocurrió un error inesperado: {e}")
            
        # Breve pausa para evitar lecturas inmediatas
        time.sleep(5)
        print("\n-----------------------------------------------------------------")


if __name__ == "__main__":
    # La cámara debe estar disponible antes de ejecutar
    if not os.path.exists('/dev/video0'):
        print("[ADVERTENCIA] La webcam no se encontró en /dev/video0. El streaming de video fallará.")
    
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\nSaliendo del programa.")
        # Asegurarse de que la cerradura esté cerrada al salir
        control_solenoid_lock('close')
    finally:
        # Aquí iría la limpieza de GPIO si se usa
        pass