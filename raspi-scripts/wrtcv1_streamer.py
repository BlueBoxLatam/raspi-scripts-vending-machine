import subprocess
import time
import signal
import sys
import os

# CONFIGURACIÓN
SERVER_IP = "34.55.59.16" # Tu IP de GCP
STREAM_PORT = "8890"      # Puerto donde MediaMTX escuchará el SRT
# IMPORTANTE: MediaMTX v1.9+ requiere ?streamid=publish:nombre_del_path
SRT_URL = f"srt://{SERVER_IP}:{STREAM_PORT}?mode=caller&streamid=publish:cam"

stream_process = None

def start_ffmpeg():
    global stream_process
    print(f"🎥 [WebRTC-PoC] Iniciando stream hacia {SRT_URL}...")
    
    # Ajustes simplificados para WebRTC
    # Nota: No necesitamos segmentar aquí, MediaMTX lo hará en memoria.
    # Enviamos stream continuo de baja latencia.
    cmd = [
        'ffmpeg', 
        '-f', 'v4l2', 
        '-framerate', '15',
        '-video_size', '640x480',
        '-i', '/dev/video0', 
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-pix_fmt', 'yuv420p', 
        '-c:v', 'libx264',           
        '-preset', 'ultrafast',      
        '-tune', 'zerolatency',      
        '-g', '15',             # Keyframe cada 1s (suficiente para WebRTC)     
        '-b:v', '600k',              
        '-bufsize', '600k',          
        '-f', 'mpegts', 
        SRT_URL
    ]
    
    stream_process = subprocess.Popen(cmd)

def stop_ffmpeg():
    global stream_process
    if stream_process:
        print("🛑 Deteniendo...")
        stream_process.terminate()
        stream_process = None

if __name__ == "__main__":
    try:
        start_ffmpeg()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_ffmpeg()
