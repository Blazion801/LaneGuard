import cv2
import uvicorn
import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# 1. FIXED IMPORT: We import the new class name from your renamed pipeline2 file
from pipeline2 import ImprovedLaneDetector

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. FIXED INIT: Initialize the new pipeline class
pipeline = ImprovedLaneDetector()

# --- GLOBAL STATE TO BRIDGE VIDEO AND WEBSOCKET ---
latest_telemetry = {
    "offset": 0.0,
    "curvature": 0,
    "fps": 0
}

# --- 1. VIDEO STREAMING ENDPOINT ---
def generate_frames():
    global latest_telemetry
    # cap = cv2.VideoCapture('test_video.mp4') 
    cap = cv2.VideoCapture() 
    
    while cap.isOpened():
        success, frame = cap.read()
        
        if not success:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
            
        # 3. FIXED OUTPUT: The new pipeline returns the image and a dictionary of results
        processed_frame, results = pipeline.process_frame(frame)
        
        # Safely handle the infinity curvature for straight roads
        curve_val = results["curvature"]
        safe_curve = int(curve_val) if curve_val != float('inf') else 9999

        # Update the global telemetry state directly from the new dictionary
        latest_telemetry["offset"] = round(results["offset"], 2)
        latest_telemetry["curvature"] = safe_curve
        latest_telemetry["fps"] = int(results["fps"])
        
        ret, buffer = cv2.imencode('.jpg', processed_frame)
        frame_bytes = buffer.tobytes()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.get("/video_feed")
def video_feed():
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


# --- 2. WEBSOCKET TELEMETRY ENDPOINT ---
@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Broadcast the real math from the global state
            await websocket.send_text(json.dumps(latest_telemetry))
            
            # Send data 20 times a second to match video framerate!
            await asyncio.sleep(0.05) 
            
    except WebSocketDisconnect:
        print("React Client Disconnected")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
