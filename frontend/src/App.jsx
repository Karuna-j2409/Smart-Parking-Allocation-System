import React, { useState, useEffect, useRef } from 'react';
import './index.css';
import ParkingGrid from './components/ParkingGrid';
import SimulationPanel from './components/SimulationPanel';
import QueueVisualizer from './components/QueueVisualizer';
import MetricsDashboard from './components/MetricsDashboard';

function App() {
  const [gameState, setGameState] = useState({
    step: 0,
    slots: [],
    queue: [],
    logs: [],
    stats: {
      arrived: 0, allocated: 0, rejected: 0, preempted: 0,
      total_wait_time: 0, avg_wait_time: 0, utilization: 0,
      queue_length: 0, congestion_factor: 1.0
    },
    done: false
  });
  
  const [isRunning, setIsRunning] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  const connectWebSocket = () => {
    // Determine WS URL based on current host (useful for local dev)
    const wsUrl = `ws://localhost:8000/ws`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("WebSocket Connected");
      setWsConnected(true);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setGameState(data);
    };

    ws.onclose = () => {
      console.log("WebSocket Disconnected. Reconnecting in 2s...");
      setWsConnected(false);
      setTimeout(connectWebSocket, 2000);
    };

    wsRef.current = ws;
  };

  const sendCommand = async (command) => {
    try {
      const res = await fetch('http://localhost:8000/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command })
      });
      const data = await res.json();
      if (command === 'start') setIsRunning(true);
      if (command === 'stop' || command === 'reset') setIsRunning(false);
    } catch (e) {
      console.error("Error sending command", e);
    }
  };

  return (
    <div className="dashboard-container">
      <header className="header">
        <div>
          <h1>🚗 Adaptive Hybrid Smart Parking</h1>
          <p style={{ color: 'var(--text-secondary)', margin: 0 }}>
            {wsConnected ? "🟢 Connected to Simulation Engine" : "🔴 Disconnected"}
          </p>
        </div>
        <div className="header-controls">
          {!isRunning ? (
            <button onClick={() => sendCommand('start')} style={{ background: '#4ade80', color: '#000' }}>▶ Start</button>
          ) : (
            <button onClick={() => sendCommand('stop')} style={{ background: '#ff6b6b' }}>⏸ Stop</button>
          )}
          <button onClick={() => sendCommand('step')}>⏭ Step</button>
          <button onClick={() => sendCommand('reset')} style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}>🔄 Reset</button>
        </div>
      </header>

      {/* Left Column: Grid and Queue */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        <div className="card">
          <h2>Parking Grid (4x8)</h2>
          <ParkingGrid slots={gameState.slots} logs={gameState.logs} />
        </div>
        
        <div className="card" style={{ flex: 1 }}>
          <h2>Live Priority Queue (Max-Heap)</h2>
          <QueueVisualizer queue={gameState.queue} />
        </div>
      </div>

      {/* Right Column: Metrics and Logs */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        <div className="card">
          <h2>Metrics & KPIs</h2>
          <MetricsDashboard stats={gameState.stats} />
        </div>
        
        <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <h2>Decision Logs</h2>
          <SimulationPanel logs={gameState.logs} step={gameState.step} />
        </div>
      </div>
    </div>
  );
}

export default App;
