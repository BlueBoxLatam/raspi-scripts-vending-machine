const express = require('express');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = 3001; // Puerto ALT para no chocar con producción (3000)

app.use(cors());
app.use(express.static(path.join(__dirname, 'server&web_scripts/operador/public')));

// Ruta para servir el HTML de prueba
app.get('/webrtc', (req, res) => {
    res.sendFile(path.join(__dirname, '../../server&web_scripts/operador/public/index_wrtcv1.html'));
});

// Proxy simple para la API de MediaMTX (si fuera necesario por CORS)
// Por ahora asumiremos que MediaMTX tiene CORS habilitado.

app.listen(PORT, () => {
    console.log(`🚀 Servidor de Prueba WebRTC corriendo en: http://localhost:${PORT}/webrtc`);
});
