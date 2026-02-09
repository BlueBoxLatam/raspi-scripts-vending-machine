const fs = require('fs');
const path = require('path');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const admin = require('firebase-admin');
const cors = require('cors');

// --- CONFIGURACIÓN ---
// En v3 ya no usamos HLS_DIR ni Watcheo de archivos. La magia ocurre en MediaMTX.
const PORT = 3000;

// 1. Inicializar Firebase
// Asegúrate de tener el archivo 'serviceAccountKey.json' en la misma carpeta
const serviceAccount = require('./serviceAccountKey.json');
admin.initializeApp({
    credential: admin.credential.cert(serviceAccount)
});
const db = admin.firestore();

// 2. Configurar Express y Socket.IO
const app = express();
app.use(cors());
app.use(express.json());

// Servir archivos estáticos del dashboard (incluyendo indexv3.html)
// Asumimos que indexv3.html estará en ../operador/public
app.use(express.static(path.join(__dirname, '../operador/public')));

const server = http.createServer(app);
const io = new Server(server, {
    cors: { origin: "*", methods: ["GET", "POST"] }
});

// --- VARIABLES DE ESTADO ---
let currentSession = null;
let isStreamActive = false; // Estado global: ¿La Raspberry está transmitiendo video?

// --- WEBSOCKETS ---
io.on('connection', (socket) => {

    // A. Conexión del Operador (Frontend)
    socket.on('join_operator', () => {
        socket.join('operator_room');
        console.log("👤 Operador conectado.");
        // Al conectarse, le decimos inmediatamente si la cámara está prendida o apagada
        socket.emit('stream_status_update', { status: isStreamActive ? 'online' : 'offline' });
    });

    // B. Conexión de la Máquina (Raspberry Pi)
    socket.on('join_machine', (data) => {
        console.log(`🤖 Máquina conectada: ${data.id}`);
        socket.join('machine_room');
    });

    // C. Cambio de Estado del Stream (Desde Raspberry Pi)
    // Este evento es CRÍTICO. La Raspi manda 'online' cuando inicia FFmpeg/SRT.
    socket.on('stream_status_change', (data) => {
        console.log(`📡 Stream Status (${data.machineId}): ${data.status.toUpperCase()}`);

        const wasActive = isStreamActive;
        isStreamActive = (data.status === 'online');

        // Reenviar estado a TODOS los operadores conectados para que actualicen su UI
        io.to('operator_room').emit('stream_status_update', { status: data.status });
    });
});

// --- API REST ---

// 1. Obtener Productos
app.get('/api/products', async (req, res) => {
    try {
        const snapshot = await db.collection('products').get();
        const products = [];
        snapshot.forEach(doc => products.push({ id: doc.id, ...doc.data() }));
        res.json(products);
    } catch (error) { res.status(500).json({ error: "Error db" }); }
});

// 2. Identificar Estudiante (Inicio de Transacción)
// En v3, simplificamos la verificación. Si el socket dice que hay stream, confiamos.
app.post('/api/identify-student', async (req, res) => {
    const { nfcUid, machineId } = req.body;
    const scanTime = Date.now();

    console.log(`\n📡 [SCAN] Solicitud NFC: ${nfcUid} (Máquina: ${machineId})`);

    try {
        // Buscar usuario en Firestore
        const doc = await db.collection('students').doc(nfcUid).get();

        if (!doc.exists) {
            console.log("❌ Usuario desconocido");
            return res.status(404).json({ error: 'Usuario desconocido' });
        }

        const studentData = doc.data();

        // Notificar al operador que alguien escaneó (UI: "Cargando...")
        io.to('operator_room').emit('nfc_pending_video', {
            studentName: studentData.name,
            scanTime: scanTime
        });

        // Crear sesión temporal en memoria
        const sessionId = `session_${machineId}_${scanTime}`;
        currentSession = {
            id: sessionId,
            studentId: nfcUid,
            studentName: studentData.name,
            studentBalance: studentData.balance,
            machineId: machineId,
            videoVerified: false,
            scanStartTime: scanTime,
            cart: [],
            total: 0
        };

        // VERIFICACIÓN VIDEO V3 (Instantánea):
        // Si la máquina reportó 'online' por socket, asumimos que el video viaja por WebRTC.
        // Damos un pequeño delay de cortesía (1s) para asegurar que el playbck arranque en frontend.
        setTimeout(() => {
            console.log(`🎥 [CONFIRMADO] Asumiendo video WebRTC activo.`);

            currentSession.videoVerified = true;

            // 1. Avisar al Operador (Frontend) -> UI Venta
            io.to('operator_room').emit('student_scanned', {
                uid: currentSession.studentId,
                name: currentSession.studentName,
                balance: currentSession.studentBalance,
                sessionId: sessionId,
                latencyMs: 100 // Dummy value, real latency is measured in frontend
            });

            // 2. Avisar a la Raspberry Pi que abra la puerta
            io.emit('server_verified_video', {
                machineId: currentSession.machineId,
                authorized: true
            });
        }, 1000);

        // Responder a la Raspberry Pi para que espere confirmación (que llega en el setTimeout)
        res.status(200).json({
            authorized: true,
            action: "START_STREAM_ONLY",
            message: "Verificando video..."
        });

    } catch (error) {
        console.error('🔥 Error:', error);
        res.status(500).json({ error: 'Internal Error' });
    }
});

// 3. Checkout (Fin de Transacción)
app.post('/api/checkout', async (req, res) => {
    const { sessionId, items, total } = req.body;
    console.log(`\n💵 Cobrando sesión: ${sessionId} | Total: ${total}`);

    try {
        if (!currentSession || currentSession.id !== sessionId) {
            return res.status(400).json({ error: "Sesión inválida o expirada" });
        }

        // Transacción en Firestore (Atomicidad: Saldo y Registro)
        const studentRef = db.collection('students').doc(currentSession.studentId);

        await db.runTransaction(async (t) => {
            const sDoc = await t.get(studentRef);
            if (!sDoc.exists) throw new Error("Usuario no existe");

            const newBal = sDoc.data().balance - total;
            if (newBal < 0) throw new Error("Saldo insuficiente");

            // Registrar Historial
            const txRef = db.collection('transactions').doc();
            t.set(txRef, {
                timestamp: admin.firestore.FieldValue.serverTimestamp(),
                sessionId,
                studentId: currentSession.studentId,
                items,
                total,
                newBalance: newBal
            });

            // Actualizar Saldo
            t.update(studentRef, { balance: newBal });
        });

        console.log("✅ Cobro exitoso.");

        // 1. Mandar cerrar puerta a Raspberry Pi
        io.emit('force_remote_close', { machineId: currentSession.machineId });

        // 2. Avisar al Frontend éxito
        io.to('operator_room').emit('transaction_complete', { success: true, total });

        // Limpieza de sesión
        currentSession = null;

        res.status(200).json({ success: true });

    } catch (error) {
        console.error("🔥 Error checkout:", error.message);
        res.status(500).json({ error: error.message });
    }
});

// --- INICIAR SERVIDOR ---
server.listen(PORT, () => {
    console.log(`🚀 Blue Box Server v3 (WebRTC Edition) corriendo en puerto ${PORT}`);
});
