import React, { useEffect, useRef, useState } from 'react';
import './App.css'; // Pastikan CSS lamamu tetap ada

export default function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [processedImage, setProcessedImage] = useState(null);
  const [telemetry, setTelemetry] = useState({ offset: 0, curvature: 0, fps: 0, alert: "OK" });

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const loopRef = useRef(null);

  useEffect(() => {
    // 1. Minta Izin & Nyalakan Kamera Belakang HP (Resolusi dikecilkan ke 640x360 agar upload ringan)
    const setupCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment", width: 640, height: 360 } 
        });
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
      } catch (err) {
        console.error("Gagal akses kamera:", err);
        alert("Mohon izinkan akses kamera di browser HP Anda!");
      }
    };
    setupCamera();

    // 2. Hubungkan WebSocket
    // NANTI SAAT DEPLOY: Ubah URL ini menjadi URL Railway kamu, contoh: "wss://laneguard-backend.up.railway.app/ws/stream"
    const wsUrl = "ws://localhost:8000/ws/stream"; 
    
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("✅ WebSocket Terhubung!");
      setIsConnected(true);

      // KUNCI LOW-LATENCY: Ambil foto dari kamera dan kirim tiap 150ms
      loopRef.current = setInterval(() => {
        sendFrameToBackend();
      }, 150);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      // Tampilkan gambar yang sudah diproses AI
      setProcessedImage(`data:image/jpeg;base64,${data.image}`);
      // Update angka di dashboard
      setTelemetry({
        offset: data.offset,
        curvature: data.curvature,
        fps: data.fps,
        alert: data.alert
      });
    };

    ws.onclose = () => {
      console.log("❌ WebSocket Terputus");
      setIsConnected(false);
      clearInterval(loopRef.current);
    };

    return () => {
      clearInterval(loopRef.current);
      ws.close();
    };
  }, []);

  const sendFrameToBackend = () => {
    if (!videoRef.current || !canvasRef.current || !wsRef.current) return;
    if (wsRef.current.readyState !== WebSocket.OPEN) return;

    const canvas = canvasRef.current;
    const video = videoRef.current;
    const context = canvas.getContext('2d');

    // Salin frame video saat ini ke canvas
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    // Ubah jadi Base64 (Kompresi 50% biar upload dari HP tidak lag)
    const fullBase64 = canvas.toDataURL('image/jpeg', 0.5); 
    const base64Data = fullBase64.split(',')[1]; // Buang headernya

    // Tembak ke server Python
    wsRef.current.send(JSON.stringify({ image: base64Data }));
  };

  return (
    <div className="dashboard-container">
      <div className="header">
        <h1>LaneGuard Mobile HUD</h1>
        <span className={isConnected ? "badge-active" : "badge-offline"}>
          {isConnected ? "🟢 System Active" : "🔴 Disconnected"}
        </span>
      </div>

      <div className="main-content">
        {/* ELEMEN TERSEMBUNYI: Kamera asli HP yang memonitor jalan */}
        <div style={{ display: 'none' }}>
          <video ref={videoRef} autoPlay playsInline muted width="640" height="360" />
          <canvas ref={canvasRef} width="640" height="360" />
        </div>

        {/* LAYAR UTAMA: Menampilkan video balasan dari Python yang sudah ada HUD-nya */}
        <div className="video-feed">
          {processedImage ? (
            <img src={processedImage} alt="Processed LaneGuard Feed" style={{ width: '100%', borderRadius: '8px' }} />
          ) : (
            <div className="video-placeholder">Waiting for Camera & Server...</div>
          )}
        </div>

        {/* WIDGET METRIK (Kamu bisa sesuaikan styling-nya dengan CSS lamamu) */}
        <div className="telemetry-panel">
          <div className="metric">
            <h3>Status</h3>
            <h2 style={{ color: telemetry.alert === 'DEPARTURE' ? '#ff4d4d' : telemetry.alert === 'DRIFT' ? '#ffcc00' : '#00e676' }}>
              {telemetry.alert}
            </h2>
          </div>
          <div className="metric">
            <h3>Lateral Offset</h3>
            <h2>{telemetry.offset} m</h2>
          </div>
          <div className="metric">
            <h3>Curvature</h3>
            <h2>{telemetry.curvature === 9999 ? "Straight" : `${telemetry.curvature} m`}</h2>
          </div>
        </div>
      </div>
    </div>
  );
}