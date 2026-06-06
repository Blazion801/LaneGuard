import cv2
import uvicorn
import asyncio
import json
import base64
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Import arsitektur AI kalian
from pipeline2 import ImprovedLaneDetector

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline = ImprovedLaneDetector()

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    print("✅ Client (HP) Connected to WebSocket!")
    try:
        while True:
            # 1. Terima data Base64 dari HP
            data = await websocket.receive_text()
            payload = json.loads(data)
            img_b64 = payload.get("image", "")

            if not img_b64:
                continue

            # 2. Decode Base64 menjadi gambar matriks (NumPy) untuk dibaca OpenCV
            img_bytes = base64.b64decode(img_b64)
            np_arr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                continue

            # 3. Proses gambar menggunakan AI kalian
            processed_frame, results = pipeline.process_frame(frame)

            # 4. Handle infinity value untuk jalanan lurus
            curve_val = results["curvature"]
            safe_curve = int(curve_val) if curve_val != float('inf') else 9999

            # 5. Compress gambar hasil proses dan ubah kembali jadi Base64
            # PENTING: Kualitas diturunkan ke 60% agar pengiriman balik ke HP sangat cepat (Low Latency)
            _, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 40])
            out_b64 = base64.b64encode(buffer).decode('utf-8')

            # 6. Kirim balik gambar dan telemetry ke HP
            response_data = {
                "image": out_b64,
                "offset": round(results["offset"], 2),
                "curvature": safe_curve,
                "fps": int(results["fps"]),
                "alert": results["alert"]
            }
            await websocket.send_text(json.dumps(response_data))
            
    except WebSocketDisconnect:
        print("❌ Client (HP) Disconnected")
    except Exception as e:
        print(f"⚠️ Error: {e}")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
