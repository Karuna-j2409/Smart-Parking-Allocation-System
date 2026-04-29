import React from 'react';

const QueueVisualizer = ({ queue }) => {
  if (!queue || queue.length === 0) {
    return <div style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>Queue is empty.</div>;
  }

  return (
    <ul className="queue-list">
      {queue.map((v, index) => (
        <li key={v.id} className="queue-item" style={{ animationDelay: `${index * 50}ms` }}>
          <div>
            <span className={`v-type ${v.type_name}`}>
              {v.type_name === 'Emergency' ? '🚨 ' : v.type_name === 'VIP' ? '⭐ ' : '🚗 '}
              #{v.id}
            </span>
            {v.is_large && <span style={{ fontSize: '0.8rem', marginLeft: '5px', color: '#8892b0' }}>(Large)</span>}
            {v.reservation === 1 && <span style={{ fontSize: '0.8rem', marginLeft: '5px', color: '#8892b0' }}>(Reserved)</span>}
          </div>
          <div style={{ textAlign: 'right' }}>
            <span style={{ color: 'var(--accent-color)', fontWeight: 'bold' }}>P: {v.priority}</span>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
              Waited: {v.waiting}s
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
};

export default QueueVisualizer;
