import socket
import requests
import subprocess
import json
import time
import sys

# --- CONFIGURACIÓN BLUE BOX ---
TARGET_IP = "34.55.59.16"  # Tu IP Pública de GCP
API_PORT = 3000            # Puerto de la API Node.js
SRT_PORT = 9000            # Puerto de Ingesta de Video
TIMEOUT = 3                # Segundos de espera máxima

# Colores para la terminal
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

def print_status(step, status, message):
    if status == "OK":
        print(f"[{step}] {Colors.OKGREEN}✔ ÉXITO{Colors.ENDC}: {message}")
    elif status == "FAIL":
        print(f"[{step}] {Colors.FAIL}✖ FALLO{Colors.ENDC}: {message}")
    elif status == "WARN":
        print(f"[{step}] {Colors.WARNING}⚠ ALERTA{Colors.ENDC}: {message}")

print(f"{Colors.HEADER}--- INICIANDO DIAGNÓSTICO BLUE BOX VENDING ---{Colors.ENDC}")
print(f"Objetivo: {TARGET_IP}")

# 1. PRUEBA DE PING (ICMP)
print("\n--- 1. Verificando Visibilidad (Ping) ---")
try:
    # -c 1 (un paquete), -W 2 (timeout 2s)
    output = subprocess.check_output(
        ["ping", "-c", "1", "-W", "2", TARGET_IP], 
        stderr=subprocess.STDOUT, 
        universal_newlines=True
    )
    print_status("PING", "OK", "La VM es visible y responde.")
except subprocess.CalledProcessError:
    print_status("PING", "FAIL", "No hay respuesta de Ping. Revisa si la instancia está encendida.")
    sys.exit() # Si no hay ping, no tiene sentido seguir

# 2. PRUEBA DE PUERTO TCP (API)
print("\n--- 2. Verificando Puerto API (3000) ---")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(TIMEOUT)
result = sock.connect_ex((TARGET_IP, API_PORT))
if result == 0:
    print_status("PORT 3000", "OK", "El puerto está ABIERTO y aceptando conexiones.")
    port_open = True
else:
    print_status("PORT 3000", "FAIL", f"Puerto cerrado o bloqueado (Código: {result}).")
    print(f"{Colors.WARNING} -> Acción: Revisa Firewall de GCP o si Node.js (PM2) está corriendo.{Colors.ENDC}")
    port_open = False
sock.close()

# 3. PRUEBA DE PUERTO UDP (VIDEO)
# Nota: UDP no tiene handshake, es difícil saber si está 'abierto' sin respuesta, 
# pero verificamos si podemos enviar.
print("\n--- 3. Verificando Puerto Video (9000 UDP) ---")
try:
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.settimeout(TIMEOUT)
    # Enviamos un paquete dummy
    udp_sock.sendto(b'Ping SRT', (TARGET_IP, SRT_PORT))
    print_status("PORT 9000", "OK", "Paquete UDP enviado (Nota: UDP no confirma recepción).")
except Exception as e:
    print_status("PORT 9000", "FAIL", f"Error al enviar UDP: {e}")

# 4. SIMULACIÓN DE TRANSACCIÓN (MOCK REQUEST)
if port_open:
    print("\n--- 4. Simulando Petición API (HTTP POST) ---")
    url = f"http://{TARGET_IP}:{API_PORT}/api/transaction" # Ajusta tu ruta si es diferente
    
    # Payload simulado (Mock Data)
    payload = {
        "nfcUid": "TEST_CARD_DIAGNOSTIC",
        "machineId": "diag-script-pi",
        "timestamp": time.time()
    }
    
    try:
        print(f"Enviando POST a {url}...")
        response = requests.post(url, json=payload, timeout=TIMEOUT)
        
        if response.status_code == 200:
            print_status("API TEST", "OK", f"Respuesta del Servidor: {response.text}")
        else:
            print_status("API TEST", "WARN", f"El servidor respondió con error {response.status_code}: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print_status("API TEST", "FAIL", "Connection Refused por la librería HTTP.")
    except Exception as e:
        print_status("API TEST", "FAIL", f"Error inesperado: {e}")

print(f"\n{Colors.HEADER}--- DIAGNÓSTICO FINALIZADO ---{Colors.ENDC}")