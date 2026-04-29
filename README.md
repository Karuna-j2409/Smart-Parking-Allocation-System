# 🚗 Adaptive Hybrid Smart Parking System

A realistic, interactive prototype demonstrating an intelligent parking allocation system. The system uses a **Python FastAPI backend** for simulation logic and a **React/Vite frontend** for real-time visualization.

## 🎯 Concepts Demonstrated

| Concept | Implementation |
|---|---|
| **Priority Queue (Max-Heap)** | Vehicles sorted by adaptive formula. Emergencies stay at the top. |
| **Fairness — Aging** | Waiting vehicles get priority boosts based on wait time and congestion to prevent starvation. |
| **Emergency Preemption** | If the lot is full and an emergency vehicle waits too long, it preempts (evicts) the lowest priority normal vehicle. |
| **Adaptive Allocation** | Simulates entry gate bottlenecks (processing limit) to create realistic queues. |
| **Efficiency — Greedy** | Nearest free slot assigned via Euclidean distance. Large vehicles find 2 adjacent slots. |

## 🚀 Run Locally

### 1. Start Backend (Simulation Engine)
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --port 8000
```

### 2. Start Frontend (Web Dashboard)
```bash
cd frontend
npm install
npm run dev
```
Then open **http://localhost:5173** in your browser.

## 📁 Directory Structure

- `/backend`: Core Python simulation logic (`simulation.py`) and FastAPI WebSocket server (`main.py`).
- `/frontend`: React dashboard built with Vite, vanilla CSS, and real-time visualization components.
- `/results`: Automatically generated simulation logs (`simulation_logs.csv`) and statistics (`summary_stats.json`) after stopping the simulation.
- `/data`: Future use for custom datasets.

## 📊 Interaction

- Click **▶ Start** to run the simulation dynamically.
- Click **⏸ Stop** to halt and export results to the `/results` folder.
- Click **⏭ Step** to advance the simulation frame-by-frame.
- Use **🔄 Reset** to clear the grid and logs.
