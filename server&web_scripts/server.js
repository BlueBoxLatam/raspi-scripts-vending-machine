const fs = require('fs');
const path = require('path');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const admin = require('firebase-admin');
const cors = require('cors');

// 1. Inicializar Firebase
// Asegúrate de tener el archivo serviceAccountKey.json en la misma carpeta
const serviceAccount = require('./serviceAccountKey.json');

admin.initializeApp({
  credential: admin.credential.cert(serviceAccount)
});

const db = admin.firestore();

// 2. Configurar Servidor Express y Socket.IO
const app = express();
app.use(cors());
app.use(express.json()); // Para entender JSON

const server = http.createServer(app);
const io = new Server(server, {
  cors: {
    origin: "*", 
    methods: ["GET", "POST"]
  }
});

// Estructura para manejar sesiones pendientes y sockets de RPi
const pendingSessions = new Map();

// --- WebSockets (Tiempo Real) ---
io.on('connection', (socket) => {
  console.log('🔌 Nuevo cliente conectado:', socket.id);
  
  socket.on('join_operator', () => {
    socket.join('operator_room');
    console.log(`User ${socket.id} joined operator_room`);
  });

  // La RPi se reporta lista con su session ID
  socket.on('rpi_ready', ({ sessionId }) => {
    console.log(`[HANDSHAKE] RPi reportada para sesión: ${sessionId}`);
    if (pendingSessions.has(sessionId)) {
      const session = pendingSessions.get(sessionId);
      session.rpiSocketId = socket.id; // Guardamos el socket ID
      pendingSessions.set(sessionId, session);
    }
  });

  socket.on('disconnect', () => {
    console.log('❌ Cliente desconectado:', socket.id);
  });
});

// ==========================================
//              UTILIDADES
// ==========================================
function cleanVideoCache() {
  const hlsDir = '/var/www/html/hls'; 
  try {
    if (fs.existsSync(hlsDir)) {
      const files = fs.readdirSync(hlsDir);
      for (const file of files) {
        if (file.endsWith('.m3u8') || file.endsWith('.ts')) {
          fs.unlinkSync(path.join(hlsDir, file));
        }
      }
      console.log("🧹 Archivos de video antiguos eliminados.");
    }
  } catch (err) {
    console.error("⚠️ Error limpiando video:", err);
  }
}

// ==========================================
//              RUTAS API
// ==========================================

// 1. RUTA DE DIAGNÓSTICO
app.post('/api/transaction', (req, res) => {
  console.log("\n🧪 [DIAGNOSTIC] Petición de prueba recibida.");
  res.status(200).json({
    status: "ok",
    message: "Conexión RPi <-> Cloud exitosa",
    receivedData: req.body
  });
});

// 2. IDENTIFICACIÓN DE ESTUDIANTE (INICIO DE HANDSHAKE)
app.post('/api/identify-student', async (req, res) => {
  const { nfcUid, machineId } = req.body;
  const hlsDir = '/var/www/html/hls';

  console.log(`\n📡 RPi reporta tarjeta: ${nfcUid} en máquina: ${machineId}`);

  if (!nfcUid) {
    return res.status(400).json({ error: 'UID no proporcionado' });
  }

  try {
    const studentRef = db.collection('students').doc(nfcUid);
    const doc = await studentRef.get();

    if (!doc.exists || doc.data().status !== 'active') {
      console.log('⚠️ Estudiante no encontrado o inactivo.');
      return res.status(404).json({ error: 'Estudiante no registrado o inactivo' });
    }
    
    const studentData = doc.data();
    console.log(`✅ Identificado: ${studentData.name} - Saldo: ${studentData.balance}`);

    // --- INICIO DEL NUEVO FLUJO ---
    // 1. Limpiar caché de video ANTES de hacer nada.
    cleanVideoCache();

    // 2. Crear sesión en Firestore
    const sessionId = `session_${machineId}`;
    const sessionRef = db.collection('active_sessions').doc(sessionId);
    await sessionRef.set({
      studentId: nfcUid,
      studentName: studentData.name,
      studentBalance: studentData.balance,
      openedAt: admin.firestore.FieldValue.serverTimestamp(),
      status: "pending_video", // Nuevo estado
      machineId: machineId
    });

    // 3. Avisar al Frontend que se está conectando un cliente.
    io.to('operator_room').emit('client_connecting', { machineId });

    // 4. Guardar datos de la sesión para el handshake
    pendingSessions.set(sessionId, {
        studentData: {
            uid: nfcUid,
            name: studentData.name,
            lastname: `${studentData.paternalSurname || ''} ${studentData.maternalSurname || ''}`,
            balance: studentData.balance,
            sessionId: sessionId
        },
        rpiSocketId: null, // Aún no lo sabemos
        status: 'waiting_for_stream'
    });

    // 5. Configurar el Watcher para el archivo de video
    const watcher = fs.watch(hlsDir, (eventType, filename) => {
      if (filename === 'stream.m3u8') {
          console.log(`[HANDSHAKE] ✅ Archivo de stream detectado para ${sessionId}!`);
          
          const session = pendingSessions.get(sessionId);
          if (session && session.status === 'waiting_for_stream') {
              
              // A. Avisar al Frontend para que inicie la UI y el video
              io.to('operator_room').emit('video_verified_start_session', session.studentData);
              console.log(`[HANDSHAKE] -> Avisando al frontend.`);

              // B. Enviar orden de abrir puerta a la RPi correcta
              if (session.rpiSocketId) {
                  io.to(session.rpiSocketId).emit('open_door', { machineId });
                  console.log(`[HANDSHAKE] -> Enviando orden de abrir a ${session.rpiSocketId}`);
              } else {
                  console.error(`[HANDSHAKE] ERROR: No se encontró socket para ${sessionId}`);
              }
              
              // C. Actualizar estado de la sesión
              session.status = 'completed';
              db.collection('active_sessions').doc(sessionId).update({ status: 'live_monitoring' });
          }
          
          // Limpiar
          watcher.close();
          setTimeout(() => pendingSessions.delete(sessionId), 30000); // Limpia la sesión pendiente después de un tiempo
      }
    });

    // 6. Responder a la RPi para que inicie el stream
    res.status(200).json({
      status: 'PENDING_VIDEO',
      sessionId: sessionId // La RPi necesita saber su ID de sesión
    });

  } catch (error) {
    console.error('🔥 Error en identify-student:', error);
    res.status(500).json({ error: 'Error interno del servidor' });
  }
});

// 3. PROCESAR COBRO (CHECKOUT)
app.post('/api/checkout', async (req, res) => {
  const { sessionId, items, total } = req.body; 

  console.log(`\n💵 Procesando cobro para sesión: ${sessionId}`);
  console.log(`   Items: ${items.length} | Total: Bs ${total}`);

  try {
    // A. Obtener datos de la sesión actual
    const sessionRef = db.collection('active_sessions').doc(sessionId);
    const sessionDoc = await sessionRef.get();

    if (!sessionDoc.exists) {
      return res.status(404).json({ error: "Sesión no encontrada o ya cerrada" });
    }

    const sessionData = sessionDoc.data();
    const studentId = sessionData.studentId;

    // B. Transacción Atómica (Cobro + Historial + Limpieza)
    const studentRef = db.collection('students').doc(studentId);
    
    await db.runTransaction(async (t) => {
      const studentDoc = await t.get(studentRef);
      const currentBalance = studentDoc.data().balance;
      const newBalance = currentBalance - total;

      if (newBalance < 0) {
        throw new Error("Saldo insuficiente");
      }

      // 1. Crear el recibo en "transactions"
      const transactionRef = db.collection('transactions').doc();
      t.set(transactionRef, {
        timestamp: admin.firestore.FieldValue.serverTimestamp(),
        machineId: sessionData.machineId,
        studentId: studentId,
        studentName: sessionData.studentName,
        items: items,
        totalAmount: total,
        type: "purchase"
      });

      // 2. Descontar saldo al alumno
      t.update(studentRef, { balance: newBalance });

      // 3. Borrar la sesión activa
      t.delete(sessionRef); 
    });

    console.log("✅ Cobro exitoso. Transacción guardada.");

    // Limpieza de video
    cleanVideoCache();

    // C. Avisar al Frontend (Monitor)
    io.to('operator_room').emit('transaction_complete', {
      success: true,
      studentName: sessionData.studentName,
      total: total
    });

    // D. COMANDO CRÍTICO: CERRAR PUERTA RASPBERRY PI
    console.log(`🔒 Enviando orden de cierre a: ${sessionData.machineId}`);
    io.emit('force_remote_close', { 
      machineId: sessionData.machineId 
    });

    res.status(200).json({ success: true });

  } catch (error) {
    console.error("🔥 Error en checkout:", error);
    res.status(500).json({ error: error.message });
  }
});

// Iniciar servidor
const PORT = 3000;
server.listen(PORT, () => {
  console.log(`\n🚀 Servidor Blue Box MVP escuchando en puerto ${PORT}`);
  console.log(`   Rutas Activas:`);
  console.log(`   - POST /api/transaction (Diag)`);
  console.log(`   - POST /api/identify-student (Inicio)`);
  console.log(`   - POST /api/checkout (Final)`);
});