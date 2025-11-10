# Guía Definitiva para Configuración de Red en Raspberry Pi para Proyectos IoT

Este documento resume el proceso para configurar de manera robusta y fiable la conexión de red de una Raspberry Pi destinada a un proyecto "headless" (sin monitor) o IoT, como una máquina expendedora o una heladera inteligente. El objetivo es asegurar una conexión automática a redes Wi-Fi conocidas y asignar una dirección IP estática predecible.

## 1. El Objetivo: ¿Por Qué `dhcpcd` y no `NetworkManager`?

Para un dispositivo como una heladera, que siempre estará en el mismo sitio o se conectará a redes predefinidas, necesitamos **estabilidad y predecibilidad** por encima de la flexibilidad.

*   **`NetworkManager`**: Es como un "chófer con una app". Es ideal para portátiles que cambian de red constantemente. Es potente, tiene una interfaz gráfica, pero introduce más capas de complejidad que pueden fallar o sobrescribir configuraciones.
*   **`dhcpcd`**: Es como un "mecánico de confianza". Es una herramienta simple y ligera cuyo único trabajo es obtener una dirección IP. Se configura directamente en archivos de texto, lo que nos da un control total y un comportamiento 100% predecible. **Para un servidor o dispositivo IoT, esta es la opción superior.**

## 2. Paso a Paso: La Configuración Ideal

### Paso 2.1: Asegurar que `dhcpcd` tenga el control

El primer paso es eliminar cualquier conflicto, desactivando `NetworkManager` y asegurando que `dhcpcd` sea el único gestor de red.

1.  **Detener y desactivar `NetworkManager`:**
    ```bash
    sudo systemctl stop NetworkManager
    sudo systemctl disable NetworkManager
    ```

2.  **Instalar `dhcpcd` (si no existe):**
    ```bash
    sudo apt update && sudo apt install dhcpcd5
    ```
    *   **Error Común:** Si este comando falla por falta de internet, es porque al detener `NetworkManager` perdimos la conexión.
    *   **Solución:** Reactiva temporalmente `NetworkManager` solo para la instalación.
        ```bash
        sudo systemctl start NetworkManager
        # Ahora vuelve a ejecutar el comando de instalación
        sudo apt update && sudo apt install dhcpcd5
        # Y una vez instalado, vuelve a detener NetworkManager
        sudo systemctl stop NetworkManager
        ```

3.  **Activar `dhcpcd` para que se inicie siempre:**
    ```bash
    sudo systemctl enable dhcpcd
    sudo systemctl start dhcpcd
    ```

### Paso 2.2: Configurar las Credenciales Wi-Fi (`wpa_supplicant`)

Este archivo es la "agenda" donde guardamos los nombres y contraseñas de las redes Wi-Fi que la Pi debe conocer.

1.  **Editar el archivo:**
    ```bash
    sudo nano /etc/wpa_supplicant/wpa_supplicant.conf
    ```
    *   **Error Común:** El archivo aparece en blanco.
    *   **Solución:** ¡Es normal! Significa que no hay redes preconfiguradas por este método. Simplemente añade el contenido.

2.  **Añadir la configuración de red:**
    ```conf
    # Configuración base y código de país
    ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
    update_config=1
    country=BO # Cambiar por el código de tu país (ej: MX, ES, CO, US)

    # Configuración para la red del hotspot
    network={
        ssid="BlueBox"
        psk="tu_contraseña_wifi"
        key_mgmt=WPA-PSK
    }
    ```
    *   **Nota Crítica:** El `ssid` y `psk` (contraseña) deben ser **exactos**, respetando mayúsculas, minúsculas y espacios.

### Paso 2.3: Asignar la IP Estática (`dhcpcd.conf`)

Aquí le decimos a la Pi qué dirección IP debe tomar cuando se conecte a una red específica.

1.  **Editar el archivo:**
    ```bash
    sudo nano /etc/dhcpcd.conf
    ```

2.  **Añadir el perfil estático al final del archivo:**
    ```conf
    # --- Perfil de IP estática para el hotspot ---
    interface wlan0
    ssid BlueBox

    # Asignamos la IP estática. /24 significa máscara de subred 255.255.255.0
    static ip_address=10.68.57.200/24

    # Puerta de enlace (el router/teléfono)
    static routers=10.68.57.1

    # Servidores DNS para tener conexión a internet
    static domain_name_servers=8.8.8.8 1.1.1.1
    ```
    *   **Error Común:** La IP estática no se aplica.
    *   **Solución:** Asegúrate de que el `ssid` en este archivo (`ssid BlueBox`) sea idéntico al del otro archivo, pero **sin comillas**.

### Paso 2.4: Solucionar Problemas de Arranque (Condición de Carrera)

A veces, el Wi-Fi no se activa al reiniciar, pero sí funciona si lo fuerzas manualmente.

*   **Síntoma:** `hostname -I` sale en blanco al reiniciar. Los logs de `wpa_supplicant` muestran `rfkill: WLAN soft blocked`.
*   **Causa:** El servicio de Wi-Fi (`wpa_supplicant`) intenta iniciarse antes de que la interfaz de red (`wlan0`) esté completamente lista y desbloqueada por el sistema.
*   **Solución:** Hacer que `wpa_supplicant` espere.

1.  **Editar el archivo de servicio:**
    ```bash
    sudo nano /lib/systemd/system/wpa_supplicant.service
    ```

2.  **Añadir una dependencia en la sección `[Unit]`:**
    ```ini
    [Unit]
    Description=WPA supplicant
    Before=network.target
    After=dbus.service network-pre.target  # <-- Añadir "network-pre.target"
    ```

3.  **Recargar el gestor de servicios y reiniciar:**
    ```bash
    sudo systemctl daemon-reload
    sudo reboot
    ```

## 3. Guía Rápida de Comandos de Diagnóstico

*   **Ver tu IP de red:**
    `hostname -I`
*   **Ver el nombre de la red Wi-Fi a la que estás conectado:**
    `iwgetid`
*   **Probar si tienes conexión a internet:**
    `ping -c 4 google.com`
*   **Ver los últimos logs del servicio de Wi-Fi:**
    `journalctl -u wpa_supplicant -n 50 --no-pager`
*   **Ver los últimos logs del servicio de asignación de IP:**
    `journalctl -u dhcpcd -n 50 --no-pager`
*   **Forzar una reconfiguración de la red sin reiniciar:**
    `sudo dhcpcd -n wlan0`

## 4. Tareas Avanzadas

### Añadir Múltiples Redes Wi-Fi

1.  **En `/etc/wpa_supplicant/wpa_supplicant.conf`**, añade otro bloque `network={...}`:
    ```conf
    network={
        ssid="MiCasaWiFi"
        psk="contraseña_de_casa"
    }
    ```
2.  **En `/etc/dhcpcd.conf`**, añade otro perfil estático:
    ```conf
    # --- Perfil para la red de casa ---
    interface wlan0
    ssid MiCasaWiFi
    static ip_address=192.168.1.200/24
    static routers=192.168.1.1
    static domain_name_servers=8.8.8.8 1.1.1.1
    ```

### Revertir a NetworkManager

Si necesitas usar la Pi como un portátil, puedes volver a `NetworkManager`.

```bash
# Detener y desactivar dhcpcd
sudo systemctl stop dhcpcd
sudo systemctl disable dhcpcd

# Activar y arrancar NetworkManager
sudo systemctl enable NetworkManager
sudo systemctl start NetworkManager

# Reiniciar
sudo reboot
