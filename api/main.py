"""
TITAN API — FastAPI gateway for the autonomous business engine
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import os

try:
    from titan.core import TITANCore
except ImportError:
    from core import TITANCore

app = FastAPI(title="TITAN API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

titan = TITANCore()


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}


@app.get("/status")
async def status():
    return titan.status()


@app.post("/tasks")
async def add_task(task_type: str, payload: dict = {}):
    task_id = titan.add_task(task_type, payload)
    return {"task_id": task_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
