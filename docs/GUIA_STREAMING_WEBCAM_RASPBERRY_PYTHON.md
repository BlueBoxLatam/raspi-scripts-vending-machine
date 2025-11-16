 Guía Completa: Streaming de Webcam con Raspberry Pi y Python

  Este documento detalla el proceso para crear un script en Python que transmite video desde una webcam conectada a una Raspberry Pi. Se abordan
  dos enfoques diferentes, se explican los errores comunes encontrados durante la configuración y se justifica el uso de herramientas específicas.

  1. Requisitos e Instalación

  Antes de empezar, es necesario preparar el sistema y las dependencias.

  1.1. Dependencias del Sistema

  Conéctate a tu Raspberry Pi por SSH y ejecuta los siguientes comandos para actualizar el sistema e instalar las herramientas necesarias.

   1 # Actualizar la lista de paquetes y los paquetes instalados
   2 sudo apt update && sudo apt upgrade -y
   3 
   4 # Instalar FFmpeg, la herramienta clave para el streaming de video
   5 sudo apt install ffmpeg -y
   6 
   7 # Instalar las herramientas para crear entornos virtuales de Python
   8 sudo apt install python3-venv -y

  1.2. Configuración del Entorno Virtual

  Es una buena práctica aislar las librerías de cada proyecto en un entorno virtual.

   1 # 1. Crea una carpeta para el proyecto y entra en ella
   2 mkdir webcam_streamer
   3 cd webcam_streamer
   4 
   5 # 2. Crea el entorno virtual (se creará una carpeta oculta .venv)
   6 python3 -m venv .venv
   7 
   8 # 3. Activa el entorno virtual. Notarás que tu terminal ahora muestra (.venv)
   9 source .venv/bin/activate
  Importante: Siempre que trabajes en este proyecto, deberás activar el entorno virtual con el último comando.

  2. Los Scripts de Streaming

  Hemos desarrollado dos versiones del script, cada una con una librería diferente para la captura de teclas, adaptadas a distintos casos de uso.

  2.1. Versión 1: streamerv1.py (con pynput)

  Esta versión es ideal si planeas ejecutar el script directamente en el entorno de escritorio de la Raspberry Pi (con un monitor y teclado
  conectados).

  Instalación de la librería:
   1 # Asegúrate de que el entorno virtual esté activo
   2 pip install pynput

  Código:

    1 # streamerv1.py
    2 import subprocess
    3 import time
    4 import socket
    5 import threading
    6 from pynput import keyboard
    7 
    8 # --- Configuración ---
    9 STREAM_DURATION = 40
   10 VIDEO_RESOLUTION = "640x360"
   11 VIDEO_FRAMERATE = "15"
   12 STREAM_PORT = 8090
   13 WEBCAM_DEVICE = "/dev/video0"
   14 
   15 # ... (resto del código con pynput) ...
   16 
   17 def on_press(key):
   18     """Función que se ejecuta cada vez que se presiona una tecla."""
   19     global is_streaming
   20     try:
   21         if key.char.lower() == 's':
   22             # ... (lógica para iniciar el stream en un hilo) ...
   23     except AttributeError:
   24         pass
   25
   26 def on_release(key):
   27     """Función para detener el script si se presiona la tecla Esc."""
   28     if key == keyboard.Key.esc:
   29         return False
   30
   31 if __name__ == "__main__":
   32     print("Presiona 's' para iniciar, 'Esc' para salir.")
   33     with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
   34         listener.join()

  2.2. Versión 2: streamerv2.py (con readchar)

  Esta es la versión recomendada para controlar el script a través de una conexión SSH, ya que no depende de una interfaz gráfica.

  Instalación de la librería:
   1 # Asegúrate de que el entorno virtual esté activo
   2 pip install readchar

  Código:

    1 # streamerv2.py
    2 import subprocess
    3 import time
    4 import socket
    5 import threading
    6 import readchar # <--- Librería clave
    7 
    8 # --- Configuración ---
    9 STREAM_DURATION = 40
   10 VIDEO_RESOLUTION = "640x360"
   11 VIDEO_FRAMERATE = "15"
   12 STREAM_PORT = 8090
   13 WEBCAM_DEVICE = "/dev/video0"
   14 
   15 # ... (resto del código con readchar) ...
   16 
   17 def main_loop():
   18     """Bucle principal que espera la pulsación de teclas."""
   19     print("Presiona 's' para iniciar, 'q' para salir.")
   20     while True:
   21         key = readchar.readkey() # Lee una tecla de la terminal
   22         if key.lower() == 's':
   23             # ... (lógica para iniciar el stream en un hilo) ...
   24         elif key.lower() == 'q':
   25             print("\nSaliendo del programa...")
   26             break
   27
   28 if __name__ == "__main__":
   29     main_loop()

  3. Comparativa: pynput vs. readchar

  La principal diferencia entre los dos scripts es la librería utilizada para detectar la pulsación de la tecla 's'.

   * `pynput`: Es una librería que funciona a un nivel más global. Se "engancha" al sistema operativo para escuchar eventos de teclado (y ratón) en
     todas las aplicaciones. Para hacer esto en Linux, necesita acceder al servidor X, que es el sistema que gestiona la interfaz gráfica (ventanas,
     cursor, etc.).
       * Ventaja: Puede detectar teclas incluso si la ventana de la terminal no está en primer plano.
       * Desventaja: Falla si no hay una sesión gráfica activa, que es exactamente lo que ocurre en una conexión SSH estándar.

   * `readchar`: Es una librería mucho más simple. Su única función es leer el siguiente carácter o tecla que se introduce en la entrada estándar de
     la terminal (stdin), es decir, la ventana donde se está ejecutando el script.
       * Ventaja: No depende de ninguna interfaz gráfica, por lo que es perfecta para aplicaciones de terminal y para ser ejecutada por SSH.
       * Desventaja: Solo funciona si la terminal donde corre el script está activa y en primer plano.

  Conclusión: Para nuestro objetivo de controlar el script remotamente por SSH, readchar es la solución robusta y correcta.

  4. Errores Comunes y Soluciones

  Durante el proceso, nos encontramos con los siguientes problemas:

   1. Error de SSH: `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`
       * Causa: Ocurre después de reinstalar el sistema operativo de la Raspberry Pi. Tu PC recuerda la "huella digital" (clave de host) antigua de
         la Pi, y al ver una nueva, bloquea la conexión por seguridad.
       * Solución: Eliminar la clave antigua del archivo known_hosts en tu PC. El comando más sencillo es:
   1         ssh-keygen -R raspberrypi.local

   2. Error de Python: `ModuleNotFoundError: No module named 'pynput'`
       * Causa: Intentar ejecutar el script de Python sin haber activado primero el entorno virtual. El intérprete de Python global no sabe dónde
         encontrar las librerías que instalaste dentro de .venv.
       * Solución: Activar siempre el entorno virtual antes de ejecutar el script:
   1         source .venv/bin/activate
   2         python tu_script.py

   3. Error de `pynput`: `ImportError: this platform is not supported: failed to acquire X connection`
       * Causa: Ejecutar el script con pynput a través de una conexión SSH. La librería no encuentra una interfaz gráfica a la cual conectarse.
       * Solución: Usar la versión del script con la librería readchar, que no tiene esta dependencia.

  5. Visualización del Stream: ¿Por qué usar VLC?

  Al intentar abrir la URL del stream (http://IP:PUERTO) en un navegador web, notamos que en lugar de mostrar el video, iniciaba una descarga.

   * Causa: Los navegadores web modernos son muy estrictos y esperan que los servidores envíen cabeceras HTTP muy específicas (Content-Type) para
     saber cómo manejar el contenido. El servidor HTTP básico de FFmpeg no envía estas cabeceras de la manera que el navegador espera para un video
     en vivo, por lo que el navegador opta por la acción por defecto: descargar el contenido como si fuera un archivo.

   * Solución: Utilizar un reproductor de video diseñado para manejar streams de red, como VLC Media Player. VLC es mucho más flexible y es capaz de
     interpretar correctamente el flujo de datos MJPEG y mostrarlo como un video en tiempo real.

  Para abrir el stream en VLC:
   1. Abre VLC.
   2. Ve a Medio > Abrir ubicación de red....
   3. Pega la URL proporcionada por el script.
   4. Haz clic en Reproducir.
