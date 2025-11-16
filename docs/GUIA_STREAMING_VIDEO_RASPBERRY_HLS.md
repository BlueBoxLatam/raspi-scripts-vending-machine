Guía Definitiva: Streaming de Video desde Raspberry Pi a una Página Web con HLS

  1. Objetivo del Proyecto

  El objetivo es transmitir video en vivo desde una cámara conectada a una Raspberry Pi hacia una página web pública, alojada en una máquina
  virtual (VM) en la nube (GCP). El video debe ser accesible desde cualquier navegador moderno utilizando el protocolo HLS (HTTP Live
  Streaming).

  2. Arquitectura Final Funcional

  Después de un largo proceso de depuración, la arquitectura que demostró ser robusta y eficiente es la siguiente:

   * Raspberry Pi (Emisor):
       1. Captura: Usa ffmpeg para capturar video crudo (formato yuyv422) desde la cámara USB.
       2. Codificación: Codifica el video en tiempo real al formato H.264, usando el preset veryfast para no sobrecargar la CPU de la Pi. La
          resolución se reduce a 640x480 para asegurar un rendimiento estable.
       3. Empaquetado: Envuelve el video H.264 en un contenedor de transporte estándar (mpegts).
       4. Transmisión: Envía el stream a través del protocolo SRT (Secure Reliable Transport), que es confiable y maneja la pérdida de paquetes en
          redes inestables como internet.

   * Servidor/VM (Receptor):
       1. Recepción: Usa ffmpeg para recibir el stream SRT en un puerto específico (en nuestro caso, el 9000).
       2. Re-empaquetado (Muxing): Copia (-c copy) el stream de video H.264 (sin necesidad de recodificar, lo cual es muy eficiente) y lo segmenta
          en archivos HLS (.m3u8 para la lista de reproducción y .ts para los fragmentos de video).
       3. Almacenamiento: Guarda los archivos HLS en un directorio accesible por el servidor web (/var/www/hls/).

   * Servidor Web (Nginx):
       1. Sirve la página index.html que contiene el reproductor de video.
       2. Sirve los archivos HLS (.m3u8 y .ts) para que el reproductor de video en el navegador pueda acceder a ellos.

  3. Resumen de Errores Encontrados y su Solución

  Este es el camino que recorrimos para llegar a la solución final. Cada paso fue crucial para entender el problema completo.

   1. Error Inicial: `SyntaxError` en Python.
       * Problema: El script original de Python tenía errores de sintaxis e indentación.
       * Solución: Se corrigió la estructura del código para que fuera ejecutable.

   2. Error: `Address already in use` en la VM.
       * Problema: Al intentar ejecutar ffmpeg en la VM para escuchar en el puerto 9000, el sistema operativo nos informó que otro proceso ya
         estaba usando ese puerto.
       * Diagnóstico: Usamos el comando sudo lsof -i :9000 y descubrimos que el servidor web nginx estaba ocupando el puerto.
       * Solución: Investigamos la configuración de nginx con grep -r '9000' /etc/nginx/ y encontramos una línea listen 9000 udp;
         en /etc/nginx/nginx.conf. Se comentó todo el bloque stream {} que contenía esa directiva y se recargó nginx con sudo systemctl reload nginx.

   3. Error: `Packet corrupt`, `Data: bin_data` y `speed < 1.0x`.
       * Problema: Intentamos una arquitectura donde la Pi enviaba el video MJPEG de la cámara directamente, y la VM lo recodificaba. Esto falló
         por dos razones:
           1. La Pi no tenía suficiente potencia para recodificar a H.264 en alta resolución (1280x720), indicado por el speed=0.5x.
           2. El intento de enviar MJPEG dentro de un contenedor mpegts (usando UDP, RTP y finalmente SRT) resultó ser una combinación no estándar.
               El ffmpeg receptor no reconocía el stream de video y lo interpretaba como Data: bin_data, resultando en el error Output file #0 does
               not contain any stream.
       * Solución: Se rediseñó la arquitectura por completo a la versión final descrita anteriormente: la Pi codifica a H.264 en una resolución
         menor y la VM solo copia el stream.

   4. Error Final: `Permission denied` en la VM.
       * Problema: Una vez que el stream de video llegaba correctamente a la VM y era entendido, ffmpeg falló al intentar crear los archivos .ts y
          .m3u8 en el directorio /var/www/hls/.
       * Diagnóstico: El proceso ffmpeg se ejecutaba con el usuario ingenogales, pero el directorio /var/www/hls/ pertenecía al usuario root o
         www-data.
       * Solución: Se cambió la propiedad del directorio al usuario actual con el comando sudo chown -R $USER:$USER /var/www/hls/, otorgando los
         permisos de escritura necesarios.

  4. Guía de Implementación Definitiva (Paso a Paso)

  Paso 1: Configuración del Servidor (VM en GCP)

   1. Instalar software:
   1     sudo apt update
   2     sudo apt install nginx ffmpeg -y

   2. Crear y dar permisos al directorio HLS:

   1     # Crear el directorio donde se guardarán los videos
   2     sudo mkdir -p /var/www/hls/
   3 
   4     # Hacer a tu usuario actual el dueño de la carpeta para poder escribir en ella
   5     sudo chown -R $USER:$USER /var/www/hls/

   3. Crear la página web del reproductor:
   1     # Crear y editar el archivo index.html
   2     sudo nano /var/www/html/index.html
      Pega el siguiente contenido en el archivo:

    1     <!DOCTYPE html>
    2     <html lang="es">
    3     <head>
    4         <meta charset="UTF-8">
    5         <title>Live Stream desde Raspberry Pi</title>
    6         <style>
    7             body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;
      background-color: #f0f0f0; }
    8             #videoContainer { width: 80%; max-width: 960px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
    9             video { width: 100%; display: block; }
   10         </style>
   11         <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
   12     </head>
   13     <body>
   14         <div id="videoContainer">
   15             <video id="video" controls autoplay muted></video>
   16         </div>
   17         <script>
   18             document.addEventListener('DOMContentLoaded', function () {
   19                 var video = document.getElementById('video');
   20                 var videoSrc = '/hls/stream.m3u8'; // Ruta al manifiesto HLS
   21                 if (Hls.isSupported()) {
   22                     var hls = new Hls();
   23                     hls.loadSource(videoSrc);
   24                     hls.attachMedia(video);
   25                     hls.on(Hls.Events.MANIFEST_PARSED, function() {
   26                         video.play();
   27                     });
   28                 } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
   29                     video.src = videoSrc;
   30                     video.addEventListener('loadedmetadata', function() {
   31                         video.play();
   32                     });
   33                 }
   34             });
   35         </script>
   36     </body>
   37     </html>
      Guarda y sal (Ctrl+X, Y, Enter).

   4. Configurar Nginx para servir los archivos HLS:
       * Nginx necesita saber que debe servir los archivos .m3u8 y .ts con las cabeceras correctas. Edita la configuración por defecto:
   1         sudo nano /etc/nginx/sites-available/default
       * Dentro del bloque server { ... }, añade una nueva location para /hls:

    1         server {
    2             listen 80 default_server;
    3             listen [::]:80 default_server;
    4 
    5             root /var/www/html;
    6             index index.html index.htm index.nginx-debian.html;
    7 
    8             server_name _;
    9 
   10             location / {
   11                 try_files $uri $uri/ =404;
   12             }
   13 
   14             # --- AÑADIR ESTE BLOQUE ---
   15             location /hls {
   16                 types {
   17                     application/vnd.apple.mpegurl m3u8;
   18                     video/mp2t ts;
   19                 }
   20                 root /var/www;
   21                 add_header Cache-Control no-cache;
   22                 add_header Access-Control-Allow-Origin *; # Permite el acceso desde cualquier dominio
   23             }
   24             # --- FIN DEL BLOQUE A AÑADIR ---
   25         }
       * Guarda los cambios y reinicia Nginx para aplicarlos:
   1         sudo systemctl restart nginx

  Paso 2: Configuración de la Raspberry Pi

   1. Instalar software:

   1     sudo apt update
   2     sudo apt install ffmpeg -y
   3     pip3 install readchar

   2. Crear el script de Python:

   1     # Ve a tu directorio de proyectos
   2     cd /home/BLUE_BOX/vending_machine/
   3 
   4     # Crea y edita el script
   5     nano streamerv3gcp.py
      Pega el código final y funcional:

    1     import subprocess
    2     import time
    3     import threading
    4     import readchar
    5 
    6     # --- Configuración del Stream ---
    7     STREAM_DURATION = 0  # Segundos. Cambia a 0 para streaming continuo.
    8     VIDEO_RESOLUTION = "640x480" # Resolución que la Pi puede manejar codificando.
    9     VIDEO_FRAMERATE = "15"      # Cuadros por segundo.
   10     WEBCAM_DEVICE = "/dev/video0" # Dispositivo de tu webcam.
   11 
   12     # --- Configuración de GCP (¡Tu IP Pública!)
   13     GCP_VM_PUBLIC_IP = "136.113.251.166" # ¡Tu IP pública de la VM de GCP!
   14     STREAM_PORT = 9000
   15 
   16     # --- Variables Globales ---
   17     streaming_process = None
   18     is_streaming = False
   19     stream_lock = threading.Lock()
   20 
   21     def start_stream_session():
   22         """Inicia la sesión de streaming."""
   23         global streaming_process, is_streaming
   24 
   25         # Comando FFmpeg final y funcional
   26         ffmpeg_command = [
   27             'ffmpeg',
   28             '-f', 'v4l2',
   29             '-framerate', VIDEO_FRAMERATE,
   30             '-video_size', VIDEO_RESOLUTION,
   31             '-input_format', 'yuyv422', # Captura de video crudo
   32             '-i', WEBCAM_DEVICE,
   33             '-c:v', 'libx264',          # Codificamos a H.264
   34             '-preset', 'veryfast',      # La mejor opción para la Pi
   35             '-tune', 'zerolatency',     # Optimizado para baja latencia
   36             '-b:v', '800k',             # Un bitrate adecuado para 640x480
   37             '-f', 'mpegts',             # Contenedor estándar para SRT
   38             f'srt://{GCP_VM_PUBLIC_IP}:{STREAM_PORT}?mode=caller'
   39         ]
   40 
   41         print("\n--- Iniciando transmisión hacia GCP ---")
   42         print(f"Enviando video a: {GCP_VM_PUBLIC_IP}:{STREAM_PORT}")
   43 
   44         streaming_process = subprocess.Popen(ffmpeg_command)
   45         streaming_process.wait() # Espera a que el proceso termine
   46 
   47         print("\n--- La transmisión se ha detenido ---")
   48         with stream_lock:
   49             is_streaming = False
   50             streaming_process = None
   51 
   52     def main_loop():
   53         """Bucle principal que espera la pulsación de teclas."""
   54         global is_streaming, streaming_process
   55 
   56         print("Script de transmisión a GCP iniciado.")
   57         print("Presiona 's' para iniciar el video.")
   58         print("Presiona 'q' para salir del programa.")
   59 
   60         while True:
   61             key = readchar.readkey()
   62 
   63             if key.lower() == 's':
   64                 with stream_lock:
   65                     if is_streaming:
   66                         print("\n(Ya hay una transmisión en curso)")
   67                         continue
   68                     is_streaming = True
   69 
   70                 stream_thread = threading.Thread(target=start_stream_session)
   71                 stream_thread.daemon = True
   72                 stream_thread.start()
   73 
   74             elif key.lower() == 'q':
   75                 print("\nSaliendo del programa...")
   76                 with stream_lock:
   77                     if streaming_process and streaming_process.poll() is None:
   78                         print("Intentando detener el proceso de FFmpeg...")
   79                         streaming_process.terminate()
   80                 break
   81 
   82     if __name__ == "__main__":
   83         main_loop()
      Guarda y sal.

  Paso 3: ¡Lanzamiento!

  El orden es fundamental.

   1. En la VM (Receptor): Ejecuta el comando ffmpeg que se quedará esperando la conexión.

   1     ffmpeg -i "srt://0.0.0.0:9000?mode=listener" -c copy -an -f hls -hls_time 2 -hls_list_size 5 -hls_flags delete_segments
     /var/www/hls/stream.m3u8

   2. En la Raspberry Pi (Emisor): Una vez que la VM esté esperando, ejecuta tu script.

   1     python3 /home/BLUE_BOX/vending_machine/streamerv3gcp.py
      Presiona s para iniciar.

   3. Verifica y Disfruta:
       * La terminal de la VM debería cobrar vida y empezar a mostrar el progreso del video.
       * Abre tu navegador en http://<IP_DE_TU_VM>/index.html y disfruta de tu stream en vivo.
