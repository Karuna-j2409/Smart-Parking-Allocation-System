import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List

from models import SimulationState
from simulation import SimulationManager
from data_exporter import export_results

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()
sim_manager = SimulationManager(rows=4, cols=8)
simulation_running = False

@app.get("/api/state")
async def get_state():
    return sim_manager.get_state()

@app.post("/api/control")
async def control_simulation(action: dict):
    global simulation_running, sim_manager
    command = action.get("command")
    
    if command == "start":
        simulation_running = True
        asyncio.create_task(run_simulation_loop())
    elif command == "stop":
        simulation_running = False
        export_results(sim_manager.get_state())
    elif command == "step":
        sim_manager.run_step()
        await manager.broadcast(sim_manager.get_state())
    elif command == "reset":
        simulation_running = False
        sim_manager = SimulationManager(rows=4, cols=8)
        await manager.broadcast(sim_manager.get_state())
        
    return {"status": "ok", "running": simulation_running}

async def run_simulation_loop():
    global simulation_running
    while simulation_running:
        sim_manager.run_step()
        await manager.broadcast(sim_manager.get_state())
        await asyncio.sleep(1.0) # 1 second per step

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state
        await websocket.send_json(sim_manager.get_state())
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
