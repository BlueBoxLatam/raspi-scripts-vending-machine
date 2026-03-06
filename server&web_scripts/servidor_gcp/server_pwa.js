const fs = require('fs');
const path = require('path');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const admin = require('firebase-admin');
const cors = require('cors');

// --- CONFIGURACIÓN ---
const PORT = 3000;

// 1. Inicializar Firebase
const serviceAccount = require('./serviceAccountKey.json');
admin.initializeApp({
    credential: admin.credential.cert(serviceAccount)
});
const db = admin.firestore();
const auth = admin.auth(); // Added for token verification

// 2. Configurar Express y Socket.IO
const app = express();
app.use(express.json());
app.use(cors({ origin: true }));

// Servir archivos estáticos del dashboard (Operador)
app.use(express.static(path.join(__dirname, '../operador/public')));

const server = http.createServer(app);
const io = new Server(server, {
    cors: {
        origin: "*",
        methods: ["GET", "POST", "OPTIONS"],
        allowedHeaders: ["Content-Type", "Authorization"],
        credentials: false
    }
});

// --- VARIABLES DE ESTADO ---
let currentSession = null;
let currentQrToken = null; // Token rotativo actual
let qrRotationInterval = null;
const QR_ROTATION_MS = 30000; // Rotar QR cada 30 segundos

// --- HELPERS ---
function generateQrToken(machineId) {
    // Generar un token único: MACHINE_ID:TIMESTAMP:RANDOM
    const token = `${machineId}:${Date.now()}:${Math.floor(Math.random() * 1000)}`;
    currentQrToken = token;
    return token;
}

function startQrRotation() {
    if (qrRotationInterval) clearInterval(qrRotationInterval);

    // Generar primero
    const token = generateQrToken("vm_001");
    io.to('client_room').emit('update_qr', { token: token });

    // Rotar periodicamente
    qrRotationInterval = setInterval(() => {
        if (!currentSession) { // Solo rotar si no hay nadie comprando
            const newToken = generateQrToken("vm_001");
            console.log(`🔄 [QR] Token renovado: ${newToken.substring(0, 15)}...`);
            io.to('client_room').emit('update_qr', { token: newToken });
        }
    }, QR_ROTATION_MS);
}

// --- WEBSOCKETS ---
io.on('connection', (socket) => {

    // A. Conexión del Operador (Frontend)
    socket.on('join_operator', () => {
        socket.join('operator_room');
        console.log("👤 Operador conectado.");

        // Estado de Sesión (Persistencia Operador)
        if (currentSession) {
            const now = Date.now();
            const latency = now - currentSession.scanStartTime;
            socket.emit('student_scanned', {
                uid: currentSession.studentId,
                name: currentSession.studentName,
                balance: currentSession.studentBalance,
                sessionId: currentSession.id,
                latencyMs: latency
            });
        }
    });

    // B. Conexión del Cliente (Tablet)
    socket.on('join_client', () => {
        socket.join('client_room');
        console.log("🎓 Cliente (Tablet) conectado.");

        // Al conectar, enviarle el token actual o el estado de sesión
        if (currentSession) {
            socket.emit('client_welcome', {
                name: currentSession.studentName,
                balance: currentSession.studentBalance
            });
        } else {
            // Enviar token actual
            if (!currentQrToken) generateQrToken("vm_001");
            socket.emit('update_qr', { token: currentQrToken });
            socket.emit('client_reset');
        }
    });

    // C. Conexión Raspberry Pi
    socket.on('join_machine', (data) => {
        console.log(`🤖 Raspberry Pi conectada: ${data.id}`);
        socket.join('machine_room');
    });

    socket.on('stream_status_change', (data) => {
        console.log(`📹 Stream Status: ${data.status}`);
        io.to('operator_room').emit('stream_status_update', data);
    });

    socket.on('disconnect', () => {
        // console.log("Usuario desconectado");
    });
});

// --- API REST ---

// 1. Obtener Productos
app.get('/api/products', async (req, res) => {
    try {
        const snapshot = await db.collection('products').get();
        const products = [];
        snapshot.forEach(doc => {
            products.push({ id: doc.id, ...doc.data() });
        });
        res.json(products);
    } catch (error) {
        console.error("🔥 Error al obtener productos:", error);
        res.status(500).json({ error: "Error al obtener productos" });
    }
});

// 2. [NUEVO] Validar Acceso QR (Desde PWA)
app.post('/api/validate-qr-access', async (req, res) => {
    // 1. Verificar Headers de Auth (Firebase ID Token)
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return res.status(401).json({ error: 'Unauthorized' });
    }
    const idToken = authHeader.split('Bearer ')[1];

    const { qrToken, userId } = req.body;

    try {
        // A. Verificar Token de Usuario (Seguridad)
        const decodedToken = await auth.verifyIdToken(idToken);
        if (decodedToken.uid !== userId) {
            return res.status(403).json({ error: 'Identity mismatch' });
        }

        // B. Verificar Validez del QR
        if (qrToken !== currentQrToken) {
            return res.status(400).json({ error: 'QR Expirado o Inválido. Escanéalo de nuevo.' });
        }

        // C. Verificar si ya hay sesión
        if (currentSession) {
            return res.status(409).json({ error: 'Heladera ocupada. Intenta en unos segundos.' });
        }

        // D. Verificar Saldo en Firestore
        const userDoc = await db.collection('users').doc(userId).get();
        if (!userDoc.exists) throw new Error("Usuario no encontrado");

        const userData = userDoc.data();
        const balance = userData.points || 0;

        if (balance <= 0) {
            return res.status(402).json({ error: 'Saldo insuficiente' });
        }

        // --- ACCESO CONCEDIDO ---
        console.log(`✅ [ACCESO] PWA Autorizado: ${userData.name || userId} (${balance} pts)`);

        // INICIAR SESIÓN (Mismo flujo que antes con NFC)
        const machineId = "vm_001";
        const scanTime = Date.now();
        const sessionId = `session_${machineId}_${scanTime}`;

        currentSession = {
            id: sessionId,
            studentId: userId,
            studentName: userData.name || "Usuario App",
            studentBalance: balance,
            machineId: machineId,
            videoVerified: true, // Asumimos verificado porque pasó QR y Auth
            scanStartTime: scanTime,
            cart: [],
            total: 0
        };

        // 1. Notificar Raspberry Pi para abrir
        console.log("🔓 Enviando comando de apertura a Raspberry Pi...");
        io.emit('server_remote_unlock', { machineId: machineId, authorized: true });

        // 2. Notificar Tablet (Bienvenida)
        io.to('client_room').emit('client_welcome', {
            name: currentSession.studentName,
            balance: currentSession.studentBalance
        });

        // 3. Notificar Operador
        io.to('operator_room').emit('student_scanned', {
            uid: currentSession.studentId,
            name: currentSession.studentName,
            balance: currentSession.studentBalance,
            sessionId: sessionId,
            latencyMs: 100
        });

        // 4. Iniciar Watchdog Local
        resetSessionTimer();

        res.json({ success: true, message: 'Puerta Abierta' });

    } catch (error) {
        console.error("🔥 Error validando QR:", error);
        res.status(500).json({ error: 'Error interno de validación' });
    }
});

// --- TIME-OUT LOGIC (Copiado de v3) ---
const SESSION_TIMEOUT_MS = 5 * 60 * 1000;
let sessionTimeoutTimer = null;

function resetSessionTimer() {
    if (sessionTimeoutTimer) clearTimeout(sessionTimeoutTimer);

    sessionTimeoutTimer = setTimeout(() => {
        if (currentSession) {
            console.log(`⏰ [TIMEOUT] Sesión expirada.`);
            forceCloseSession("Timeout");
        }
    }, SESSION_TIMEOUT_MS);
}

function forceCloseSession(reason) {
    if (!currentSession) return;

    io.emit('force_remote_close', { machineId: currentSession.machineId });
    io.to('operator_room').emit('transaction_complete', { success: false, reason: reason });
    io.to('client_room').emit('client_reset'); // Esto debería volver a mostrar el QR

    // Generar nuevo QR inmediatamente
    const token = generateQrToken("vm_001");
    io.to('client_room').emit('update_qr', { token: token });

    currentSession = null;
}

// 3. Checkout (Adaptado para usar 'points' en users)
app.post('/api/checkout', async (req, res) => {
    const { sessionId, items, total } = req.body;

    try {
        if (!currentSession || currentSession.id !== sessionId) {
            return res.status(400).json({ error: "Sesión inválida" });
        }

        const userRef = db.collection('users').doc(currentSession.studentId);

        await db.runTransaction(async (t) => {
            const uDoc = await t.get(userRef);
            if (!uDoc.exists) throw new Error("Usuario no existe");

            const currentPoints = uDoc.data().points || 0;
            const newBal = currentPoints - total;
            if (newBal < 0) throw new Error("Saldo insuficiente");

            // Historial
            const txRef = db.collection('transactions').doc();
            t.set(txRef, {
                timestamp: admin.firestore.FieldValue.serverTimestamp(),
                sessionId,
                userId: currentSession.studentId,
                items,
                total,
                newBalance: newBal
            });

            // Update
            t.update(userRef, { points: newBal });
        });

        // Success
        io.emit('force_remote_close', { machineId: currentSession.machineId }); // Close Lock
        io.to('operator_room').emit('transaction_complete', { success: true, total });

        io.to('client_room').emit('client_purchase_summary', {
            total: total,
            newBalance: currentSession.studentBalance - total
        });

        // Cleanup
        currentSession = null;
        if (sessionTimeoutTimer) clearTimeout(sessionTimeoutTimer);

        // Regenerar QR para el siguiente
        setTimeout(() => {
            const token = generateQrToken("vm_001");
            io.to('client_room').emit('update_qr', { token: token });
        }, 5000); // 5s después del resumen

        res.json({ success: true });

    } catch (error) {
        console.error("🔥 Error checkout:", error);
        res.status(500).json({ error: error.message });
    }
});

// 4. Reset Manual
app.post('/api/reset-session', (req, res) => {
    forceCloseSession("Manual Reset");
    res.json({ success: true });
});

// --- INIT ---
startQrRotation(); // Start generating QRs
server.listen(PORT, () => {
    console.log(`🚀 Blue Box PWA Server running on port ${PORT}`);
});
