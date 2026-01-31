const fs = require('fs');
const path = require('path');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const admin = require('firebase-admin');
const cors = require('cors');

// --- CONFIGURACIÓN ---
const HLS_DIR = '/var/www/html/hls'; // Directorio donde FFmpeg guarda el video
const PORT = 3000;

// 1. Inicializar Firebase
const serviceAccount = require('./serviceAccountKey.json');
admin.initializeApp({
  credential: admin.credential.cert(serviceAccount)
});
const db = admin.firestore();

// 2. Configurar Express y Socket.IO
const app = express();
app.use(cors());
app.use(express.json());

const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: "*", methods: ["GET", "POST"] }
});

// --- VARIABLES DE ESTADO EN MEMORIA ---
// Usamos esto para evitar latencia de DB en el ciclo crítico de video
let currentSession = null; 
let videoWatcher = null;

// --- FUNCIONES UTILITARIAS ---

// A. Limpieza de Caché de Video (Borra .ts y .m3u8)
const cleanVideoCache = () => {
    try {
        if (fs.existsSync(HLS_DIR)) {
            const files = fs.readdirSync(HLS_DIR);
            for (const file of files) {
                if (file.endsWith('.m3u8') || file.endsWith('.ts')) {
                    fs.unlinkSync(path.join(HLS_DIR, file));
                }
            }
            console.log("🧹 [LIMPIEZA] Caché de video eliminado.");
        }
    } catch (err) {
        console.error("⚠️ Error limpiando video:", err);
    }
};

// B. Monitor de Video (El "Ojo" del Servidor)
const startVideoWatcher = (sessionId) => {
    console.log("👀 [VIGILANCIA] Esperando señal de video en disco...");
    
    // Si ya había un watcher, lo cerramos
    if (videoWatcher) videoWatcher.close();

    // Vigilamos la carpeta HLS
    videoWatcher = fs.watch(HLS_DIR, (eventType, filename) => {
        if (filename && (filename.endsWith('.ts') || filename.endsWith('.m3u8'))) {
            
            // ¡DETECTAMOS VIDEO!
            if (currentSession && currentSession.id === sessionId && !currentSession.videoVerified) {
                console.log(`🎥 [CONFIRMADO] Video detectado (${filename}). Autorizando apertura.`);
                
                currentSession.videoVerified = true;
                
                // 1. Avisar al Frontend (Muestra datos)
                io.to('operator_room').emit('student_scanned', {
                    uid: currentSession.studentId,
                    name: currentSession.studentName,
                    balance: currentSession.studentBalance,
                    sessionId: sessionId
                });

                // 2. Avisar a Raspberry Pi (Abre Puerta)
                io.emit('server_verified_video', { 
                    machineId: currentSession.machineId,
                    authorized: true
                });

                // Dejamos de vigilar para no saturar CPU
                if (videoWatcher) videoWatcher.close();
            }
        }
    });
};

// --- WEBSOCKETS ---
io.on('connection', (socket) => {
  console.log('🔌 Cliente conectado:', socket.id);
  
  // AL CONECTAR EL OPERADOR (Fix para F5)
  socket.on('join_operator', () => {
    socket.join('operator_room');
    console.log(`User ${socket.id} joined operator_room`);

    // Si hay una sesión activa en memoria, enviarla al nuevo socket
    if (currentSession && currentSession.videoVerified) {
        console.log("🔄 Restaurando sesión para operador reconectado.");
        socket.emit('student_scanned', {
            uid: currentSession.studentId,
            name: currentSession.studentName,
            balance: currentSession.studentBalance,
            sessionId: currentSession.id
        });
    }
  });
});

// ================= RUTAS API =================

// 1. IDENTIFICACIÓN (INICIO)
app.post('/api/identify-student', async (req, res) => {
  const { nfcUid, machineId } = req.body;
  console.log(`\n📡 Solicitud de acceso: ${nfcUid}`);

  try {
    // A. Validaciones
    const studentRef = db.collection('students').doc(nfcUid);
    const doc = await studentRef.get();

    if (!doc.exists) return res.status(404).json({ error: 'Estudiante no encontrado' });
    const studentData = doc.data();
    if (studentData.status !== 'active') return res.status(403).json({ error: 'Cuenta inactiva' });

    console.log(`✅ Usuario válido: ${studentData.name}. Preparando entorno...`);

    // B. LIMPIEZA PREVENTIVA (Anti-Caché)
    cleanVideoCache();

    // C. Crear Sesión en Memoria y BD
    const sessionId = `session_${machineId}_${Date.now()}`;
    
    currentSession = {
        id: sessionId,
        studentId: nfcUid,
        studentName: studentData.name,
        studentBalance: studentData.balance,
        machineId: machineId,
        videoVerified: false, // Importante: Aún no verificado
        cart: [],
        total: 0
    };

    // Guardar en Firestore (Opcional, para logs)
    await db.collection('active_sessions').doc(sessionId).set({
        ...currentSession,
        status: "waiting_for_video",
        createdAt: admin.firestore.FieldValue.serverTimestamp()
    });

    // D. INICIAR VIGILANCIA DE VIDEO
    startVideoWatcher(sessionId);

    // E. RESPONDER A RASPBERRY PI
    // Le decimos: "Usuario OK, empieza a transmitir, pero NO ABRAS todavía"
    res.status(200).json({
      authorized: true,
      action: "START_STREAM_ONLY", // Nueva instrucción clara
      message: "Esperando video para abrir puerta"
    });

  } catch (error) {
    console.error('🔥 Error:', error);
    res.status(500).json({ error: 'Error interno' });
  }
});

// 2. CHECKOUT (FINAL)
app.post('/api/checkout', async (req, res) => {
  const { sessionId, items, total } = req.body; 
  console.log(`\n💵 Cobrando sesión: ${sessionId} | Total: ${total}`);

  try {
    if (!currentSession || currentSession.id !== sessionId) {
        return res.status(400).json({ error: "Sesión no válida o expirada" });
    }

    // A. Transacción DB
    const studentRef = db.collection('students').doc(currentSession.studentId);
    await db.runTransaction(async (t) => {
      const sDoc = await t.get(studentRef);
      const newBal = sDoc.data().balance - total;
      if (newBal < 0) throw new Error("Saldo insuficiente");

      // Guardar historial
      const txRef = db.collection('transactions').doc();
      t.set(txRef, {
        timestamp: admin.firestore.FieldValue.serverTimestamp(),
        sessionId,
        studentId: currentSession.studentId,
        items,
        total,
        newBalance: newBal
      });

      // Actualizar saldo
      t.update(studentRef, { balance: newBal });
    });

    console.log("✅ Cobro exitoso.");

    // B. COMANDO DE CIERRE A RASPBERRY
    io.emit('force_remote_close', { machineId: currentSession.machineId });

    // C. CONFIRMACIÓN A FRONTEND
    io.to('operator_room').emit('transaction_complete', {
      success: true,
      studentName: currentSession.studentName,
      total: total
    });

    // D. LIMPIEZA FINAL
    cleanVideoCache(); // Borrar video inmediatamente
    currentSession = null; // Borrar sesión de memoria
    if(videoWatcher) videoWatcher.close();

    res.status(200).json({ success: true });

  } catch (error) {
    console.error("🔥 Error checkout:", error);
    res.status(500).json({ error: error.message });
  }
});

server.listen(PORT, () => {
  console.log(`🚀 Blue Box Server v2 corriendo en puerto ${PORT}`);
});