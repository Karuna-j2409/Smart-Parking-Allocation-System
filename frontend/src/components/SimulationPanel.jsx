import React from 'react';

const SimulationPanel = ({ logs, step }) => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ marginBottom: '10px', display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ color: 'var(--text-secondary)' }}>Current Step:</span>
        <span style={{ fontWeight: 'bold', color: 'var(--text-primary)' }}>{step}</span>
      </div>
      
      <div className="log-list">
        {logs.map((log, index) => {
          let className = "log-entry";
          if (log.action_type === 'preempt') className += " preempt";
          else if (log.action_type === 'reject') className += " reject";
          else className += " normal";
          
          return (
            <div key={`${log.step}-${log.vehicle}-${index}`} className={className}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span className="vehicle-info">
                  Step {log.step} | {log.type} {log.vehicle}
                </span>
                <span style={{ fontWeight: 'bold', color: log.action_type === 'preempt' ? 'var(--color-emergency)' : log.action_type === 'reject' ? 'var(--color-occupied)' : 'var(--color-free)' }}>
                  {log.status}
                </span>
              </div>
              <span style={{ fontSize: '0.8rem' }}>
                {log.action_type === 'preempt' ? (
                  <>⚠️ Preempted slot(s) {log.slot} (Waited {log.waited}s)</>
                ) : log.action_type === 'reject' ? (
                  <>❌ Rejected. Lot full and queue capacity reached.</>
                ) : (
                  <>✅ Allocated to slot {log.slot} (Dist: {log.distance})</>
                )}
              </span>
            </div>
          );
        })}
        {logs.length === 0 && (
          <div style={{ color: 'var(--text-secondary)', fontStyle: 'italic', padding: '10px' }}>
            No logs available. Start simulation to see events.
          </div>
        )}
      </div>
    </div>
  );
};

export default SimulationPanel;
