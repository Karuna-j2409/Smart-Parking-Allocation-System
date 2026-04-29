import heapq
import random
import math
from typing import List, Dict, Tuple, Optional

class Vehicle:
    def __init__(self, vid: int, vtype: int, arrival: int, reservation: int, is_large: bool = False):
        self.id = vid
        self.type = vtype  # 3=Emergency, 2=VIP, 1=Normal
        self.arrival = arrival
        self.waiting = 0
        self.reservation = reservation
        self.is_large = is_large
        self.priority = 0
        
        type_names = {3: "Emergency", 2: "VIP", 1: "Normal"}
        self.type_name = type_names.get(vtype, "Normal")
        
    def compute_priority(self, congestion_factor: float = 1.0):
        # Adaptive priority: wait time matters more if highly congested
        # Emergency type has extremely high weight to ensure it stays at top
        self.priority = (1000 if self.type == 3 else 0) + 50 * self.type + (3 * congestion_factor) * self.waiting + 10 * self.reservation
        return -self.priority  # Min-heap behavior to act as max-heap

    def __lt__(self, other):
        # Tie-breaker: older ID first
        return self.id < other.id

class Slot:
    def __init__(self, sid: int, row: int, col: int, is_reserved: bool = False):
        self.id = sid
        self.row = row
        self.col = col
        self.occupied = False
        self.vehicle_id = None
        self.is_reserved_slot = is_reserved
        self.is_large_vehicle = False
        self.time_left = 0 # Time until vehicle departs

def euclidean(s: Slot):
    # Distance from entrance (0,0)
    return math.sqrt(s.row ** 2 + s.col ** 2)

class SimulationManager:
    def __init__(self, rows=4, cols=8):
        self.rows = rows
        self.cols = cols
        self.total_slots = rows * cols
        self.slots: List[Slot] = []
        self.heap = []
        self.step = 0
        self.logs = []
        
        # Stats
        self.stats = {
            "arrived": 0,
            "allocated": 0,
            "rejected": 0,
            "preempted": 0,
            "total_wait_time": 0,
            "avg_wait_time": 0.0,
            "utilization": 0.0,
            "queue_length": 0,
            "congestion_factor": 1.0
        }
        
        self.init_slots()

    def init_slots(self):
        self.slots = []
        for r in range(self.rows):
            for c in range(self.cols):
                # Make the last column reserved for VIP/Emergency
                is_res = (c == self.cols - 1)
                self.slots.append(Slot(r * self.cols + c, r, c, is_reserved=is_res))

    def add_vehicles(self, num: int):
        for _ in range(num):
            self.stats["arrived"] += 1
            vid = self.stats["arrived"]
            # 10% Emergency, 20% VIP, 70% Normal
            vtype = random.choices([3, 2, 1], weights=[10, 20, 70])[0]
            reservation = 1 if (vtype == 2 and random.random() < 0.5) else 0
            is_large = random.random() < 0.15 # 15% chance of large vehicle
            
            v = Vehicle(vid, vtype, self.step, reservation, is_large)
            heapq.heappush(self.heap, (v.compute_priority(self.stats["congestion_factor"]), v))

    def get_utilization(self):
        occ = sum(1 for s in self.slots if s.occupied)
        return occ / self.total_slots

    def update_departures(self):
        # Decrement time_left and free slots
        for s in self.slots:
            if s.occupied:
                s.time_left -= 1
                if s.time_left <= 0:
                    s.occupied = False
                    s.vehicle_id = None
                    s.is_large_vehicle = False

    def find_nearest_slot(self, v: Vehicle) -> Optional[List[Slot]]:
        best_dist = float('inf')
        best_slots = None
        
        if v.is_large:
            # Need 2 adjacent horizontal slots
            for r in range(self.rows):
                for c in range(self.cols - 1):
                    s1 = self.slots[r * self.cols + c]
                    s2 = self.slots[r * self.cols + c + 1]
                    
                    if not s1.occupied and not s2.occupied:
                        # Check reservation constraint
                        if (s1.is_reserved_slot or s2.is_reserved_slot) and v.type == 1:
                            continue # Normal vehicles can't take reserved slots
                        
                        dist = (euclidean(s1) + euclidean(s2)) / 2
                        if dist < best_dist:
                            best_dist = dist
                            best_slots = [s1, s2]
        else:
            # Single slot
            for s in self.slots:
                if not s.occupied:
                    if s.is_reserved_slot and v.type == 1:
                        continue
                    dist = euclidean(s)
                    if dist < best_dist:
                        best_dist = dist
                        best_slots = [s]
                        
        return best_slots

    def preempt_for_emergency(self, emergency_v: Vehicle) -> Optional[List[Slot]]:
        # Find the lowest priority normal vehicle and evict it
        # Prefer single slots if emergency is single, double if emergency is double
        candidates = []
        for s in self.slots:
            if s.occupied and s.vehicle_id is not None:
                # In a real system, we might look up the vehicle type. 
                # For simplicity, if it's not reserved slot, maybe it's normal. 
                # Let's just track vehicle types in slots or rely on a simple heuristic.
                # Here we assume normal vehicles are in non-reserved slots.
                if not s.is_reserved_slot:
                    candidates.append(s)
                    
        if candidates:
            # Pick a random candidate or the one furthest away
            candidates.sort(key=lambda x: euclidean(x), reverse=True) # Evict furthest
            if emergency_v.is_large:
                # Need 2 adjacent. Just find any 2 adjacent normal.
                for i in range(len(candidates)-1):
                    # Check if they are actually adjacent in grid
                    c1, c2 = candidates[i], candidates[i+1]
                    if c1.row == c2.row and abs(c1.col - c2.col) == 1:
                        c1.occupied = False
                        c2.occupied = False
                        return [c1, c2]
            else:
                c1 = candidates[0]
                c1.occupied = False
                return [c1]
        return None

    def run_step(self):
        self.step += 1
        self.update_departures()
        
        # Arrivals (randomly 2 to 5 vehicles per step)
        self.add_vehicles(random.randint(2, 5))
        
        self.stats["congestion_factor"] = 1.0 + self.get_utilization()
        
        # Process queue
        # We try to process as many as possible until we can't find slots
        processed = []
        rejected = []
        temp_heap = []
        
        # Simulate entry gate bottleneck: process up to 2 vehicles per step
        process_limit = 2
        
        while self.heap and len(processed) < process_limit:
            _, v = heapq.heappop(self.heap)
            slots = self.find_nearest_slot(v)
            
            if slots:
                # Allocate
                for s in slots:
                    s.occupied = True
                    s.vehicle_id = v.id
                    s.is_large_vehicle = v.is_large
                    s.time_left = random.randint(20, 60) # Stays for 20 to 60 steps
                
                self.stats["allocated"] += 1
                self.stats["total_wait_time"] += v.waiting
                
                dist_str = f"{round(euclidean(slots[0]), 1)}" if not v.is_large else f"{round(euclidean(slots[0]), 1)} (L)"
                slot_str = ",".join([f"({s.row},{s.col})" for s in slots])
                
                self.logs.insert(0, {
                    "step": self.step,
                    "status": "Allocated",
                    "vehicle": f"#{v.id}",
                    "type": v.type_name,
                    "priority": -_,
                    "slot": slot_str,
                    "distance": dist_str,
                    "waited": v.waiting,
                    "action_type": "normal"
                })
                processed.append(v)
            else:
                # No slot available
                if v.type == 3 and v.waiting >= 2:
                    # Emergency preemption
                    preempted_slots = self.preempt_for_emergency(v)
                    if preempted_slots:
                        for s in preempted_slots:
                            s.occupied = True
                            s.vehicle_id = v.id
                            s.is_large_vehicle = v.is_large
                            s.time_left = random.randint(20, 60)
                        
                        self.stats["preempted"] += 1
                        self.stats["allocated"] += 1
                        self.stats["total_wait_time"] += v.waiting
                        
                        slot_str = ",".join([f"({s.row},{s.col})" for s in preempted_slots])
                        self.logs.insert(0, {
                            "step": self.step,
                            "status": "Preempted",
                            "vehicle": f"#{v.id}",
                            "type": v.type_name,
                            "priority": -_,
                            "slot": slot_str,
                            "distance": "—",
                            "waited": v.waiting,
                            "action_type": "preempt"
                        })
                        processed.append(v)
                        continue

                # Wait or Reject
                if v.waiting < 10:
                    v.waiting += 1
                    # Re-compute priority (Aging)
                    heapq.heappush(temp_heap, (v.compute_priority(self.stats["congestion_factor"]), v))
                else:
                    # Reject
                    self.stats["rejected"] += 1
                    self.logs.insert(0, {
                        "step": self.step,
                        "status": "Rejected",
                        "vehicle": f"#{v.id}",
                        "type": v.type_name,
                        "priority": -_,
                        "slot": "—",
                        "distance": "—",
                        "waited": v.waiting,
                        "action_type": "reject"
                    })
                    rejected.append(v)
                    
        # Put back waiting vehicles into heap
        for item in self.heap:
            _, v = item
            v.waiting += 1
            heapq.heappush(temp_heap, (v.compute_priority(self.stats["congestion_factor"]), v))
            
        self.heap = temp_heap
        
        # Keep logs manageable
        if len(self.logs) > 50:
            self.logs = self.logs[:50]
            
        # Update stats
        self.stats["queue_length"] = len(self.heap)
        self.stats["utilization"] = self.get_utilization()
        if self.stats["allocated"] > 0:
            self.stats["avg_wait_time"] = self.stats["total_wait_time"] / self.stats["allocated"]

    def get_state(self):
        # Convert internal state to dictionaries matching pydantic models
        slots_list = []
        for s in self.slots:
            slots_list.append({
                "id": s.id, "row": s.row, "col": s.col, 
                "occupied": s.occupied, "vehicle_id": s.vehicle_id, 
                "is_reserved_slot": s.is_reserved_slot,
                "is_large_vehicle": s.is_large_vehicle
            })
            
        queue_list = []
        # Sort queue by priority for display
        sorted_queue = sorted(self.heap, key=lambda x: x[0])
        for _, v in sorted_queue:
            queue_list.append({
                "id": v.id, "type": v.type, "type_name": v.type_name,
                "arrival_time": v.arrival, "waiting_time": v.waiting,
                "reservation": v.reservation, "priority": -_,
                "is_large": v.is_large
            })
            
        return {
            "step": self.step,
            "slots": slots_list,
            "queue": queue_list,
            "logs": self.logs,
            "stats": self.stats,
            "done": False,
            "fcfs_stats": None # We will add FCFS stats later if needed
        }
