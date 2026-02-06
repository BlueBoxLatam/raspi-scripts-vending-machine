const fs = require('fs');
const path = require('path');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const admin = require('firebase-admin');
const cors = require('cors');

// --- CONFIGURACIÓN ---
const HLS_DIR = '/var/www/html/hls'; // Directorio donde FFmpeg guarda el video (.m3u8 y .ts)
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

const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: "*", methods: ["GET", "POST"] }
});

// --- VARIABLES DE ESTADO ---
let currentSession = null; 
let videoWatcher = null;
let isStreamActive = false; // Estado global: ¿La Raspberry está transmitiendo video?

// --- FUNCIONES UTILITARIAS ---

// A. Limpieza de Caché de Video
// En v2, solo limpiamos si el stream se apaga (force=true) para no romper la continuidad
const cleanVideoCache = (force = false) => {
    if (!force) return;

    try {
        if (fs.existsSync(HLS_DIR)) {
            const files = fs.readdirSync(HLS_DIR);
            for (const file of files) {
                if (file.endsWith('.m3u8') || file.endsWith('.ts')) {
                    fs.unlinkSync(path.join(HLS_DIR, file));
                }
            }
            console.log("🧹 [LIMPIEZA] Caché de video eliminado (Stream Offline).");
        }
    } catch (err) {
        console.error("⚠️ Error limpiando video:", err);
    }
};

// B. Vigilante de Archivos de Video (Video Watcher)
const startVideoWatcher = (sessionId) => {
    // Si había un watcher anterior, lo cerramos para evitar duplicados
    if (videoWatcher) {
        videoWatcher.close();
        videoWatcher = null;
    }
    
    // Función de verificación interna
    const verify = () => {
        // Verificamos si la sesión sigue activa y aún no ha sido verificada
        if (currentSession && currentSession.id === sessionId && !currentSession.videoVerified) {
            const now = Date.now();
            const latency = now - currentSession.scanStartTime;
            console.log(`🎥 [CONFIRMADO] Video activo para sesión. Latencia: ${latency}ms`);
            
            currentSession.videoVerified = true;
            
            // 1. Avisar al Operador (Frontend) que ya puede ver al alumno
            io.to('operator_room').emit('student_scanned', {
                uid: currentSession.studentId,
                name: currentSession.studentName,
                balance: currentSession.studentBalance,
                sessionId: sessionId,
                latencyMs: latency
            });

            // 2. Avisar a la Raspberry Pi que abra la puerta
            io.emit('server_verified_video', { 
                machineId: currentSession.machineId,
                authorized: true
            });
            
            // Ya cumplimos, cerramos el watcher
            if (videoWatcher) {
                videoWatcher.close();
                videoWatcher = null;
            }
        }
    };

    // ESTRATEGIA DE VERIFICACIÓN:
    
    // 1. Check Inmediato (Fast-Track): 
    // Si el stream ya es continuo, los archivos ya existen y son recientes.
    try {
        const m3u8Path = path.join(HLS_DIR, 'stream.m3u8');
        if (fs.existsSync(m3u8Path)) {
            const stats = fs.statSync(m3u8Path);
            const diff = (Date.now() - stats.mtimeMs) / 1000;
            
            // Si el archivo playlist se modificó hace menos de 4 segundos, el stream está vivo.
            if (diff < 4) { 
                console.log("⚡ [FAST-TRACK] Stream continuo detectado (Video ya estaba corriendo).");
                verify();
                return; // Salimos, no hace falta watcher
            }
        }
    } catch(e) { 
        console.log("No hay stream previo activo."); 
    }

    // 2. Check Lento (Watchdog):
    // Si no estaba corriendo (primer alumno del recreo), esperamos a que FFmpeg cree los archivos.
    console.log("👀 [VIGILANCIA] Esperando que FFmpeg genere nuevos segmentos...");
    try {
        videoWatcher = fs.watch(HLS_DIR, (eventType, filename) => {
            if (filename && (filename.endsWith('.ts') || filename.endsWith('.m3u8'))) {
                verify();
            }
        });
    } catch (err) {
        console.error("Error iniciando watcher:", err);
    }
};

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
  // Este evento es CRÍTICO para la lógica v2
  socket.on('stream_status_change', (data) => {
      console.log(`📡 Stream Status (${data.machineId}): ${data.status.toUpperCase()}`);
      
      const wasActive = isStreamActive;
      isStreamActive = (data.status === 'online');
      
      // Si pasa de Online -> Offline, limpiamos caché para ahorrar espacio
      if (wasActive && !isStreamActive) {
          cleanVideoCache(true); 
      }

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

    // Iniciar verificación de video (puede ser instantánea o demorar unos segundos)
    startVideoWatcher(sessionId);

    // Responder a la Raspberry Pi para que espere
    res.status(200).json({
      authorized: true,
      action: "START_STREAM_ONLY", // Instrucción específica v2
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

    // Limpieza de sesión (Pero NO de video, para que siga fluido)
    currentSession = null;
    if(videoWatcher) {
        videoWatcher.close();
        videoWatcher = null;
    }
    
    res.status(200).json({ success: true });

  } catch (error) {
    console.error("🔥 Error checkout:", error.message);
    res.status(500).json({ error: error.message });
  }
});

// --- INICIAR SERVIDOR ---
server.listen(PORT, () => {
  console.log(`🚀 Blue Box Server v2 (Continuous Mode) corriendo en puerto ${PORT}`);
});