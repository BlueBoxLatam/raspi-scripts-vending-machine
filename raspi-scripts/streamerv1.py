import subprocess
import time
import socket
import threading
from pynput import keyboard

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
        # No necesita estar "conectado", solo se usa para obtener la IP
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
        s.close()
        return IP
    except Exception:
        return "127.0.0.1"

def start_stream_session():
    """Inicia y gestiona un ciclo de transmisión de video con FFmpeg."""
    global streaming_process, is_streaming

    # Construimos el comando de FFmpeg
    # Explicación de los parámetros:
    # -f v4l2:              Formato de entrada para Video4Linux2 (webcams en Linux).
    # -input_format mjpeg:  Solicita a la webcam que envíe video comprimido en MJPEG.
    #                       Esto reduce drásticamente el uso de CPU en la Pi.
    # -framerate/-video_size: Parámetros de captura.
    # -i {WEBCAM_DEVICE}:   El dispositivo de la webcam.
    # -c:v copy:            No re-codifica el video, solo lo copia. Es la opción MÁS eficiente.
    # -f mjpeg:             Formato del contenedor de salida.
    # -listen 1:            Actúa como un servidor HTTP esperando una conexión.
    # http://0.0.0.0:{port}: URL donde servirá el stream.
    ffmpeg_command = [
        'ffmpeg',
        '-f', 'v4l2',
        '-input_format', 'mjpeg',
        '-framerate', VIDEO_FRAMERATE,
        '-video_size', VIDEO_RESOLUTION,
        '-i', WEBCAM_DEVICE,
        '-c:v', 'copy',
        '-f', 'mjpeg',
        '-listen', '1',
        f'http://0.0.0.0:{STREAM_PORT}'
    ]

    ip_address = get_ip_address()
    print("\n--- Iniciando transmisión ---")
    print(f"🔥 Abre esta URL en un navegador o VLC en otra computadora:")
    print(f"    http://{ip_address}:{STREAM_PORT}")
    print(f"La transmisión durará {STREAM_DURATION} segundos.")

    # Inicia el proceso de FFmpeg en segundo plano
    # stdout y stderr se redirigen para no saturar la terminal
    streaming_process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Espera la duración definida
    time.sleep(STREAM_DURATION)

    # Termina el proceso de FFmpeg
    if streaming_process and streaming_process.poll() is None:
        print("\n--- Deteniendo transmisión ---")
        streaming_process.terminate()
        # Espera a que el proceso termine completamente
        try:
            streaming_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            streaming_process.kill()
            print("El proceso de FFmpeg no respondió, fue forzado a cerrarse.")
        print("Listo. Esperando la próxima pulsación de 's'.")

    # Libera el bloqueo para permitir una nueva transmisión
    with stream_lock:
        is_streaming = False
        streaming_process = None


def on_press(key):
    """Función que se ejecuta cada vez que se presiona una tecla."""
    global is_streaming
    try:
        # Comprueba si la tecla es 's' o 'S'
        if key.char.lower() == 's':
            with stream_lock:
                if is_streaming:
                    print("\n(Ya hay una transmisión en curso, espera a que termine)")
                    return
                is_streaming = True

            # Inicia la sesión de streaming en un hilo separado
            # para no bloquear el listener de teclado.
            stream_thread = threading.Thread(target=start_stream_session)
            stream_thread.daemon = True
            stream_thread.start()

    except AttributeError:
        # Ignora teclas especiales que no tienen 'char' (como Shift, Ctrl, etc.)
        pass

def on_release(key):
    """Función para detener el script si se presiona la tecla Esc."""
    if key == keyboard.Key.esc:
        print("\nSaliendo del programa...")
        # Detener el listener
        return False

# --- Programa Principal ---
if __name__ == "__main__":
    print("Script de transmisión iniciado.")
    print("Presiona 's' o 'S' para iniciar el video por 40 segundos.")
    print("Presiona 'Esc' para salir del programa.")

    # Configura y ejecuta el listener de teclado
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()

    # Asegurarse de que si el script termina, el proceso de ffmpeg también lo haga
    if streaming_process and streaming_process.poll() is None:
        streaming_process.kill()
        print("Proceso de FFmpeg detenido al salir.")
