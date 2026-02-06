# 🛡️ Bitácora de Migración a Producción Segura (HTTPS/SSL) y Actualización SOTA

**Fecha:** 04 de Febrero de 2026  
**Proyecto:** Blue Box Vending Machine  
**Objetivo de la Sesión:** Migración del entorno de desarrollo a producción segura bajo dominio `grabbie.one` utilizando Cloudflare, Nginx y SSL.

---

## 1. Registro de Errores y Obstáculos (Troubleshooting Log)

Durante el despliegue se presentaron y resolvieron los siguientes bloqueos críticos:

| Error / Síntoma | Causa Raíz | Solución Implementada |
| :--- | :--- | :--- |
| **`Failed to fetch`** (Frontend) | Bloqueo de **CORS** y **Mixed Content**. El navegador bloqueaba peticiones HTTP inseguras desde un origen HTTPS, o el servidor Node.js no aceptaba el origen. | 1. Configuración de `API_URL` a `https`.<br>2. Ajuste de transportes en Socket.IO (`websocket`, `polling`).<br>3. Habilitación de CORS en `server.js`. |
| **`ERR_TOO_MANY_REDIRECTS`** | Conflicto de modos SSL. Cloudflare estaba en modo **Flexible** (hablando HTTP con el servidor), pero Nginx estaba configurado para forzar redirección a HTTPS, creando un bucle infinito. | Cambio de Cloudflare a modo **Full** y configuración de Nginx para escuchar en puerto 443 con certificados autofirmados. |
| **Error 521: Web Server is Down** | **Nginx no escuchaba en el puerto 443**. Aunque el servicio estaba activo, la configuración no se había recargado correctamente o tenía errores de sintaxis, por lo que rechazaba la conexión segura de Cloudflare. | Sobrescritura forzada de la configuración de Nginx usando `tee` y reinicio del servicio. |
| **Node.js Crash (`SyntaxError`)** | Carácter accidental (`a`) al inicio del archivo `server.js`. | Limpieza del código y validación de sintaxis. |
| **Servidor Apagado (Connection Refused)** | El proceso de Node.js se detenía al cerrar la sesión SSH. | Implementación de **PM2** para gestión de procesos y persistencia tras reinicios. |
| **Advertencia de Seguridad en Navegador** | Uso de certificados autofirmados (`nginx-selfsigned.crt`) en el servidor de origen. | Comportamiento esperado en modo Cloudflare Full (no Strict). Se acepta la excepción en el navegador o se confía en el proxy de Cloudflare. |

---

## 2. Guía Paso a Paso de Solución (Comandos Ejecutados)

A continuación, el resumen consolidado de los comandos utilizados para estabilizar la infraestructura.

### Paso 1: Generación de Certificados SSL (Autofirmados)
Para permitir el modo **Cloudflare Full**, generamos certificados locales en la VM.

```bash
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/nginx-selfsigned.key \
  -out /etc/ssl/certs/nginx-selfsigned.crt
```
*Datos ingresados:* Country: `BO`, Common Name: `api.grabbie.one`.

### Paso 2: Configuración de Persistencia con PM2
Para asegurar que la API de Node.js nunca se apague.

```bash
# 1. Instalación
sudo npm install -g pm2

# 2. Iniciar proceso
cd ~/bluebox-server
pm2 start server.js --name "bluebox-api"

# 3. Congelar lista de procesos para reinicios
pm2 save
pm2 startup
```

### Paso 3: Configuración Definitiva de Nginx (Reverse Proxy + SSL)
Se utilizó el comando `tee` para evitar errores de edición manual y asegurar la escritura del archivo.

```bash
sudo tee /etc/nginx/sites-available/default > /dev/null << 'EOF'
# 1. Redirección de HTTP a HTTPS
server {
    listen 80;
    server_name api.grabbie.one operador.grabbie.one;
    return 301 https://$host$request_uri;
}

# 2. API y Video (HTTPS / Puerto 443)
server {
    listen 443 ssl;
    server_name api.grabbie.one;

    # Certificados SSL
    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;

    # Proxy a Node.js (API + WebSockets)
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Forwarded-Proto https;
    }

    # Video HLS
    location /hls {
        root /var/www/html;
        add_header Access-Control-Allow-Origin *;
        add_header Cache-Control no-cache;
        types {
            application/vnd.apple.mpegurl m3u8;
            video/mp2t ts;
        }
    }
}

# 3. Operador Web (HTTPS / Puerto 443)
server {
    listen 443 ssl;
    server_name operador.grabbie.one;

    ssl_certificate /etc/ssl/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;

    root /var/www/operador;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF
```

### Paso 4: Aplicación y Verificación

```bash
# Eliminar configuraciones conflictivas antiguas
sudo rm -f /etc/nginx/sites-enabled/grabbie-api

# Reiniciar Nginx
sudo systemctl restart nginx

# Verificación final de puertos (Debe mostrar *:443)
sudo ss -tulpn | grep nginx
```

---

## 3. Actualización del State of the Art (SOTA)

El proyecto ha evolucionado de una prueba de concepto local a una arquitectura de producción segura y distribuida.

### 🏗️ Arquitectura Actual (v2.1)

1.  **Seguridad Perimetral (Cloudflare):**
    *   Modo SSL: **Full**.
    *   Protección DDoS y CDN activas.
    *   Enrutamiento DNS gestionado para `api` y `operador`.

2.  **Servidor de Aplicaciones (GCP VM):**
    *   **Nginx:** Actúa como terminador SSL y Reverse Proxy. Maneja el tráfico estático (Web Operador, Video HLS) y redirige el tráfico dinámico (API, WebSockets) a Node.js.
    *   **Node.js (PM2):** Ejecución persistente. Maneja lógica de negocio, conexión a Firestore y señalización de WebSockets.
    *   **Firewall:** Reglas `default-allow-https` (443) y `allow-srt-9000` (9000 UDP) activas.

3.  **Frontend (Operador):**
    *   Alojado en `/var/www/operador`.
    *   Conexión segura vía `wss://` (WebSocket Secure) a través de Cloudflare.
    *   Reproducción de video HLS segura (`https://`).

### ✅ Estado de Servicios

*   **API Endpoint:** `https://api.grabbie.one` (Status: Online)
*   **Operador Dashboard:** `https://operador.grabbie.one` (Status: Online)
*   **Video Stream:** `srt://34.55.59.16:9000` -> HLS (Status: Ready)

---

**Próximos Pasos Recomendados:**
1.  Implementar rotación de logs en PM2 (`pm2 install pm2-logrotate`).
2.  Restringir CORS en `server.js` únicamente a los dominios `grabbie.one`.
3.  Automatizar el despliegue del frontend mediante un script CI/CD simple o Git Hooks en el servidor.