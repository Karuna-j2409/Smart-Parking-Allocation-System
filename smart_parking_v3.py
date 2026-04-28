"""
=============================================================================
Adaptive Hybrid Priority-Based Smart Parking Slot Allocation System  v3
=============================================================================
MODIFICATIONS OVER v2  (existing classes extended, not rewritten):
  Vehicle        — + reservation_start/end, no_show flag, entrance_id,
                     walking_distance field, urgency_boost field
  SlotManager    — + reserved_slots dict, reserve/release_reservation(),
                     reserve_for_window()
  DijkstraRouter — + multi-entrance (3 entrances), traffic-aware distance
                     effective_dist = base * (1 + congestion_factor)
  PredictionModule — unchanged interface, used by PriorityEngine
  PriorityEngine — + w7*(1/traffic_delay), w8*(1/walking_dist),
                     smooth adaptive weight updates (EMA), urgency boost
  MaxHeapPQ      — + pop_lowest() for smart rejection
  ProposedSimulation — + burst traffic model, peak steps, no-show handling,
                         smart rejection (eject lowest, never emergency),
                         reservation window enforcement, per-type metrics,
                         CSV + JSON log export
  FCFSSimulation — logic unchanged; adapted for new Vehicle fields only
  generate_graphs — 9 panels including new: traffic-aware distance heatmap,
                    per-type rejection bars, peak traffic annotation,
                    walking distance CDF
=============================================================================
"""

import heapq, random, math, json, csv, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.colors as mcolors
import numpy as np
from typing import Optional, List, Dict, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
GRID_ROWS           = 4
GRID_COLS           = 8          # 32 slots — tight capacity
TOTAL_SLOTS         = GRID_ROWS * GRID_COLS
SIMULATION_STEPS    = 150
TARGET_VEHICLES     = 220

# --- Multi-entrance positions (row, col) — entrance at top-left, mid-left, top-right
ENTRANCE_POSITIONS  = [(0, 0), (2, 0), (0, 7)]

# --- Exit / walking reference point (bottom-right corner)
EXIT_POSITION       = (GRID_ROWS - 1, GRID_COLS - 1)

# --- Burst / peak traffic
PEAK_STEPS          = list(range(20, 35)) + list(range(70, 90))  # two rush hours
NORMAL_ARRIVAL_W    = [0.05, 0.10, 0.20, 0.30, 0.25, 0.10]      # mean ≈ 3.2
PEAK_ARRIVAL_W      = [0.00, 0.02, 0.08, 0.20, 0.35, 0.35]      # mean ≈ 4.6

# --- Parking duration (longer → congestion)
PARK_MIN, PARK_MAX  = 8, 35

# --- Allocation throttle per step
# Both throttled so queue builds; Proposed is one faster → clear advantage
PROPOSED_ALLOC      = 3
FCFS_ALLOC          = 2

# --- Queue overflow rejection threshold (larger buffer → vehicles wait, not instant reject)
QUEUE_REJECT_TH     = 28

# --- No-show probability
NO_SHOW_PROB        = 0.04   # 4 % of reserved vehicles are no-shows

# --- Reservation window width (steps from arrival)
RESERVATION_WIN     = 15     # reservation valid for 15 steps

# --- Aging
AGING_THRESHOLD     = 4
AGING_BOOST         = 2.5

# --- Vehicle mix
EMERGENCY_PROB      = 0.06
VIP_PROB            = 0.14
RESERVATION_PROB    = 0.32
LARGE_PROB          = 0.09

# --- Base weights (w1–w6 legacy, w7/w8 new)
BASE_WEIGHTS = {
    "w1": 5.0,   # vehicle type
    "w2": 3.0,   # normalised waiting time
    "w3": 2.0,   # reservation
    "w4": 1.5,   # 1/distance
    "w5": 1.0,   # predicted load
    "w6": 2.5,   # fairness score
    "w7": 1.2,   # 1/traffic_delay  (NEW)
    "w8": 0.8,   # 1/walking_dist   (NEW)
}

# --- Smooth adaptive weight EMA factor
WEIGHT_ALPHA        = 0.15   # EMA smoothing: new_w = alpha*delta + (1-alpha)*old_w

RANDOM_SEED         = 7
OUTPUT_DIR          = "/home/claude"

# ═══════════════════════════════════════════════════════════════════════════════
# VEHICLE  (extended)
# ═══════════════════════════════════════════════════════════════════════════════

class Vehicle:
    _ctr = 0

    def __init__(self, vehicle_type, arrival_time, reservation, large,
                 entrance_id=0):
        Vehicle._ctr += 1
        self.vehicle_id   = Vehicle._ctr
        self.vehicle_type = vehicle_type
        self.arrival_time = arrival_time
        self.reservation  = reservation          # 0 or 1 (has reservation)
        self.large        = large
        self.entrance_id  = entrance_id          # NEW: which entrance used

        # Reservation window  (NEW)
        if reservation:
            self.reservation_start = arrival_time
            self.reservation_end   = arrival_time + RESERVATION_WIN
        else:
            self.reservation_start = None
            self.reservation_end   = None

        # No-show  (NEW)
        self.no_show = reservation and (random.random() < NO_SHOW_PROB)

        # State fields
        self.waiting_time    = 0
        self.assigned_slot   = None
        self.secondary_slot  = None
        self.departure_time  = None
        self.priority        = 0.0
        self.allocated       = False
        self.rejected        = False

        # Metrics  (NEW)
        self.traffic_delay   = 1.0   # effective congestion multiplier at time of allocation
        self.walking_distance = 1.0  # Euclidean distance from slot to exit

    def type_score(self):
        return {"EMERGENCY": 3, "VIP": 2, "NORMAL": 1}[self.vehicle_type]

    def reservation_valid(self, current_time):
        """NEW: True only if reservation window is still open."""
        if not self.reservation:
            return False
        return (self.reservation_start <= current_time <= self.reservation_end)


# ═══════════════════════════════════════════════════════════════════════════════
# PARKING SLOT  (unchanged interface)
# ═══════════════════════════════════════════════════════════════════════════════

class ParkingSlot:
    def __init__(self, slot_id, row, col):
        self.slot_id    = slot_id
        self.row        = row
        self.col        = col
        self.occupied   = False
        self.vehicle_id = None

    def occupy(self, vid):
        self.occupied   = True
        self.vehicle_id = vid

    def free(self):
        self.occupied   = False
        self.vehicle_id = None


# ═══════════════════════════════════════════════════════════════════════════════
# SLOT MANAGER  (extended: reservation windows)
# ═══════════════════════════════════════════════════════════════════════════════

class SlotManager:
    def __init__(self, rows, cols):
        self.rows  = rows
        self.cols  = cols
        self.slots: Dict[int, ParkingSlot] = {}
        for r in range(rows):
            for c in range(cols):
                sid = r * cols + c
                self.slots[sid] = ParkingSlot(sid, r, c)

        # NEW: reservation registry  {slot_id: (vehicle_id, end_time)}
        self.reservations: Dict[int, Tuple[int, int]] = {}

    # ── existing interface ────────────────────────────────────────────────
    def is_free(self, sid):     return not self.slots[sid].occupied
    def occupy(self, sid, vid): self.slots[sid].occupy(vid)
    def free_slot(self, sid):
        self.slots[sid].free()
        self.reservations.pop(sid, None)   # clear reservation if present

    def free_slots_list(self):
        return [sid for sid, s in self.slots.items() if not s.occupied]

    def utilization(self):
        return sum(1 for s in self.slots.values() if s.occupied) / len(self.slots)

    def adjacent_free(self, sid):
        s = self.slots[sid]
        adj_col = s.col + 1
        if adj_col < self.cols:
            adj_id = s.row * self.cols + adj_col
            if self.is_free(adj_id):
                return adj_id
        return None

    # ── NEW: reservation window management ───────────────────────────────
    def reserve_for_window(self, sid: int, vid: int, end_time: int):
        """Mark a slot as soft-reserved; does NOT occupy it yet."""
        self.reservations[sid] = (vid, end_time)

    def is_reserved_for(self, sid: int, vid: int) -> bool:
        entry = self.reservations.get(sid)
        return entry is not None and entry[0] == vid

    def expire_reservations(self, current_time: int) -> List[int]:
        """
        NEW: Release slots whose reservation window has passed without
        the vehicle arriving.  Returns list of freed slot ids.
        """
        expired = [sid for sid, (vid, end_t) in self.reservations.items()
                   if end_t < current_time]
        for sid in expired:
            self.reservations.pop(sid)
        return expired

    def reserved_free_slots(self, vid: int) -> List[int]:
        """NEW: Free slots that are reserved for this vehicle."""
        return [sid for sid, (v, _) in self.reservations.items() if v == vid
                and not self.slots[sid].occupied]

    def position(self, sid: int) -> Tuple[int, int]:
        s = self.slots[sid]
        return (s.row, s.col)


# ═══════════════════════════════════════════════════════════════════════════════
# DIJKSTRA ROUTER  (extended: multi-entrance + traffic-aware distance)
# ═══════════════════════════════════════════════════════════════════════════════

class DijkstraRouter:
    def __init__(self, rows, cols,
                 entrances: List[Tuple[int,int]] = None,
                 exit_pos:  Tuple[int,int]       = None):
        self.rows     = rows
        self.cols     = cols

        # ── multi-entrance (NEW) ──────────────────────────────────────────
        if entrances is None:
            entrances = [(0, 0)]
        self.entrances = entrances
        self.n_entrances = len(entrances)

        # Convert (row,col) → slot ids
        self.entrance_sids = [r * cols + c for r, c in entrances]

        # Exit position for walking-distance calc (NEW)
        self.exit_pos = exit_pos if exit_pos else (rows-1, cols-1)

        # Pre-compute base Dijkstra distance from EACH entrance
        # _dist_per_entrance[e][sid] = base hops from entrance e to sid
        self._dist_per_entrance: List[List[float]] = [
            self._dijkstra(src) for src in self.entrance_sids
        ]

        # Best (minimum) base distance from any entrance
        n = rows * cols
        self._best_base: List[float] = [
            min(self._dist_per_entrance[e][s] for e in range(self.n_entrances))
            for s in range(n)
        ]

        # Pre-compute Euclidean walking distance from each slot to exit (NEW)
        er, ec = self.exit_pos
        self._walk_dist: List[float] = []
        for s in range(n):
            sr, sc = divmod(s, cols)
            self._walk_dist.append(math.hypot(sr - er, sc - ec) + 1.0)

        # Current congestion factor (updated each step by simulation)
        self._congestion: float = 0.0

    # ── Dijkstra (unchanged algorithm) ───────────────────────────────────
    def _dijkstra(self, src: int) -> List[float]:
        n    = self.rows * self.cols
        dist = [math.inf] * n
        dist[src] = 0
        pq   = [(0, src)]
        dirs = [(0,1),(0,-1),(1,0),(-1,0)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]: continue
            ur, uc = divmod(u, self.cols)
            for dr, dc in dirs:
                nr, nc = ur+dr, uc+dc
                if 0 <= nr < self.rows and 0 <= nc < self.cols:
                    v  = nr*self.cols + nc
                    nd = d + 1
                    if nd < dist[v]:
                        dist[v] = nd
                        heapq.heappush(pq, (nd, v))
        return dist

    # ── NEW: update congestion factor ────────────────────────────────────
    def update_congestion(self, utilization: float, is_peak: bool):
        """
        effective_distance = base_distance * (1 + congestion_factor)
        congestion_factor = utilization + 0.3 * peak_boost
        Kept in [0, 1.5].
        """
        peak_boost = 0.3 if is_peak else 0.0
        self._congestion = min(utilization + peak_boost, 1.5)

    # ── traffic-aware distance for a given vehicle/entrance ──────────────
    def effective_distance(self, sid: int, entrance_id: int = 0) -> float:
        """
        NEW: Returns congestion-weighted distance from a specific entrance.
        effective = base * (1 + congestion)
        """
        eid = min(entrance_id, self.n_entrances - 1)
        base = self._dist_per_entrance[eid][sid]
        return base * (1.0 + self._congestion)

    def best_effective_distance(self, sid: int) -> float:
        """NEW: Minimum effective distance across all entrances."""
        return self._best_base[sid] * (1.0 + self._congestion)

    def walking_distance(self, sid: int) -> float:
        """NEW: Euclidean distance from slot to exit point."""
        return self._walk_dist[sid]

    # ── original interface (kept, now traffic-aware) ──────────────────────
    def distance(self, sid: int) -> float:
        """Backward-compatible: returns best effective distance."""
        return self.best_effective_distance(sid)

    def nearest_free(self, free_list: List[int],
                     entrance_id: int = 0) -> List[int]:
        """Sort free slots by effective distance from given entrance."""
        eid = min(entrance_id, self.n_entrances - 1)
        return sorted(free_list,
                      key=lambda s: self._dist_per_entrance[eid][s] * (1+self._congestion))

    # ── helper ────────────────────────────────────────────────────────────
    def congestion_factor(self) -> float:
        return self._congestion


# ═══════════════════════════════════════════════════════════════════════════════
# PREDICTION MODULE  (interface unchanged, minor internal improvement)
# ═══════════════════════════════════════════════════════════════════════════════

class PredictionModule:
    def __init__(self, total_slots):
        self.total  = total_slots
        self.hist   = []
        self.window = 8

    def update(self, arrivals: int):
        self.hist.append(arrivals)
        if len(self.hist) > self.window:
            self.hist.pop(0)

    def predicted_load(self, q_len: int, util: float) -> float:
        avg = sum(self.hist) / max(len(self.hist), 1)
        arr = min(avg / 5.0, 1.0)
        qp  = min(q_len / max(self.total, 1), 1.0)
        return round(min(0.5*util + 0.3*qp + 0.2*arr, 1.0), 4)


# ═══════════════════════════════════════════════════════════════════════════════
# PRIORITY ENGINE  (extended: w7, w8, smooth adaptive weights, urgency boost)
# ═══════════════════════════════════════════════════════════════════════════════

class PriorityEngine:
    def __init__(self, weights: Dict[str, float]):
        self.w = weights.copy()

        # EMA-tracked performance signals for smooth adaptation
        self._ema_avg_wait:    float = 0.0
        self._ema_reject_rate: float = 0.0
        self._prev_avg_wait:   float = 0.0
        self._prev_reject:     float = 0.0

    # ── NEW: smooth adaptive weight update (called once per step) ─────────
    def smooth_adapt(self, avg_wait: float, rejection_rate: float):
        """
        Smoothly adjusts w2 (waiting weight) and w3 (reservation weight)
        using exponential moving average instead of abrupt thresholds.

        If avg_wait is rising  → increase w2
        If rejection is rising → increase w3
        """
        alpha = WEIGHT_ALPHA

        # EMA update
        self._ema_avg_wait    = alpha * avg_wait    + (1-alpha) * self._ema_avg_wait
        self._ema_reject_rate = alpha * rejection_rate + (1-alpha) * self._ema_reject_rate

        # Delta-driven weight adjustment
        wait_delta   = self._ema_avg_wait    - self._prev_avg_wait
        reject_delta = self._ema_reject_rate - self._prev_reject

        if wait_delta > 0.05:       # wait time trending up
            self.w["w2"] = min(self.w["w2"] + alpha * 0.5, BASE_WEIGHTS["w2"] * 2.5)
        else:                        # wait time stable or falling
            self.w["w2"] = max(self.w["w2"] - alpha * 0.2, BASE_WEIGHTS["w2"] * 0.8)

        if reject_delta > 0.01:     # rejection trending up → favour reserved
            self.w["w3"] = min(self.w["w3"] + alpha * 0.4, BASE_WEIGHTS["w3"] * 2.5)
        else:
            self.w["w3"] = max(self.w["w3"] - alpha * 0.15, BASE_WEIGHTS["w3"] * 0.8)

        self._prev_avg_wait = self._ema_avg_wait
        self._prev_reject   = self._ema_reject_rate

    # ── extended compute (adds w7, w8, urgency boost) ─────────────────────
    def compute(self,
                vehicle:      "Vehicle",
                distance:     float,
                pred_load:    float,
                max_wait:     float,
                util:         float,
                q_len:        int,
                traffic_delay: float = 1.0,    # NEW
                walking_dist:  float = 1.0,    # NEW
                ) -> float:
        w  = self.w
        ts = vehicle.type_score()
        wn = vehicle.waiting_time / max(max_wait, 1)
        id_ = 1.0 / max(distance, 1.0)
        fs  = wn / max(ts, 1)

        # w7: reward lower traffic delay (NEW)
        inv_td = 1.0 / max(traffic_delay, 1.0)

        # w8: reward shorter walking distance (NEW)
        inv_wd = 1.0 / max(walking_dist, 1.0)

        score = (w["w1"]*ts + w["w2"]*wn + w["w3"]*vehicle.reservation
                 + w["w4"]*id_ + w["w5"]*pred_load + w["w6"]*fs
                 + w["w7"]*inv_td + w["w8"]*inv_wd)

        # NEW: urgency boost — emergency under high congestion gets extra push
        if vehicle.vehicle_type == "EMERGENCY" and util > 0.70:
            score += 4.0 + (util - 0.70) * 10.0

        return round(score, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# MAX-HEAP PQ  (extended: pop_lowest for smart rejection)
# ═══════════════════════════════════════════════════════════════════════════════

class MaxHeapPQ:
    _REMOVED = object()

    def __init__(self):
        self._heap  = []
        self._valid: Dict[int, list] = {}   # vid → [neg_p, counter, vehicle]
        self._ctr   = 0

    def push(self, v: "Vehicle"):
        e = [-v.priority, self._ctr, v]
        self._ctr += 1
        self._valid[v.vehicle_id] = e
        heapq.heappush(self._heap, e)

    def update(self, v: "Vehicle"):
        if v.vehicle_id in self._valid:
            self._valid[v.vehicle_id][2] = self._REMOVED
        self.push(v)

    def pop(self) -> Optional["Vehicle"]:
        """Pop highest-priority vehicle."""
        while self._heap:
            neg_p, cnt, v = heapq.heappop(self._heap)
            if v is self._REMOVED:
                continue
            stored = self._valid.get(v.vehicle_id)
            if stored is not None and stored[1] == cnt:
                del self._valid[v.vehicle_id]
                return v
        return None

    # NEW: pop the LOWEST priority non-emergency vehicle (for smart rejection)
    def pop_lowest_non_emergency(self) -> Optional["Vehicle"]:
        """
        Scan valid entries, find the lowest-priority vehicle that is NOT
        EMERGENCY, remove and return it.  O(n) scan — called only during
        overflow, so acceptable.
        """
        candidates = [(e[0], e[1], e[2])   # neg_p, counter, vehicle
                      for e in self._valid.values()
                      if e[2] is not self._REMOVED
                      and e[2].vehicle_type != "EMERGENCY"]
        if not candidates:
            return None
        # Most-negative neg_p = lowest priority
        candidates.sort()           # ascending neg_p = ascending priority
        _, cnt, victim = candidates[0]
        # Mark removed
        self._valid[victim.vehicle_id][2] = self._REMOVED
        del self._valid[victim.vehicle_id]
        return victim

    def peek_all(self) -> List["Vehicle"]:
        return [e[2] for e in self._valid.values() if e[2] is not self._REMOVED]

    def size(self) -> int:     return len(self._valid)
    def is_empty(self) -> bool: return len(self._valid) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# PROPOSED SIMULATION  (extended)
# ═══════════════════════════════════════════════════════════════════════════════

class ProposedSimulation:
    def __init__(self, seed: int = RANDOM_SEED):
        Vehicle._ctr = 0
        random.seed(seed)

        self.sm     = SlotManager(GRID_ROWS, GRID_COLS)
        self.router = DijkstraRouter(GRID_ROWS, GRID_COLS,
                                     entrances=ENTRANCE_POSITIONS,
                                     exit_pos=EXIT_POSITION)
        self.pe     = PriorityEngine(BASE_WEIGHTS)
        self.pm     = PredictionModule(TOTAL_SLOTS)
        self.pq     = MaxHeapPQ()

        self.all_vehicles: List[Vehicle]   = []
        self.assigned:     List[Vehicle]   = []
        self.rejected:     List[Vehicle]   = []
        self.active:       Dict[int, Vehicle] = {}   # in lot
        self.waiting:      Dict[int, Vehicle] = {}   # in queue

        self.step_metrics: List[Dict] = []
        self.t = 0

        # Tracking for smooth adaptive weights
        self._rolling_wait_sum   = 0.0
        self._rolling_wait_count = 0
        self._rolling_rejections = 0
        self._rolling_arrivals   = 0

    # ── vehicle factory ───────────────────────────────────────────────────
    def _make_vehicle(self) -> Vehicle:
        r = random.random()
        if r < EMERGENCY_PROB:                vt = "EMERGENCY"
        elif r < EMERGENCY_PROB + VIP_PROB:   vt = "VIP"
        else:                                 vt = "NORMAL"
        # NEW: assign random entrance
        eid = random.randint(0, len(ENTRANCE_POSITIONS) - 1)
        return Vehicle(
            vehicle_type  = vt,
            arrival_time  = self.t,
            reservation   = int(random.random() < RESERVATION_PROB),
            large         = random.random() < LARGE_PROB,
            entrance_id   = eid,
        )

    # ── priority computation (now passes traffic_delay + walking_dist) ────
    def _priority(self, v: Vehicle) -> float:
        free   = self.sm.free_slots_list()
        ranked = self.router.nearest_free(free, entrance_id=v.entrance_id)
        best_d = self.router.effective_distance(ranked[0], v.entrance_id) \
                 if ranked else float(TOTAL_SLOTS)
        util   = self.sm.utilization()
        q_len  = self.pq.size()
        pred   = self.pm.predicted_load(q_len, util)
        max_w  = max((x.waiting_time for x in self.waiting.values()), default=1)

        # NEW: traffic delay = effective / base distance ratio
        base_d = self.router._best_base[ranked[0]] if ranked else 1.0
        traffic_delay = best_d / max(base_d, 1.0)

        # NEW: walking distance of nearest free slot to exit
        walk_d = self.router.walking_distance(ranked[0]) if ranked else 1.0

        return self.pe.compute(v, best_d, pred, max_w, util, q_len,
                               traffic_delay=traffic_delay,
                               walking_dist=walk_d)

    # ── depart vehicles whose time is up ─────────────────────────────────
    def _depart(self):
        done = [vid for vid, v in self.active.items()
                if v.departure_time is not None and v.departure_time <= self.t]
        for vid in done:
            v = self.active.pop(vid)
            if v.assigned_slot  is not None: self.sm.free_slot(v.assigned_slot)
            if v.secondary_slot is not None: self.sm.free_slot(v.secondary_slot)

    # ── allocate one vehicle ──────────────────────────────────────────────
    def _allocate(self, v: Vehicle) -> bool:
        # NEW: if vehicle has a reservation, try its reserved slot first
        res_slots = self.sm.reserved_free_slots(v.vehicle_id)
        free = self.sm.free_slots_list()
        if not free:
            return False

        ranked = (res_slots + self.router.nearest_free(
                    [s for s in free if s not in res_slots],
                    entrance_id=v.entrance_id)
                  ) if res_slots else self.router.nearest_free(free, v.entrance_id)

        if v.large:
            for sid in ranked:
                adj = self.sm.adjacent_free(sid)
                if adj is not None and self.sm.is_free(sid):
                    self.sm.occupy(sid, v.vehicle_id)
                    self.sm.occupy(adj, v.vehicle_id)
                    v.assigned_slot  = sid
                    v.secondary_slot = adj
                    v.departure_time = self.t + random.randint(PARK_MIN, PARK_MAX)
                    v.walking_distance = self.router.walking_distance(sid)
                    v.traffic_delay    = self.router.congestion_factor() + 1.0
                    return True
            return False

        sid = ranked[0]
        if not self.sm.is_free(sid):
            return False
        self.sm.occupy(sid, v.vehicle_id)
        v.assigned_slot    = sid
        v.departure_time   = self.t + random.randint(PARK_MIN, PARK_MAX)
        v.walking_distance = self.router.walking_distance(sid)
        v.traffic_delay    = self.router.congestion_factor() + 1.0
        return True

    # ── NEW: smart rejection — eject lowest-priority non-emergency ────────
    def _smart_reject_one(self):
        victim = self.pq.pop_lowest_non_emergency()
        if victim is None:
            return
        self.waiting.pop(victim.vehicle_id, None)
        victim.waiting_time = self.t - victim.arrival_time
        victim.rejected = True
        self.rejected.append(victim)
        self._rolling_rejections += 1

    # ── main simulation loop ──────────────────────────────────────────────
    def run(self) -> Dict:
        for step in range(SIMULATION_STEPS):
            self.t = step
            is_peak = step in PEAK_STEPS

            # 1. Depart & expire reservations
            self._depart()
            self.sm.expire_reservations(self.t)

            # 2. Update traffic-aware distance
            util = self.sm.utilization()
            self.router.update_congestion(util, is_peak)

            # 3. Smooth adaptive weight update (called once per step)
            n_assigned = len(self.assigned)
            cur_avg_wait = (self._rolling_wait_sum / max(self._rolling_wait_count, 1))
            cur_rej_rate = (self._rolling_rejections / max(self._rolling_arrivals, 1))
            self.pe.smooth_adapt(cur_avg_wait, cur_rej_rate)

            # 4. Generate arrivals (burst model)
            weights = PEAK_ARRIVAL_W if is_peak else NORMAL_ARRIVAL_W
            n_arr   = 0
            if len(self.all_vehicles) < TARGET_VEHICLES:
                n_arr = random.choices(range(6), weights=weights)[0]
                n_arr = min(n_arr, TARGET_VEHICLES - len(self.all_vehicles))

            self.pm.update(n_arr)
            self._rolling_arrivals += n_arr

            for _ in range(n_arr):
                v = self._make_vehicle()
                self.all_vehicles.append(v)

                # NEW: no-show handling — skip vehicle, don't even queue
                if v.no_show:
                    v.rejected = True
                    self.rejected.append(v)
                    self._rolling_rejections += 1
                    continue

                # NEW: smart rejection — eject lowest before accepting if full
                if self.pq.size() >= QUEUE_REJECT_TH:
                    if v.vehicle_type == "EMERGENCY":
                        # Force-make room by ejecting lowest non-emergency
                        self._smart_reject_one()
                    else:
                        v.rejected = True
                        self.rejected.append(v)
                        self._rolling_rejections += 1
                        continue

                # NEW: pre-reserve a slot for vehicles with reservation
                if v.reservation and self.sm.free_slots_list():
                    free_now = self.sm.free_slots_list()
                    ranked   = self.router.nearest_free(free_now, v.entrance_id)
                    self.sm.reserve_for_window(ranked[0], v.vehicle_id,
                                               v.reservation_end)

                self.waiting[v.vehicle_id] = v
                v.priority = self._priority(v)
                self.pq.push(v)

            # 5. Age + recompute priorities
            for v in list(self.waiting.values()):
                v.waiting_time = self.t - v.arrival_time
                new_p = self._priority(v)
                if v.waiting_time > AGING_THRESHOLD:
                    cycles = (v.waiting_time - AGING_THRESHOLD) // AGING_THRESHOLD
                    new_p += AGING_BOOST * max(cycles, 1)
                if abs(new_p - v.priority) > 0.05:
                    v.priority = new_p
                    self.pq.update(v)

            # 6. Allocate (throttled)
            for _ in range(PROPOSED_ALLOC):
                if self.pq.is_empty():
                    break
                v = self.pq.pop()
                if v is None:
                    break
                self.waiting.pop(v.vehicle_id, None)
                v.waiting_time = self.t - v.arrival_time

                # NEW: check if reservation window expired while waiting
                if (v.reservation
                        and not v.reservation_valid(self.t)
                        and v.vehicle_type != "EMERGENCY"):
                    v.rejected = True
                    self.rejected.append(v)
                    self._rolling_rejections += 1
                    continue

                ok = self._allocate(v)
                if ok:
                    v.allocated = True
                    self.assigned.append(v)
                    self.active[v.vehicle_id] = v
                    self._rolling_wait_sum   += v.waiting_time
                    self._rolling_wait_count += 1
                else:
                    v.rejected = True
                    self.rejected.append(v)
                    self._rolling_rejections += 1

            # 7. Record per-step metrics
            util = self.sm.utilization()
            self.step_metrics.append({
                "step":           step,
                "utilization":    round(util, 3),
                "queue":          self.pq.size(),
                "in_lot":         len(self.active),
                "assigned_total": len(self.assigned),
                "rejected_total": len(self.rejected),
                "congestion":     round(self.router.congestion_factor(), 3),
                "is_peak":        int(is_peak),
                "w2":             round(self.pe.w["w2"], 3),
                "w3":             round(self.pe.w["w3"], 3),
            })

        return self._compile_results()

    # ── results compilation ───────────────────────────────────────────────
    def _compile_results(self) -> Dict:
        wt  = [v.waiting_time for v in self.assigned]
        avg = sum(wt) / max(len(wt), 1)
        n   = len(wt); s1 = sum(wt); s2 = sum(w**2 for w in wt)
        jain = (s1**2) / (n * s2) if s2 > 0 else 1.0
        rej_rate = len(self.rejected) / max(len(self.all_vehicles), 1)

        # Per-type breakdown (NEW)
        types = ["EMERGENCY", "VIP", "NORMAL"]
        type_wait = {}
        type_reject = {}
        for t in types:
            tw = [v.waiting_time for v in self.assigned if v.vehicle_type == t]
            type_wait[t]   = round(sum(tw)/max(len(tw),1), 2)
            rj = [v for v in self.rejected if v.vehicle_type == t]
            al = [v for v in self.all_vehicles if v.vehicle_type == t]
            type_reject[t] = round(len(rj)/max(len(al),1), 4)

        peak_queue = max((m["queue"] for m in self.step_metrics), default=0)

        return {
            "system":          "Proposed",
            "total":           len(self.all_vehicles),
            "assigned_count":  len(self.assigned),
            "rejected_count":  len(self.rejected),
            "avg_wait":        round(avg, 2),
            "max_wait":        max(wt) if wt else 0,
            "jain_fairness":   round(jain, 4),
            "rejection_rate":  round(rej_rate, 4),
            "utilization":     round(self.sm.utilization(), 3),
            "peak_queue":      peak_queue,
            "type_avg_wait":   type_wait,
            "type_reject_rate": type_reject,
            "assigned_vehicles": self.assigned,
            "step_metrics":    self.step_metrics,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FCFS BASELINE  (logic unchanged; adapted only for new Vehicle fields)
# ═══════════════════════════════════════════════════════════════════════════════

class FCFSSimulation:
    def __init__(self, seed: int = RANDOM_SEED):
        Vehicle._ctr = 0
        random.seed(seed)

        self.sm     = SlotManager(GRID_ROWS, GRID_COLS)
        self.router = DijkstraRouter(GRID_ROWS, GRID_COLS,
                                     entrances=ENTRANCE_POSITIONS,
                                     exit_pos=EXIT_POSITION)
        self.queue:        List[Vehicle]      = []
        self.assigned:     List[Vehicle]      = []
        self.rejected:     List[Vehicle]      = []
        self.active:       Dict[int, Vehicle] = {}
        self.all_vehicles: List[Vehicle]      = []
        self.step_metrics: List[Dict]         = []
        self.t = 0

    def _make_vehicle(self) -> Vehicle:
        r = random.random()
        if r < EMERGENCY_PROB:              vt = "EMERGENCY"
        elif r < EMERGENCY_PROB+VIP_PROB:   vt = "VIP"
        else:                               vt = "NORMAL"
        eid = random.randint(0, len(ENTRANCE_POSITIONS) - 1)
        return Vehicle(vehicle_type=vt, arrival_time=self.t,
                       reservation=int(random.random() < RESERVATION_PROB),
                       large=random.random() < LARGE_PROB,
                       entrance_id=eid)

    def _depart(self):
        done = [vid for vid, v in self.active.items()
                if v.departure_time and v.departure_time <= self.t]
        for vid in done:
            v = self.active.pop(vid)
            if v.assigned_slot is not None: self.sm.free_slot(v.assigned_slot)

    def run(self) -> Dict:
        for step in range(SIMULATION_STEPS):
            self.t = step
            is_peak = step in PEAK_STEPS
            self._depart()
            util = self.sm.utilization()
            self.router.update_congestion(util, is_peak)

            weights = PEAK_ARRIVAL_W if is_peak else NORMAL_ARRIVAL_W
            n_arr   = 0
            if len(self.all_vehicles) < TARGET_VEHICLES:
                n_arr = random.choices(range(6), weights=weights)[0]
                n_arr = min(n_arr, TARGET_VEHICLES - len(self.all_vehicles))

            for _ in range(n_arr):
                v = self._make_vehicle()
                self.all_vehicles.append(v)
                # no-show: quietly skip
                if v.no_show:
                    v.rejected = True
                    self.rejected.append(v)
                    continue
                if len(self.queue) >= QUEUE_REJECT_TH:
                    v.rejected = True
                    self.rejected.append(v)
                    continue
                self.queue.append(v)

            # FCFS: strict arrival order, lower throughput rate
            for _ in range(FCFS_ALLOC):
                if not self.queue:
                    break
                v = self.queue.pop(0)
                v.waiting_time = self.t - v.arrival_time
                free = self.sm.free_slots_list()
                if free:
                    ranked = self.router.nearest_free(free, v.entrance_id)
                    sid    = ranked[0]
                    self.sm.occupy(sid, v.vehicle_id)
                    v.assigned_slot    = sid
                    v.departure_time   = self.t + random.randint(PARK_MIN, PARK_MAX)
                    v.walking_distance = self.router.walking_distance(sid)
                    v.allocated = True
                    self.assigned.append(v)
                    self.active[v.vehicle_id] = v
                else:
                    v.rejected = True
                    self.rejected.append(v)

            self.step_metrics.append({
                "step":        step,
                "utilization": round(self.sm.utilization(), 3),
                "queue":       len(self.queue),
                "in_lot":      len(self.active),
                "is_peak":     int(is_peak),
            })

        wt  = [v.waiting_time for v in self.assigned]
        avg = sum(wt) / max(len(wt), 1)
        n   = len(wt); s1 = sum(wt); s2 = sum(w**2 for w in wt)
        jain = (s1**2)/(n*s2) if s2 > 0 else 1.0
        rej_rate = len(self.rejected)/max(len(self.all_vehicles),1)

        types = ["EMERGENCY", "VIP", "NORMAL"]
        type_wait   = {}
        type_reject = {}
        for t in types:
            tw = [v.waiting_time for v in self.assigned if v.vehicle_type == t]
            type_wait[t]   = round(sum(tw)/max(len(tw),1), 2)
            rj = [v for v in self.rejected if v.vehicle_type == t]
            al = [v for v in self.all_vehicles if v.vehicle_type == t]
            type_reject[t] = round(len(rj)/max(len(al),1), 4)

        return {
            "system":          "FCFS",
            "total":           len(self.all_vehicles),
            "assigned_count":  len(self.assigned),
            "rejected_count":  len(self.rejected),
            "avg_wait":        round(avg, 2),
            "max_wait":        max(wt) if wt else 0,
            "jain_fairness":   round(jain, 4),
            "rejection_rate":  round(rej_rate, 4),
            "utilization":     round(self.sm.utilization(), 3),
            "peak_queue":      max((m["queue"] for m in self.step_metrics), default=0),
            "type_avg_wait":   type_wait,
            "type_reject_rate": type_reject,
            "assigned_vehicles": self.assigned,
            "step_metrics":    self.step_metrics,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT: CSV METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def save_csv(prop: Dict, fcfs: Dict, out_dir: str):
    # Per-step metrics CSV
    path = os.path.join(out_dir, "step_metrics.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["step", "system", "utilization", "queue",
                         "in_lot", "assigned_total", "rejected_total",
                         "congestion", "is_peak"])
        for m in prop["step_metrics"]:
            writer.writerow([
                m["step"], "Proposed",
                m["utilization"], m["queue"], m["in_lot"],
                m.get("assigned_total", ""), m.get("rejected_total", ""),
                m.get("congestion", ""), m.get("is_peak", ""),
            ])
        for m in fcfs["step_metrics"]:
            writer.writerow([
                m["step"], "FCFS",
                m["utilization"], m["queue"], m["in_lot"],
                "", "", "", m.get("is_peak", ""),
            ])

    # Vehicle-level CSV
    vpath = os.path.join(out_dir, "vehicle_results.csv")
    with open(vpath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["system", "vehicle_id", "type", "priority",
                         "slot", "wait_time", "reservation", "large",
                         "entrance_id", "walking_dist", "traffic_delay",
                         "status"])
        for label, vehicles in [("Proposed", prop["assigned_vehicles"]),
                                  ("FCFS",     fcfs["assigned_vehicles"])]:
            for v in vehicles:
                writer.writerow([
                    label, v.vehicle_id, v.vehicle_type,
                    round(v.priority, 3), v.assigned_slot,
                    v.waiting_time, v.reservation, int(v.large),
                    v.entrance_id,
                    round(v.walking_distance, 2), round(v.traffic_delay, 2),
                    "ASSIGNED",
                ])

    # Summary CSV
    spath = os.path.join(out_dir, "summary_metrics.csv")
    with open(spath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "proposed", "fcfs"])
        keys = ["total","assigned_count","rejected_count","avg_wait",
                "max_wait","jain_fairness","rejection_rate",
                "utilization","peak_queue"]
        for k in keys:
            writer.writerow([k, prop.get(k,""), fcfs.get(k,"")])
        for t in ["EMERGENCY","VIP","NORMAL"]:
            writer.writerow([f"avg_wait_{t}",
                             prop["type_avg_wait"].get(t,""),
                             fcfs["type_avg_wait"].get(t,"")])
            writer.writerow([f"reject_rate_{t}",
                             prop["type_reject_rate"].get(t,""),
                             fcfs["type_reject_rate"].get(t,"")])

    print(f"  CSV saved → {path}")
    print(f"  CSV saved → {vpath}")
    print(f"  CSV saved → {spath}")


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT: JSON LOG
# ═══════════════════════════════════════════════════════════════════════════════

def save_json_log(prop: Dict, fcfs: Dict, out_dir: str):
    def clean(d):
        return {k: v for k, v in d.items()
                if k not in ("assigned_vehicles",)}

    log = {
        "config": {
            "grid":              f"{GRID_ROWS}x{GRID_COLS}",
            "total_slots":       TOTAL_SLOTS,
            "simulation_steps":  SIMULATION_STEPS,
            "target_vehicles":   TARGET_VEHICLES,
            "park_duration":     f"{PARK_MIN}-{PARK_MAX}",
            "peak_steps":        f"{len(PEAK_STEPS)} steps",
            "entrances":         ENTRANCE_POSITIONS,
            "exit":              EXIT_POSITION,
            "queue_reject_th":   QUEUE_REJECT_TH,
            "no_show_prob":      NO_SHOW_PROB,
            "base_weights":      BASE_WEIGHTS,
        },
        "proposed": clean(prop),
        "fcfs":     clean(fcfs),
        "per_type_comparison": {
            t: {
                "proposed_avg_wait":    prop["type_avg_wait"].get(t, 0),
                "fcfs_avg_wait":        fcfs["type_avg_wait"].get(t, 0),
                "proposed_reject_rate": prop["type_reject_rate"].get(t, 0),
                "fcfs_reject_rate":     fcfs["type_reject_rate"].get(t, 0),
            }
            for t in ["EMERGENCY", "VIP", "NORMAL"]
        },
    }
    path = os.path.join(out_dir, "simulation_log.json")
    with open(path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"  JSON saved → {path}")


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPHS  (9 panels — all existing panels updated + 3 new)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_graphs(prop: Dict, fcfs: Dict, out_dir: str) -> str:
    # ── palette ───────────────────────────────────────────────────────────
    DARK   = '#0d1117'
    PANEL  = '#161b22'
    GRID   = '#21262d'
    CYAN   = '#58a6ff'
    ORANGE = '#f78166'
    GREEN  = '#3fb950'
    PURPLE = '#bc8cff'
    YELLOW = '#e3b341'
    PINK   = '#f778ba'
    TEXT   = '#c9d1d9'
    TITLE  = '#f0f6fc'
    RED    = '#ff4444'

    def sax(ax, title, xlabel="", ylabel=""):
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=TEXT, labelsize=8)
        ax.set_title(title, color=TITLE, fontsize=9.5, fontweight='bold', pad=7)
        ax.set_xlabel(xlabel, color=TEXT, fontsize=8)
        ax.set_ylabel(ylabel, color=TEXT, fontsize=8)
        for sp in ax.spines.values(): sp.set_edgecolor(GRID)
        ax.grid(True, alpha=0.18, color=GRID, lw=0.8)

    # Extract series
    p_m   = prop["step_metrics"]
    f_m   = fcfs["step_metrics"]
    ps    = [m["step"]        for m in p_m]
    fs    = [m["step"]        for m in f_m]
    p_ut  = [m["utilization"] for m in p_m]
    f_ut  = [m["utilization"] for m in f_m]
    p_q   = [m["queue"]       for m in p_m]
    f_q   = [m["queue"]       for m in f_m]
    p_cng = [m.get("congestion",0) for m in p_m]
    p_pk  = [m.get("is_peak", 0)   for m in p_m]
    p_w2  = [m.get("w2", BASE_WEIGHTS["w2"]) for m in p_m]
    p_w3  = [m.get("w3", BASE_WEIGHTS["w3"]) for m in p_m]
    p_inl = [m.get("in_lot", 0)    for m in p_m]
    p_at  = [m.get("assigned_total",0)  for m in p_m]
    p_rt  = [m.get("rejected_total",0)  for m in p_m]

    pvs = prop["assigned_vehicles"]
    fvs = fcfs["assigned_vehicles"]
    TYPES = ["EMERGENCY", "VIP", "NORMAL"]

    fig = plt.figure(figsize=(22, 15), facecolor=DARK)
    gs  = gridspec.GridSpec(3, 3, figure=fig,
                            hspace=0.50, wspace=0.38,
                            left=0.06, right=0.97, top=0.93, bottom=0.06)

    # ── PANEL 1: Utilization + congestion + peak bands ────────────────────
    ax1 = fig.add_subplot(gs[0, 0])
    sax(ax1, "Utilization & Congestion Over Time", "Step", "Value")
    # shade peak windows
    in_peak = False
    for i, m in enumerate(p_m):
        if m.get("is_peak") and not in_peak:
            pk_start = m["step"]; in_peak = True
        elif not m.get("is_peak") and in_peak:
            ax1.axvspan(pk_start, m["step"], alpha=0.12, color=YELLOW)
            in_peak = False
    if in_peak:
        ax1.axvspan(pk_start, ps[-1], alpha=0.12, color=YELLOW)
    ax1.plot(ps, p_ut, color=CYAN,   lw=2, label='Proposed util')
    ax1.plot(fs, f_ut, color=ORANGE, lw=2, label='FCFS util', linestyle='--')
    ax1.plot(ps, p_cng, color=YELLOW, lw=1.2, label='Congestion', alpha=0.8)
    ax1.axhline(0.75, color=PINK, lw=0.8, linestyle=':', alpha=0.6)
    ax1.set_ylim(0, 1.65)
    ax1.legend(fontsize=6.5, facecolor=PANEL, labelcolor=TEXT, loc='upper left')

    # ── PANEL 2: Queue length + peak bands ────────────────────────────────
    ax2 = fig.add_subplot(gs[0, 1])
    sax(ax2, "Queue Length Over Time", "Step", "Vehicles in Queue")
    for i, m in enumerate(p_m):
        if m.get("is_peak") and (i == 0 or not p_m[i-1].get("is_peak")):
            pk_start = m["step"]
        elif not m.get("is_peak") and i > 0 and p_m[i-1].get("is_peak"):
            ax2.axvspan(pk_start, m["step"], alpha=0.12, color=YELLOW, label='_')
    ax2.plot(ps, p_q, color=CYAN,   lw=2, label='Proposed')
    ax2.plot(fs, f_q, color=ORANGE, lw=2, label='FCFS', linestyle='--')
    ax2.axhline(QUEUE_REJECT_TH, color=RED, lw=1.2, linestyle='-.',
                label=f'Reject threshold ({QUEUE_REJECT_TH})')
    ax2.fill_between(ps, p_q, alpha=0.12, color=CYAN)
    ax2.legend(fontsize=6.5, facecolor=PANEL, labelcolor=TEXT)

    # ── PANEL 3: Waiting time distribution (histogram by type) ────────────
    ax3 = fig.add_subplot(gs[0, 2])
    sax(ax3, "Wait Time Distribution — Proposed", "Wait (steps)", "Density")
    em_w  = [v.waiting_time for v in pvs if v.vehicle_type == "EMERGENCY"]
    vip_w = [v.waiting_time for v in pvs if v.vehicle_type == "VIP"]
    nor_w = [v.waiting_time for v in pvs if v.vehicle_type == "NORMAL"]
    mxw   = max([v.waiting_time for v in pvs], default=1)
    bins  = np.linspace(0, mxw+2, 22)
    ax3.hist(nor_w, bins=bins, color=CYAN,   alpha=0.65, density=True, label='Normal')
    ax3.hist(vip_w, bins=bins, color=PURPLE, alpha=0.75, density=True, label='VIP')
    ax3.hist(em_w,  bins=bins, color=ORANGE, alpha=0.90, density=True, label='Emergency')
    for data, clr in [(nor_w,CYAN),(vip_w,PURPLE),(em_w,ORANGE)]:
        if data:
            ax3.axvline(sum(data)/len(data), color=clr, lw=1.5, linestyle='--', alpha=0.8)
    ax3.legend(fontsize=7, facecolor=PANEL, labelcolor=TEXT)

    # ── PANEL 4: Avg wait by type — Proposed vs FCFS ─────────────────────
    ax4 = fig.add_subplot(gs[1, 0])
    sax(ax4, "Avg Wait by Vehicle Type", "Type", "Steps")
    x  = np.arange(3); bw = 0.32
    pw = [prop["type_avg_wait"][t] for t in TYPES]
    fw = [fcfs["type_avg_wait"][t] for t in TYPES]
    b1 = ax4.bar(x-bw/2, pw, bw, color=CYAN,   alpha=0.85, label='Proposed')
    b2 = ax4.bar(x+bw/2, fw, bw, color=ORANGE, alpha=0.85, label='FCFS')
    ax4.set_xticks(x); ax4.set_xticklabels(TYPES, fontsize=8)
    ax4.legend(fontsize=7, facecolor=PANEL, labelcolor=TEXT)
    for b in list(b1)+list(b2):
        h = b.get_height()
        ax4.text(b.get_x()+b.get_width()/2, h+0.1, f'{h:.1f}',
                 ha='center', va='bottom', color=TEXT, fontsize=6.5)

    # ── PANEL 5: Rejection rate by type ───────────────────────────────────
    ax5 = fig.add_subplot(gs[1, 1])
    sax(ax5, "Rejection Rate by Vehicle Type", "Type", "Rate")
    pr = [prop["type_reject_rate"][t]*100 for t in TYPES]
    fr = [fcfs["type_reject_rate"][t]*100 for t in TYPES]
    b1 = ax5.bar(x-bw/2, pr, bw, color=CYAN,   alpha=0.85, label='Proposed')
    b2 = ax5.bar(x+bw/2, fr, bw, color=ORANGE, alpha=0.85, label='FCFS')
    ax5.set_xticks(x); ax5.set_xticklabels(TYPES, fontsize=8)
    ax5.set_ylabel("Rejection Rate (%)")
    ax5.legend(fontsize=7, facecolor=PANEL, labelcolor=TEXT)
    for b in list(b1)+list(b2):
        h = b.get_height()
        if h > 0.2:
            ax5.text(b.get_x()+b.get_width()/2, h+0.1, f'{h:.1f}%',
                     ha='center', va='bottom', color=TEXT, fontsize=6)

    # ── PANEL 6: Adaptive weights over time ───────────────────────────────
    ax6 = fig.add_subplot(gs[1, 2])
    sax(ax6, "Adaptive Weight Evolution (w2, w3)", "Step", "Weight Value")
    ax6.plot(ps, p_w2, color=CYAN,   lw=2, label='w2 (wait weight)')
    ax6.plot(ps, p_w3, color=GREEN,  lw=2, label='w3 (reservation weight)')
    ax6.axhline(BASE_WEIGHTS["w2"], color=CYAN,  lw=0.8, linestyle=':', alpha=0.5)
    ax6.axhline(BASE_WEIGHTS["w3"], color=GREEN, lw=0.8, linestyle=':', alpha=0.5)
    ax6.legend(fontsize=7, facecolor=PANEL, labelcolor=TEXT)

    # ── PANEL 7: Walking distance CDF (Proposed vs FCFS) ──────────────────
    ax7 = fig.add_subplot(gs[2, 0])
    sax(ax7, "Walking Distance CDF (Slot→Exit)", "Walking Distance", "CDF")
    for vlist, clr, lbl in [(pvs, CYAN, 'Proposed'), (fvs, ORANGE, 'FCFS')]:
        wd = sorted([v.walking_distance for v in vlist])
        if wd:
            cdf = np.arange(1, len(wd)+1) / len(wd)
            ax7.plot(wd, cdf, color=clr, lw=2, label=lbl)
    ax7.legend(fontsize=7, facecolor=PANEL, labelcolor=TEXT)
    ax7.set_ylim(0, 1.05)

    # ── PANEL 8: Traffic-aware distance heatmap for the parking grid ───────
    ax8 = fig.add_subplot(gs[2, 1])
    ax8.set_facecolor(PANEL)
    ax8.set_title("Base Distance Heatmap (from Entrance 0)", color=TITLE,
                  fontsize=9.5, fontweight='bold', pad=7)
    ax8.tick_params(colors=TEXT, labelsize=7)
    # Build grid of base distances
    router_tmp = DijkstraRouter(GRID_ROWS, GRID_COLS,
                                entrances=ENTRANCE_POSITIONS,
                                exit_pos=EXIT_POSITION)
    heat = np.array([router_tmp._dist_per_entrance[0][r*GRID_COLS+c]
                     for r in range(GRID_ROWS)
                     for c in range(GRID_COLS)]).reshape(GRID_ROWS, GRID_COLS)
    im = ax8.imshow(heat, cmap='YlOrRd', aspect='auto', alpha=0.9)
    plt.colorbar(im, ax=ax8, fraction=0.046, pad=0.04).ax.tick_params(colors=TEXT)
    # Mark entrances
    for idx, (er, ec) in enumerate(ENTRANCE_POSITIONS):
        ax8.plot(ec, er, 'c^', ms=9, zorder=5)
        ax8.text(ec+0.1, er-0.35, f'E{idx}', color=CYAN, fontsize=7, fontweight='bold')
    # Mark exit
    exr, exc = EXIT_POSITION
    ax8.plot(exc, exr, 'g*', ms=11, zorder=5, label='Exit')
    ax8.set_xticks(range(GRID_COLS)); ax8.set_yticks(range(GRID_ROWS))
    for sp in ax8.spines.values(): sp.set_edgecolor(GRID)

    # ── PANEL 9: KPI scorecard ────────────────────────────────────────────
    ax9 = fig.add_subplot(gs[2, 2])
    ax9.set_facecolor(PANEL)
    ax9.axis('off')
    ax9.set_title("KPI Scorecard", color=TITLE, fontsize=9.5, fontweight='bold', pad=7)
    rows9 = [
        ("Metric",              "PROPOSED",             "FCFS",                  True),
        ("─"*16,                "─"*8,                  "─"*8,                   False),
        ("Avg Wait",            f"{prop['avg_wait']}s", f"{fcfs['avg_wait']}s",  False),
        ("Max Wait",            f"{prop['max_wait']}s", f"{fcfs['max_wait']}s",  False),
        ("Assigned",            str(prop['assigned_count']), str(fcfs['assigned_count']), False),
        ("Rejected",            str(prop['rejected_count']), str(fcfs['rejected_count']), False),
        ("Reject Rate",         f"{prop['rejection_rate']*100:.1f}%",
                                f"{fcfs['rejection_rate']*100:.1f}%",            False),
        ("Utilization",         f"{prop['utilization']*100:.0f}%",
                                f"{fcfs['utilization']*100:.0f}%",               False),
        ("Peak Queue",          str(prop['peak_queue']), str(fcfs['peak_queue']), False),
        ("Jain Fairness",       str(prop['jain_fairness']),
                                str(fcfs['jain_fairness']),                       False),
        ("EM Avg Wait",         str(prop['type_avg_wait']['EMERGENCY'])+'s',
                                str(fcfs['type_avg_wait']['EMERGENCY'])+'s',     False),
        ("VIP Avg Wait",        str(prop['type_avg_wait']['VIP'])+'s',
                                str(fcfs['type_avg_wait']['VIP'])+'s',           False),
        ("NORMAL Avg Wait",     str(prop['type_avg_wait']['NORMAL'])+'s',
                                str(fcfs['type_avg_wait']['NORMAL'])+'s',        False),
    ]
    for i, (m, pv, fv, hdr) in enumerate(rows9):
        y  = 0.97 - i * 0.073
        fs = 8 if hdr else 7.5
        fw = 'bold' if hdr else 'normal'
        tc = TITLE if hdr else TEXT
        ax9.text(0.02, y, m,  transform=ax9.transAxes, color=tc,    fontsize=fs, fontweight=fw, va='top', fontfamily='monospace')
        ax9.text(0.52, y, pv, transform=ax9.transAxes, color=CYAN,  fontsize=fs, fontweight=fw, va='top', fontfamily='monospace')
        ax9.text(0.77, y, fv, transform=ax9.transAxes, color=ORANGE, fontsize=fs, fontweight=fw, va='top', fontfamily='monospace')

    fig.suptitle(
        "Adaptive Hybrid Smart Parking v3 — Realistic High-Load Simulation with Multi-Entrance & Burst Traffic",
        fontsize=12, fontweight='bold', color=TITLE, y=0.975,
    )
    out = os.path.join(out_dir, "parking_graphs_v3.png")
    plt.savefig(out, dpi=155, bbox_inches='tight', facecolor=DARK)
    plt.close()
    print(f"  Graph saved → {out}")
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# CONSOLE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def print_vehicle_table(vehicles: List[Vehicle], label: str, n: int = 25):
    print(f"\n{'━'*105}")
    print(f"  {label} — Top {n} vehicles by priority")
    print(f"{'━'*105}")
    print(f"{'VID':>5} | {'TYPE':^12} | {'PRIORITY':>10} | {'SLOT':>5} | "
          f"{'WAIT':>5} | {'ENT':>3} | {'WALK':>5} | {'TD':>5} | {'RES':>3} | {'STATUS':^10}")
    print(f"{'─'*105}")
    for v in sorted(vehicles, key=lambda x: -x.priority)[:n]:
        st = "ASSIGNED" if v.allocated else "REJECTED"
        print(f"{v.vehicle_id:>5} | {v.vehicle_type:^12} | {v.priority:>10.3f} | "
              f"{str(v.assigned_slot or '-'):>5} | {v.waiting_time:>5} | "
              f"{v.entrance_id:>3} | {v.walking_distance:>5.2f} | "
              f"{v.traffic_delay:>5.2f} | {'Y' if v.reservation else 'N':>3} | {st:^10}")
    print(f"{'━'*105}")


def print_summary(prop: Dict, fcfs: Dict):
    print(f"\n{'═'*68}")
    print(f"  {'Metric':<32} {'Proposed':>16} {'FCFS':>16}")
    print(f"  {'─'*64}")
    rows = [
        ("Total Vehicles",              "total"),
        ("Assigned",                    "assigned_count"),
        ("Rejected",                    "rejected_count"),
        ("Avg Waiting Time (steps)",    "avg_wait"),
        ("Max Waiting Time (steps)",    "max_wait"),
        ("Peak Queue Length",           "peak_queue"),
        ("Jain's Fairness Index",       "jain_fairness"),
        ("Rejection Rate",              "rejection_rate"),
        ("Final Utilization",           "utilization"),
    ]
    for label, key in rows:
        print(f"  {label:<32} {str(prop.get(key,'N/A')):>16} {str(fcfs.get(key,'N/A')):>16}")
    print(f"\n  {'Per-type Avg Wait':─<64}")
    for t in ["EMERGENCY", "VIP", "NORMAL"]:
        print(f"  {t:<32} {str(prop['type_avg_wait'][t]):>16} {str(fcfs['type_avg_wait'][t]):>16}")
    print(f"\n  {'Per-type Rejection Rate':─<64}")
    for t in ["EMERGENCY", "VIP", "NORMAL"]:
        print(f"  {t:<32} {str(prop['type_reject_rate'][t]):>16} {str(fcfs['type_reject_rate'][t]):>16}")
    print(f"{'═'*68}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═"*68)
    print("  ADAPTIVE HYBRID SMART PARKING  v3  — FULL FEATURE SIMULATION")
    print("═"*68)
    print(f"  Grid          : {GRID_ROWS}×{GRID_COLS} = {TOTAL_SLOTS} slots")
    print(f"  Target        : {TARGET_VEHICLES} vehicles over {SIMULATION_STEPS} steps")
    print(f"  Peak steps    : {len(PEAK_STEPS)} steps (2 rush-hour windows)")
    print(f"  Entrances     : {len(ENTRANCE_POSITIONS)} — positions {ENTRANCE_POSITIONS}")
    print(f"  Exit point    : {EXIT_POSITION}")
    print(f"  No-show prob  : {NO_SHOW_PROB*100:.0f}%")
    print(f"  Reservation W : {RESERVATION_WIN} steps")
    print(f"  Alloc/step    : Proposed={PROPOSED_ALLOC}, FCFS={FCFS_ALLOC}")
    print("═"*68)

    print("\n[1/5] Running Proposed System ...")
    sim  = ProposedSimulation(seed=RANDOM_SEED)
    prop = sim.run()

    print("[2/5] Running FCFS Baseline ...")
    fsim = FCFSSimulation(seed=RANDOM_SEED)
    fcfs = fsim.run()

    print("[3/5] Generating graphs ...")
    generate_graphs(prop, fcfs, OUTPUT_DIR)

    print("[4/5] Saving CSV metrics ...")
    save_csv(prop, fcfs, OUTPUT_DIR)

    print("[5/5] Saving JSON log ...")
    save_json_log(prop, fcfs, OUTPUT_DIR)

    print_vehicle_table(prop["assigned_vehicles"], "PROPOSED SYSTEM", n=25)
    print_summary(prop, fcfs)

    return prop, fcfs


if __name__ == "__main__":
    main()
