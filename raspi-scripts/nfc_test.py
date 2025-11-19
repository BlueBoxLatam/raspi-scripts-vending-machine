import board
import busio
import time
# Importamos la clase específica para I2C
from adafruit_pn532.i2c import PN532_I2C 

# =================================================================
## 1. Configuración de Pines I2C
# =================================================================
# La Raspberry Pi usa board.SCL y board.SDA como pines I2C estándar.
try:
    I2C_BUS = busio.I2C(board.SCL, board.SDA)
except Exception as e:
    print(f"[ERROR] No se pudo inicializar I2C. Asegúrate de que I2C esté habilitado en raspi-config: {e}")
    exit()

# =================================================================
## 2. Inicialización del Lector PN532 (I2C)
# =================================================================
# La clase PN532_I2C solo necesita el objeto I2C_BUS.
pn532 = PN532_I2C(I2C_BUS, debug=False)

# =================================================================
## 3. Configuración y Chequeo Inicial
# =================================================================
print("--- Lector PN532 - Prueba Inicial (Modo I2C) ---")

# Leer versión de Firmware
try:
    ic, ver, rev, support = pn532.firmware_version
    print(f"✅ PN532 encontrado. Firmware: {ver}.{rev} y soporte {support}")
except Exception as e:
    print(f"❌ Error al leer la versión del PN532. Comprueba las conexiones. Error: {e}")
    exit()

# Establecer la configuración para leer tarjetas (modo Lector)
# Esto configura el PN532 para el modo de comunicación con tarjetas NFC/RFID.
pn532.SAM_configuration()

# =================================================================
## 4. Bucle Principal de Lectura
# =================================================================
print("\nEsperando una tarjeta NFC/RFID...")
while True:
    # Intenta leer un UID (espera por una tarjeta)
    # timeout=0.5 hace que la función no se bloquee indefinidamente.
    uid = pn532.read_passive_target(timeout=0.5)

    # Si se detecta un UID:
    if uid is not None:
        # El UID es un objeto 'bytes' (ej. b'\x53\xcd\xf5\x58'), lo convertimos a un string hexadecimal legible
        # Usamos una función de lista para formatear cada byte a dos caracteres hexadecimales
        uid_hex = "".join([f"{i:02x}" for i in uid])
        print("--------------------")
        print(f"🎉 Tarjeta Detectada y Leída 🎉")
        print(f"UID (Identificador Único): {uid_hex.upper()}")
        print("--------------------")
        # Pequeña pausa para evitar lecturas repetidas muy rápidas
        time.sleep(1)