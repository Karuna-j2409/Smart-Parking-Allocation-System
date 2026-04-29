import React, { useEffect, useState } from 'react';

const ParkingGrid = ({ slots, logs }) => {
  const [preemptedSlots, setPreemptedSlots] = useState(new Set());

  // Listen to logs to trigger preemption animations
  useEffect(() => {
    if (!logs || logs.length === 0) return;
    
    // Find preempt actions in the latest step
    const latestStep = logs[0].step;
    const preemptLogs = logs.filter(l => l.step === latestStep && l.action_type === 'preempt');
    
    if (preemptLogs.length > 0) {
      const newPreempted = new Set();
      preemptLogs.forEach(log => {
        // Slot string looks like "(1,2)" or "(1,2),(1,3)"
        const coords = log.slot.match(/\d+/g);
        if (coords) {
          for (let i = 0; i < coords.length; i += 2) {
            const r = parseInt(coords[i]);
            const c = parseInt(coords[i+1]);
            const sid = r * 8 + c; // Assuming 8 columns
            newPreempted.add(sid);
          }
        }
      });
      setPreemptedSlots(newPreempted);
      
      // Clear animation class after a short delay
      setTimeout(() => {
        setPreemptedSlots(new Set());
      }, 600);
    }
  }, [logs]);

  if (!slots || slots.length === 0) {
    return <div style={{ color: 'var(--text-secondary)' }}>Loading grid...</div>;
  }

  return (
    <div className="parking-grid-wrapper">
      {slots.map((slot) => {
        let className = "slot";
        if (slot.occupied) {
          // Identify if it's an emergency vehicle. For prototype, if priority > 1000 usually it's emergency, 
          // or we can pass the vehicle type to the slot state. 
          // We don't have vehicle type in slot state right now, but we do have 'preempted' animation.
          className += " occupied";
        } else if (slot.is_reserved_slot) {
          className += " reserved";
        } else {
          className += " free";
        }

        if (preemptedSlots.has(slot.id)) {
          className += " preempted-animation";
        }

        return (
          <div key={slot.id} className={className} title={`Slot ${slot.id} (${slot.row},${slot.col})`}>
            {slot.occupied ? (
              <>
                <span style={{ fontSize: '0.7rem', color: '#fff', opacity: 0.8 }}>#{slot.vehicle_id}</span>
                {slot.is_large_vehicle && <span style={{ fontSize: '0.6rem' }}>(L)</span>}
              </>
            ) : (
              <span style={{ opacity: 0.5 }}>{slot.id}</span>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default ParkingGrid;
