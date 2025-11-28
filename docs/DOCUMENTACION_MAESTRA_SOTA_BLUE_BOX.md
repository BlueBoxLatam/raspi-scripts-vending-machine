# 📘 Blue Box Vending - Documentación Maestra de Estado del Arte (SOTA)

**Versión del Documento:** 1.0  
**Fecha de Actualización:** 20 de Noviembre de 2025  
**Estado del Proyecto:** Backend Operativo / Fase de Integración de Frontend

## 1. Resumen Ejecutivo y Vision SOTA

El proyecto Blue Box Vending ha superado exitosamente la fase de prototipo de conectividad (IoT & Backend). Actualmente, contamos con una infraestructura híbrida funcional que conecta el mundo físico (Raspberry Pi) con la nube (GCP) mediante protocolos de baja latencia.

**Logro Clave Actual:**  
Se ha logrado una transmisión de video estable (SRT) y una comunicación de datos bidireccional (HTTP/WebSockets) entre una Raspberry Pi 3 y una VM en Google Cloud, validando usuarios contra Firebase Firestore en tiempo real.

## 🏢 Arquitectura Actual

*   **Edge (Heladera):** Raspberry Pi 3 ejecutando un script orquestador en Python (NFC + Video + GPIO).
*   **Cloud Core (GCP VM):** Servidor Ubuntu actuando como "Cerebro Central".
*   **IP Pública:** 34.55.59.16
*   **Puerto 3000 (TCP):** API Node.js + Socket.IO para lógica de negocio y tiempo real.
*   **Puerto 9000 (UDP):** Receptor FFmpeg para streaming de video SRT.
*   **Database:** Google Firestore (NoSQL) estructurada para escalabilidad multi-colegio.

## 2. Bitácora de Desarrollo: Errores y Soluciones (Knowledge Base)

Registro de obstáculos críticos superados para referencia futura.

| Feature | Estado | Error Encontrado | Solución Implementada |
| :--- | :--- | :--- | :--- |
| **NFC (PN532)** | ✅ Estable | Librería `adafruit_pn532` no detectada en entornos de prueba. | Implementación de modo "Mock/Simulación" automático si falla el import de hardware. |
| **Streaming Video** | ✅ Estable | Video corrupto, RCV-DROPPED, duración cortada (22s vs 30s). | 1. Protocolo SRT con latency=2000000 (2s buffer).<br>2. Bitrate bajado a 1000k (1Mbps).<br>3. FPS ajustados a 10.<br>4. Aumento de duración en Python a 40s para compensar buffer. |
| **Conectividad VM** | ✅ Estable | Puerto 3000/9000 bloqueado. | Creación de reglas de Firewall VPC en GCP (`allow-bluebox-api-3000`, `allow-srt-9000`) permitiendo 0.0.0.0/0. |
| **Servidor Node** | ✅ Estable | Error `EADDRINUSE :::3000`. | Uso de `sudo fuser -k 3000/tcp` para matar procesos zombies antes de reiniciar. |
| **Arquitectura** | ✅ Migrado | Latencia alta en Cloud Functions (Cold start). | Migración de lógica a servidor dedicado Node.js en VM (Compute Engine). |

## 3. Estrategias de Optimización (Nuevas Implementaciones)

Para la siguiente fase, se han aprobado las siguientes mejoras arquitectónicas para reducir tiempos de espera y mejorar la UX.

### 3.1. Modo "Streaming Persistente" (Optimización de Receso)
*   **Problema:** El Handshake SRT tarda 3-5 segundos por cada alumno. En un recreo de 15 mins, esto es inaceptable.
*   **Solución:**
    *   **Inicio del Recreo:** La RPi inicia el stream UNA VEZ. Se mantiene activo los 15 minutos.
    *   **Transacción:** Cuando el alumno pasa la tarjeta, no se negocia video (ya existe). Solo se valida saldo (200ms) y se abre la puerta.
    *   **Clipping en Nube:** El servidor Node.js registra `Timestamp_Inicio` (Apertura) y `Timestamp_Fin` (Cierre). Luego, un proceso FFmpeg en la VM corta ese fragmento del video maestro y lo guarda como evidencia individual.

### 3.2. Handshake Inteligente (Lectura de Logs)
*   **Problema:** Abrir la puerta antes de que el video sea visible para el operador.
*   **Solución:**
    *   Python leerá el stderr de FFmpeg.
    *   La puerta (GPIO) SOLO se abrirá cuando FFmpeg reporte bitrate > 0 (confirmación de flujo de datos).

### 3.3. Sensor Magnético (Duración Dinámica)
*   **Problema:** Grabar 40 segundos fijos es ineficiente si el alumno tarda 5 segundos.
*   **Solución:**
    *   Instalar sensor Reed Switch en GPIO.
    *   **Lógica:** Grabar hasta que `Sensor == CERRADO` + Buffer de seguridad (5s).

### 3.4. Frontend de Baja Latencia
*   **Estrategia:**
    *   **Hosting:** Firebase Hosting (para entrega rápida de estáticos HTML/JS).
    *   **Data:** WebSockets (`socket.io-client`) conectando directamente a la IP de la VM (34.55.59.16:3000), evitando el "salto" extra de pasar por Firebase Functions para la señalización en vivo.

## 4. Estructura de Base de Datos (Firestore Schema)

Esta estructura es definitiva y debe respetarse estrictamente para garantizar la integridad de los datos y futuras funcionalidades de ML.

### 🏫 Colección: schools
Datos institucionales.
*   **ID Documento:** `{schoolId}` (ej. `test_school`)
*   **Campos:**
    *   `name` (string): Nombre oficial.
    *   `address` (string): Dirección física.
    *   `contactEmail` (string): Email administrativo.
    *   `status` (string): `active`, `trial`, `inactive`.
    *   `contract_start_date` (timestamp).

### 🤖 Colección: vendingMachines
Inventario de hardware físico.
*   **ID Documento:** `{machineId}` (ej. `vm-school-001`)
*   **Campos:**
    *   `schoolId` (string): Referencia a colección `schools`.
    *   `location` (string): "Patio Principal".
    *   `status` (string): `Active`, `Maintenance`, `Offline`.
    *   `model` (string): "BlueBox-V1".
    *   `rpiIpAddress` (string): IP local (para mantenimiento remoto).
    *   `streamEndpoint` (string): IP de la VM destino.
    *   `isDoorLocked` (boolean).

### 🎓 Colección: students
Identidad y saldo. Lectura crítica de alta velocidad.
*   **ID Documento:** `{nfcUid}` (Formato con dos puntos: `53:CD:F5:58:A2:00:01`)
*   **Campos:**
    *   `name` (string): Nombre de pila.
    *   `paternalSurname` (string): Apellido paterno.
    *   `maternalSurname` (string): Apellido materno.
    *   `course` (string): Grado/Curso (ej. "1ero Secundaria A").
    *   `balance` (number): Saldo actual en moneda local. **CRÍTICO**.
    *   `status` (string): `active` (permite compra), `suspended` (deniega), `lost_card`.
    *   `emailStudent` (string).
    *   `parentId` (string): ID del padre en colección `users`.
    *   `homeSchoolId` (string): Referencia a `schools`.
    *   `nfcTagIssueDate` (timestamp).
    *   `lastTransactionDate` (timestamp).

### 🛒 Colección: products
Catálogo global de ítems.
*   **ID Documento:** `{sku}` (ej. `SAND-POLLO-01`)
*   **Campos:**
    *   `name` (string): Nombre corto para UI.
    *   `category` (string): "Sandwiches", "Bebidas".
    *   `price` (number): Precio base.
    *   `sku` (string): Igual al ID.
    *   `description` (string).
    *   `productImageUrl` (string): URL a Cloud Storage.
    *   `nutritionalInfo` (map): `{ calories: number, protein: string, ... }`.
    *   `stockAlertThreshold` (number): Nivel para alerta.
    *   `tags` (array): `["pollo", "fresco"]`.
    *   `status` (string): `Available`, `Out_of_Stock`.

### 📦 Colección: inventory
Tabla pivote: Cuánto hay de qué en dónde.
*   **ID Documento:** `{locationId}_{productId}` (ej. `vm-school-001_SAND-POLLO-01`)
*   **Campos:**
    *   `locationId` (string): ID de la máquina o bodega.
    *   `productId` (string): SKU del producto.
    *   `quantity` (number): Cantidad física real. Se actualiza atómicamente.
    *   `lastRestock` (timestamp).
    *   `initialCapacity` (number).

### 🧾 Colección: transactions
Historial financiero y de auditoría.
*   **ID Documento:** Auto-generado por Firestore.
*   **Campos:**
    *   `timestamp` (serverTimestamp): Hora exacta.
    *   `type` (string): `purchase` (compra heladera), `recharge` (recarga saldo).
    *   `totalAmount` (number): Monto deducido.
    *   `studentId` (string): UID del alumno.
    *   `machineId` (string).
    *   `schoolId` (string).
    *   `items` (array of maps): `[{ productId: "...", name: "...", quantity: 1, unitPrice: 10, lineTotal: 10 }]`
    *   `paymentMethod` (string): `balance` (para heladera), `stripe/qr` (para recargas).
    *   `videoId` (string): Nombre del archivo MP4 en Cloud Storage.
    *   `auditStatus` (string): `pending`, `validated`.

## 5. Roadmap & Siguientes Pasos (Priorizados)

El desarrollo debe seguir este orden estricto para evitar dependencias rotas.

### 🔴 Prioridad Alta (Infraestructura Visual)
*   **Transcodificación HLS (VM):**
    *   Configurar servicio `systemd` para FFmpeg Listener persistente.
    *   **Salida:** Archivos `.m3u8` y `.ts` en `/var/www/html/hls`.
*   **Servidor Web (VM):**
    *   Configurar NGINX para servir la carpeta HLS con cabeceras CORS correctas (`Access-Control-Allow-Origin: *`).
    *   Validar reproducción en VLC vía HTTP (`http://34.55.59.16/hls/stream.m3u8`).

### 🟡 Prioridad Media (Interfaz Operador)
*   **Desarrollo Frontend (React/Vite):**
    *   Integrar cliente `socket.io-client`.
    *   Crear vista "Dashboard" que escuche el evento `student_scanned`.
    *   Integrar reproductor `hls.js` o `video.js`.
*   **Despliegue Frontend:**
    *   Build del proyecto.
    *   Deploy en Firebase Hosting.

### 🟢 Prioridad Baja (Optimización y Cierre)
*   **Lógica de "Checkout":**
    *   Endpoint en Node.js para recibir la lista de productos seleccionados por el operador.
    *   Ejecución de transacción atómica en Firestore (Resta Saldo + Resta Inventario + Crea Transacción).
*   **Sistema de Grabación y Subida (Vertex AI Prep):**
    *   Script en VM para guardar MP4 localmente.
    *   Trigger para subir a Cloud Storage al finalizar la compra.

## 6. Información Técnica de Referencia

*   **IP Externa VM:** 34.55.59.16
*   **Colores Corporativos:**
    *   Azul Oscuro: `#223D73`
    *   Azul Medio: `#36ABD9`
    *   Naranja Intenso: `#F2921D`
*   **Hardware Crítico:**
    *   Cámara: `/dev/video0` (Logitech C930e)
    *   NFC: I2C (SDA/SCL) PN532
    *   Relé: GPIO 17 (BCM)

---
*Este documento debe ser proporcionado al inicio de cualquier sesión de desarrollo con un Asistente de IA para garantizar la continuidad del contexto.*
