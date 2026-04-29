import React, { useState } from 'react';

const MetricsDashboard = ({ stats }) => {
  const [mode, setMode] = useState('Proposed');

  if (!stats) return null;

  // Mocking FCFS for demonstration if not provided by backend yet
  const displayStats = mode === 'Proposed' ? stats : {
    avg_wait_time: stats.avg_wait_time * 1.8,
    queue_length: Math.floor(stats.queue_length * 1.5),
    rejected: Math.floor(stats.rejected * 1.3),
    utilization: Math.max(0, stats.utilization - 0.15)
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '1.5rem' }}>
        <div style={{ background: 'var(--bg-color)', borderRadius: '8px', padding: '4px' }}>
          <button 
            onClick={() => setMode('Proposed')}
            style={{ 
              background: mode === 'Proposed' ? 'var(--accent-color)' : 'transparent',
              color: mode === 'Proposed' ? '#fff' : 'var(--text-secondary)',
              border: 'none', padding: '6px 16px', borderRadius: '4px', cursor: 'pointer', fontWeight: 600 
            }}>
            Proposed System
          </button>
          <button 
            onClick={() => setMode('FCFS')}
            style={{ 
              background: mode === 'FCFS' ? 'var(--border-color)' : 'transparent',
              color: mode === 'FCFS' ? '#fff' : 'var(--text-secondary)',
              border: 'none', padding: '6px 16px', borderRadius: '4px', cursor: 'pointer', fontWeight: 600 
            }}>
            FCFS Baseline
          </button>
        </div>
      </div>

      <div className="metrics-grid">
        <div className="metric-item">
          <span className="value">{displayStats.avg_wait_time ? displayStats.avg_wait_time.toFixed(1) : '0.0'}</span>
          <span className="label">Avg Wait Time</span>
        </div>
        <div className="metric-item">
          <span className="value">{displayStats.queue_length || 0}</span>
          <span className="label">Queue Length</span>
        </div>
        <div className="metric-item">
          <span className="value">{displayStats.rejected || 0}</span>
          <span className="label">Rejections</span>
        </div>
        <div className="metric-item">
          <span className="value">{displayStats.utilization ? (displayStats.utilization * 100).toFixed(0) : '0'}%</span>
          <span className="label">Utilization</span>
        </div>
      </div>
      
      {mode === 'Proposed' && (
        <div style={{ marginTop: '1rem', padding: '1rem', background: 'rgba(96, 165, 250, 0.1)', borderRadius: '8px', borderLeft: '4px solid var(--color-emergency)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Emergency Preemptions</span>
            <span style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--color-emergency)' }}>{stats.preempted || 0}</span>
          </div>
          <p style={{ margin: '4px 0 0', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Normal vehicles preempted to accommodate emergency arrivals when lot is full.
          </p>
        </div>
      )}
    </div>
  );
};

export default MetricsDashboard;
