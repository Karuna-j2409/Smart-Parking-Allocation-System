import json
import csv
import os
from typing import Dict, Any

def export_results(state: Dict[str, Any], output_dir: str = "../results"):
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Export summary stats
    stats = state.get("stats", {})
    with open(os.path.join(output_dir, "summary_stats.json"), "w") as f:
        json.dump(stats, f, indent=4)
        
    # 2. Export logs as CSV
    logs = state.get("logs", [])
    if logs:
        # Assuming all logs have the same keys
        keys = logs[0].keys()
        with open(os.path.join(output_dir, "simulation_logs.csv"), "w", newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(logs)
            
    print(f"Results exported to {output_dir}")
