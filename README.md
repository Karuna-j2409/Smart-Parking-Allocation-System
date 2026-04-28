# 🚗 Smart Parking Allocation System

A **DAA (Design and Analysis of Algorithms)** project prototype demonstrating **Priority-Based Smart Parking Slot Allocation** using a Max-Heap Priority Queue.

## 🎯 Concepts Demonstrated

| Concept | Implementation |
|---|---|
| **Priority Queue (Max-Heap)** | Vehicles sorted by `P = 5×type + 3×wait + 2×reservation` |
| **Fairness — Aging** | Waiting vehicles get priority boost every step — no starvation |
| **Efficiency — Greedy** | Nearest free slot assigned via Euclidean distance from entrance |
| **Dynamic Update** | Heap re-heapifies every step — live reordering |

## 🚀 Run Locally

```bash
pip install streamlit pandas matplotlib
python -m streamlit run app.py
```

Then open **http://localhost:8501** in your browser.

## 📁 Files

| File | Description |
|---|---|
| `app.py` | Streamlit prototype UI + simulation logic |
| `smart_parking_v3.py` | Full advanced simulation (Dijkstra, adaptive weights, multi-entrance) |
| `parking_graphs_v3.png` | Output graphs from full simulation |
| `simulation_log.json` | Full simulation event log |
| `vehicle_results.csv` | Per-vehicle allocation results |
| `step_metrics.csv` | Per-step simulation metrics |

## 🧠 Priority Formula

```
Priority = 5 × type_score + 3 × waiting_time + 2 × reservation
```

- **Emergency** → type_score = 3  
- **VIP** → type_score = 2  
- **Normal** → type_score = 1  

## 📊 Demo

- Click **▶️ Run 1 Step** to step through allocation one vehicle at a time  
- Click **⏩ Run All** to run the complete simulation  
- Use the sidebar to change vehicle count and reset
