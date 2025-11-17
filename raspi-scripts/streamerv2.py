import subprocess
import time
import socket
import threading
import readchar # <--- Cambiamos la librería

# --- Configuración ---
STREAM_DURATION = 40  # Segundos
VIDEO_RESOLUTION = "640x360"
VIDEO_FRAMERATE = "15"
STREAM_PORT = 8090  # Puerto para ver la transmisión
WEBCAM_DEVICE = "/dev/video0" # Generalmente es este, si no, verifica con 'ls /dev/video*'

# --- Variables Globales ---
streaming_process = None
is_streaming = False
stream_lock = threading.Lock()

def get_ip_address():
    """Obtiene la dirección IP local de la Raspberry Pi."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
        s.close()
        return IP
    except Exception:
        return "127.0.0.1"

def start_stream_session():
    """Inicia y gestiona un ciclo de transmisión de video con FFmpeg."""
    global streaming_process, is_streaming

    ffmpeg_command = [
        'ffmpeg', '-f', 'v4l2', '-input_format', 'mjpeg',
        '-framerate', VIDEO_FRAMERATE, '-video_size', VIDEO_RESOLUTION,
        '-i', WEBCAM_DEVICE, '-c:v', 'copy', '-f', 'mjpeg',
        '-listen', '1', f'http://0.0.0.0:{STREAM_PORT}'
    ]

    ip_address = get_ip_address()
    print("\n--- Iniciando transmisión ---")
    print(f"🔥 Abre esta URL en un navegador o VLC en otra computadora:")
    print(f"    http://{ip_address}:{STREAM_PORT}")
    print(f"La transmisión durará {STREAM_DURATION} segundos.")

    streaming_process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    time.sleep(STREAM_DURATION)

    if streaming_process and streaming_process.poll() is None:
        print("\n--- Deteniendo transmisión ---")
        streaming_process.terminate()
        try:
            streaming_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            streaming_process.kill()
            print("El proceso de FFmpeg no respondió, fue forzado a cerrarse.")
        print("Listo. Esperando la próxima pulsación de 's'.")

    with stream_lock:
        is_streaming = False
        streaming_process = None

def main_loop():
    """Bucle principal que espera la pulsación de teclas."""
    global is_streaming

    print("Script de transmisión iniciado.")
    print("Presiona 's' o 'S' para iniciar el video por 40 segundos.")
    print("Presiona 'q' para salir del programa.")

    while True:
        # Lee una sola tecla de la terminal
        key = readchar.readkey()

        if key.lower() == 's':
            with stream_lock:
                if is_streaming:
                    print("\n(Ya hay una transmisión en curso, espera a que termine)")
                    continue
                is_streaming = True

            # Inicia la sesión de streaming en un hilo para no bloquear el bucle
            stream_thread = threading.Thread(target=start_stream_session)
            stream_thread.daemon = True
            stream_thread.start()

        elif key.lower() == 'q':
            print("\nSaliendo del programa...")
            break

# --- Programa Principal ---
if __name__ == "__main__":
    try:
        main_loop()
    finally:
        # Asegurarse de que si el script termina, el proceso de ffmpeg también lo haga
        if streaming_process and streaming_process.poll() is None:
            streaming_process.kill()
            print("Proceso de FFmpeg detenido al salir.")
