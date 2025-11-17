import subprocess
import time
import socket
import threading
import readchar # Asegúrate de tenerlo instalado: pip install readchar

# --- Configuración del Stream ---
STREAM_DURATION = 40  # Segundos. Cambia a 0 o un número muy grande para streaming continuo.
VIDEO_RESOLUTION = "640x480" # Calidad 720p. Ajusta si tu webcam no lo soporta o si quieres menos calidad.
VIDEO_FRAMERATE = "15"      # Cuadros por segundo. 15-25 FPS es un buen balance para la Pi.
WEBCAM_DEVICE = "/dev/video0" # Dispositivo de tu webcam. Generalmente es /dev/video0.

# --- Configuración de GCP (¡Tu IP Pública!) ---
GCP_VM_PUBLIC_IP = "136.113.251.166" # ¡Tu IP pública de la VM de GCP!
STREAM_PORT = 9000                   # Puerto que abriste en el firewall de GCP para SRT

# --- Variables Globales ---
streaming_process = None
is_streaming = False
stream_lock = threading.Lock()

def start_stream_session():
    """Inicia una sesión de transmisión de video a la VM de GCP usando UDP."""
    global streaming_process, is_streaming

    # Comando FFmpeg para ENVIAR el stream en formato UDP
    # Este comando-CODIFICA el video a H.264 (libx264) ya que la salida es UDP.
    ffmpeg_command = [
        'ffmpeg',
        '-f', 'v4l2',
        '-framerate', VIDEO_FRAMERATE,
        '-video_size', VIDEO_RESOLUTION,
        '-input_format', 'yuyv422', # Captura de video crudo (muy común en webcams)
        '-i', WEBCAM_DEVICE,
        '-c:v', 'libx264',          # Codificamos a H.264
        '-preset', 'veryfast',      # La mejor opción para la Pi
        '-tune', 'zerolatency',     # Optimizado para baja latencia
        '-b:v', '800k',             # Un bitrate adecuado para 640x480
        '-f', 'mpegts',
        f'srt://{GCP_VM_PUBLIC_IP}:{STREAM_PORT}?mode=caller'
    ]

    print("\n--- Iniciando transmisión hacia GCP ---")
    print(f"Enviando video a: {GCP_VM_PUBLIC_IP}:{STREAM_PORT}")
    if STREAM_DURATION > 0:
        print(f"La transmisión durará {STREAM_DURATION} segundos.")
    else:
        print("La transmisión es continua (hasta que la detengas manualmente).")

    # Iniciamos el proceso de FFmpeg.
    streaming_process = subprocess.Popen(
        ffmpeg_command,
        # Mantener los comentarios de stdout/stderr para ver los logs de FFmpeg
        # stdout=subprocess.DEVNULL, # Redirige la salida estándar a /dev/null
        # stderr=subprocess.DEVNULL  # Redirige los errores estándar a /dev/null
    )

    # Esperamos la duración definida.
    if STREAM_DURATION > 0:
        time.sleep(STREAM_DURATION)
    else:
        streaming_process.wait()

    # Detenemos el proceso de forma segura
    if streaming_process and streaming_process.poll() is None: # Si el proceso sigue activo
        print("\n--- Deteniendo transmisión ---")
        streaming_process.terminate() # Intenta terminarlo limpiamente
        try:
            streaming_process.wait(timeout=5) # Espera un poco a que termine
        except subprocess.TimeoutExpired:
            streaming_process.kill() # Si no termina, fórza el cierre
            print("El proceso de FFmpeg fue forzado a cerrarse.")
        print("Listo. Esperando la próxima pulsación de 's'.")

    with stream_lock:
        is_streaming = False
        streaming_process = None

def main_loop():
    """Bucle principal que espera la pulsación de teclas."""
    global is_streaming

    print("Script de transmisión a GCP iniciado.")
    print(f"La IP de la VM de destino es: {GCP_VM_PUBLIC_IP}")
    print("Presiona 's' o 'S' para iniciar el video.")
    print("Presiona 'q' para salir del programa.")
    if STREAM_DURATION == 0:
        print("Para detener una transmisión continua, presiona 'q' y el script intentará cerrarla.")

    while True:
        key = readchar.readkey() # Lee una sola tecla sin necesidad de Enter

        if key.lower() == 's':
            with stream_lock:
                if is_streaming:
                    print("\n(Ya hay una transmisión en curso, espera a que termine o presiona 'q' para salir y reiniciar)")
                    continue
                is_streaming = True

            # Inicia la transmisión en un hilo separado para no bloquear la interfaz
            stream_thread = threading.Thread(target=start_stream_session)
            stream_thread.daemon = True # El hilo se cerrará cuando el programa principal lo haga
            stream_thread.start()

        elif key.lower() == 'q':
            print("\nSaliendo del programa...")
            # Intenta detener el proceso de streaming si está activo
            with stream_lock:
                if streaming_process and streaming_process.poll() is None:
                    print("Intentando detener el proceso de FFmpeg antes de salir...")
                    streaming_process.terminate()
                    try:
                        streaming_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        streaming_process.kill()
                        print("FFmpeg forzado a cerrarse al salir.")
            break # Sale del bucle principal

if __name__ == "__main__":
    try:
        main_loop()
    finally:
        # Asegura que FFmpeg se detenga si el script se cierra inesperadamente
        if streaming_process and streaming_process.poll() is None:
            streaming_process.kill()
            print("Proceso de FFmpeg detenido al salir.")
