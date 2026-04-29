from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class VehicleModel(BaseModel):
    id: int
    type: int  # 3: Emergency, 2: VIP, 1: Normal
    type_name: str
    arrival_time: int
    waiting_time: int
    reservation: int
    priority: int
    is_large: bool = False
    
class SlotModel(BaseModel):
    id: int
    row: int
    col: int
    occupied: bool
    vehicle_id: Optional[int]
    is_reserved_slot: bool
    is_large_vehicle: bool = False  # True if part of a large vehicle allocation

class LogEntry(BaseModel):
    step: int
    status: str
    vehicle: str
    type: str
    priority: int
    slot: str
    distance: str
    waited: int
    action_type: str = "normal" # normal, preempt, reject

class SimulationState(BaseModel):
    step: int
    slots: List[SlotModel]
    queue: List[VehicleModel]
    logs: List[LogEntry]
    stats: Dict[str, Any]
    done: bool
    fcfs_stats: Optional[Dict[str, Any]] = None
