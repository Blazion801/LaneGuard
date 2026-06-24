import React, { useState, useEffect, useRef } from "react";
import {
  AlertTriangle,
  Car,
  Navigation,
  ShieldCheck,
  Sun,
  Moon,
  Activity 
} from "lucide-react";

export default function App() {
  // --- STATE UI & DESAIN ---
  const [isDarkMode, setIsDarkMode] = useState(true);
  
  // --- STATE SYSTEM & AI ---
  const [isConnected, setIsConnected] = useState(false);
  const [processedImage, setProcessedImage] = useState(null);
  const [telemetry, setTelemetry] = useState({ offset: 0, curvature: 0, fps: 0, alert: "OK" });

  // --- REFS UNTUK VIDEO LOKAL & WEBSOCKET ---
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const loopRef = useRef(null);

  // Derivasi logic warning dari data AI
  const isWarning = telemetry.alert === 'DEPARTURE' || Math.abs(telemetry.offset) > 0.5;

  useEffect(() => {
    // 1. Matikan setupCamera karena kita akan pakai file video
    // (Kode getUserMedia dihapus)

    // 2. Setup WebSocket 
    const wsUrl = "wss://laneguard-production.up.railway.app/ws/stream"; 
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("✅ WebSocket Terhubung!");
      setIsConnected(true);
      
      // Ambil foto dari video MP4 tiap 80ms untuk dikirim ke Backend
      loopRef.current = setInterval(() => {
        sendFrameToBackend();
      }, 80);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setProcessedImage(`data:image/jpeg;base64,${data.image}`);
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
    
    // PENTING: Jangan kirim kalau videonya sedang pause atau berhenti
    if (videoRef.current.paused || videoRef.current.ended) return;

    const canvas = canvasRef.current;
    const video = videoRef.current;
    const context = canvas.getContext('2d');

    // Salin frame dari VIDEO MP4 ke canvas
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const fullBase64 = canvas.toDataURL('image/jpeg', 0.4); 
    const base64Data = fullBase64.split(',')[1]; 

    wsRef.current.send(JSON.stringify({ image: base64Data }));
  };

  const toggleTheme = () => setIsDarkMode(!isDarkMode);

  return (
    <div className={`${isDarkMode ? "dark" : ""} min-h-screen w-full`}>
      <div className="min-h-screen w-full bg-slate-100 dark:bg-slate-950 text-slate-800 dark:text-slate-100 flex flex-col font-sans transition-colors duration-300">
        
        {/* --- HEADER --- */}
        <header className="p-3 md:p-4 bg-white dark:bg-slate-900 flex justify-between items-center shadow-sm border-b border-slate-200 dark:border-slate-800 transition-colors duration-300">
          <div className="flex items-center gap-2 md:gap-3">
            <ShieldCheck
              className={isWarning ? "text-red-500 animate-pulse" : "text-emerald-500 dark:text-emerald-400"}
              size={28} 
            />
            <h1 className="text-xl md:text-2xl font-bold tracking-wider text-slate-800 dark:text-white">
              LANE-GUARD
            </h1>
          </div>

          <div className="flex items-center gap-3 md:gap-6">
            <div className={`flex items-center gap-1.5 px-2 md:px-3 py-1 rounded-md border font-mono text-xs md:text-sm font-bold ${
              telemetry.fps < 5 
                ? 'bg-red-100 text-red-600 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800' 
                : 'bg-slate-100 text-slate-600 border-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700'
            }`}>
              <Activity size={14} className={telemetry.fps < 5 ? 'animate-pulse' : ''} />
              <span>{telemetry.fps} FPS</span>
            </div>

            {/* Status Connection */}
            <div className="hidden sm:flex items-center gap-2 text-xs md:text-sm font-semibold text-slate-500 dark:text-slate-400 font-mono">
              <span className="flex h-3 w-3 relative">
                <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${isConnected ? "bg-emerald-400" : "bg-red-400"}`}></span>
                <span className={`relative inline-flex rounded-full h-3 w-3 ${isConnected ? "bg-emerald-500" : "bg-red-500"}`}></span>
              </span>
              {isConnected ? "Active" : "Offline"}
            </div>

            <button
              onClick={toggleTheme}
              className="p-2 rounded-full bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 transition-colors"
              title="Toggle Theme"
            >
              {isDarkMode ? (
                <Moon size={20} className="text-blue-200" />
              ) : (
                <Sun size={20} className="text-yellow-500" />
              )}
            </button>
          </div>
        </header>

        {/* --- MAIN CONTENT AREA --- */}
        <main className="flex-1 flex flex-col p-2 md:p-4 gap-4 relative">
          
          {isWarning && (
            <div className="absolute top-4 md:top-8 left-1/2 -translate-x-1/2 z-10 w-11/12 md:w-auto max-w-sm bg-red-600 text-white px-4 md:px-8 py-2 md:py-3 rounded-full flex justify-center items-center gap-2 md:gap-3 shadow-lg shadow-red-600/50 animate-pulse">
              <AlertTriangle size={24} />
              <span className="font-bold text-sm md:text-xl uppercase tracking-widest whitespace-nowrap text-center">
                Lane Departure
              </span>
            </div>
          )}

          {/* PERUBAHAN UTAMA: ELEMEN TERSEMBUNYI UNTUK MEMUTAR VIDEO LOKAL */}
          <div className="hidden">
            <video 
              ref={videoRef} 
              src="/jalan.mp4" /* Pastikan jalan.mp4 ada di folder public */
              autoPlay 
              loop 
              muted 
              playsInline 
              width="640" 
              height="360" 
            />
            <canvas ref={canvasRef} width="640" height="360" />
          </div>

          {/* --- VIDEO FEED AREA --- */}
          <div className={`w-full max-w-5xl mx-auto aspect-video bg-black rounded-xl md:rounded-2xl overflow-hidden border-2 md:border-4 relative transition-colors duration-300 flex items-center justify-center ${isWarning ? "border-red-500 shadow-lg shadow-red-500/50" : "border-slate-300 dark:border-slate-800"}`}>
            {processedImage ? (
              <img src={processedImage} alt="LaneGuard Feed" className="w-full h-full object-contain" />
            ) : (
              <div className="text-slate-500 flex flex-col items-center gap-2">
                <ShieldCheck size={48} className="opacity-50" />
                <p className="font-mono text-sm tracking-widest uppercase">{isConnected ? "Waiting for video feed..." : "Waiting for backend..."}</p>
              </div>
            )}
            <div className="absolute inset-0 bg-gradient-to-b from-transparent to-slate-900/40 pointer-events-none"></div>
          </div>

          {/* --- METRICS DASHBOARD --- */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-4 flex-1">
            
            <div className="bg-white dark:bg-slate-900 rounded-xl md:rounded-2xl p-4 flex flex-col justify-center border border-slate-200 dark:border-slate-800 shadow-sm transition-colors duration-300 min-h-[100px]">
              <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 mb-2">
                <Navigation size={18} />
                <span className="text-xs md:text-sm font-bold uppercase tracking-wider">
                  Curvature Radius
                </span>
              </div>
              <div className="text-3xl md:text-4xl font-mono font-bold text-slate-800 dark:text-white">
                {telemetry.curvature === 9999 ? "Straight" : telemetry.curvature} <span className="text-lg md:text-xl text-slate-400">{telemetry.curvature !== 9999 && "m"}</span>
              </div>
            </div>

            <div className={`rounded-xl md:rounded-2xl p-4 flex flex-col justify-center border transition-all duration-300 shadow-sm min-h-[100px] ${
                isWarning
                  ? "bg-red-50 dark:bg-red-900/20 border-red-400 dark:border-red-500"
                  : "bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800"
              }`}
            >
              <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 mb-2">
                <Car size={18} />
                <span className="text-xs md:text-sm font-bold uppercase tracking-wider">
                  Vehicle Offset
                </span>
              </div>
              
              <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                <div className="flex items-baseline gap-2">
                  <span className={`text-4xl md:text-5xl font-mono font-bold ${
                      isWarning
                        ? "text-red-600 dark:text-red-400"
                        : "text-emerald-600 dark:text-emerald-400"
                    }`}
                  >
                    {Math.abs(telemetry.offset).toFixed(2)}
                  </span>
                  <span className="text-lg md:text-xl text-slate-400 font-mono">m</span>
                </div>
                
                <span className={`text-xl md:text-2xl font-bold uppercase ${
                    telemetry.offset > 0
                      ? "text-blue-500 dark:text-blue-400"
                      : telemetry.offset < 0
                        ? "text-amber-500 dark:text-amber-400"
                        : "text-slate-400"
                  }`}
                >
                  {telemetry.offset > 0 ? "Right" : telemetry.offset < 0 ? "Left" : "Center"}
                </span>
              </div>
            </div>
            
          </div>
        </main>
      </div>
    </div>
  );
}
