import streamlit as st
import pandas as pd
import heapq
import random
import math
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ────────────────────────────────────────────────
# PAGE CONFIG
# ────────────────────────────────────────────────
st.set_page_config(
    page_title="🚗 Smart Parking System",
    layout="wide",
    page_icon="🚗",
    initial_sidebar_state="expanded"
)

# ────────────────────────────────────────────────
# CUSTOM CSS — dark, premium look
# ────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    .main { background-color: #0f1117; }

    .metric-card {
        background: linear-gradient(135deg, #1e2130, #252a3d);
        border: 1px solid #2e3455;
        border-radius: 12px;
        padding: 16px 20px;
        text-align: center;
    }
    .metric-card h2 { color: #a78bfa; font-size: 2rem; margin: 0; }
    .metric-card p  { color: #8892b0; font-size: 0.8rem; margin: 4px 0 0 0; }

    .log-emergency { color: #ff6b6b; font-weight: bold; }
    .log-vip       { color: #ffd93d; font-weight: bold; }
    .log-normal    { color: #6bcb77; }

    .concept-box {
        background: linear-gradient(135deg, #1a1f35, #1e2440);
        border-left: 4px solid #a78bfa;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 6px 0;
        font-size: 0.85rem;
        color: #ccd6f6;
    }

    div[data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }
    div[data-testid="stButton"] > button {
        width: 100%;
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.2s;
    }
    .stProgress .st-bo { background-color: #a78bfa; }
</style>
""", unsafe_allow_html=True)


# ────────────────────────────────────────────────
# CLASSES
# ────────────────────────────────────────────────
class Vehicle:
    def __init__(self, vid, vtype, arrival, reservation):
        self.id          = vid
        self.type        = vtype        # 3=Emergency, 2=VIP, 1=Normal
        self.arrival     = arrival
        self.waiting     = 0
        self.reservation = reservation  # 0 or 1
        self.priority    = 0
        self.type_label  = {3: "🚨 Emergency", 2: "⭐ VIP", 1: "🚗 Normal"}[vtype]
        self.type_name   = {3: "Emergency",    2: "VIP",    1: "Normal"}[vtype]

    def compute_priority(self):
        self.priority = 5 * self.type + 3 * self.waiting + 2 * self.reservation
        return -self.priority   # negative → max-heap via heapq (min-heap)

    def __lt__(self, other):    # tiebreak for heapq
        return self.id < other.id


class Slot:
    def __init__(self, sid, row, col):
        self.id         = sid
        self.row        = row
        self.col        = col
        self.occupied   = False
        self.vehicle_id = None


def euclidean(s):
    """Distance from entrance (0,0) to slot grid position."""
    return math.sqrt(s.row ** 2 + s.col ** 2)


def find_nearest_slot(slots):
    best, best_dist = None, float("inf")
    for s in slots:
        if not s.occupied:
            d = euclidean(s)
            if d < best_dist:
                best_dist = d
                best = s
    return best, round(best_dist, 2)


# ────────────────────────────────────────────────
# SIMULATION INIT
# ────────────────────────────────────────────────
ROWS, COLS = 4, 5   # 20 slot grid

def init_sim(num_vehicles):
    random.seed(42)
    slots = []
    for r in range(ROWS):
        for c in range(COLS):
            slots.append(Slot(r * COLS + c, r, c))

    heap, vehicles = [], []
    for i in range(num_vehicles):
        vtype       = random.choices([3, 2, 1], weights=[10, 20, 70])[0]
        reservation = random.choice([0, 1])
        v = Vehicle(i + 1, vtype, i, reservation)
        vehicles.append(v)
        heapq.heappush(heap, (v.compute_priority(), v))

    st.session_state.slots    = slots
    st.session_state.vehicles = vehicles
    st.session_state.heap     = heap
    st.session_state.logs     = []
    st.session_state.step     = 0
    st.session_state.done     = False
    st.session_state.aging_events = []


# ────────────────────────────────────────────────
# ONE STEP OF SIMULATION
# ────────────────────────────────────────────────
def run_step():
    if not st.session_state.heap:
        st.session_state.done = True
        return

    _, v = heapq.heappop(st.session_state.heap)
    slot, dist = find_nearest_slot(st.session_state.slots)

    step_num = st.session_state.step + 1

    if slot:
        slot.occupied   = True
        slot.vehicle_id = v.id
        st.session_state.logs.insert(0, {
            "Step":      step_num,
            "Status":    "✅ Allocated",
            "Vehicle":   v.type_label + f" #{v.id}",
            "Type":      v.type_name,
            "Priority":  v.priority,
            "Slot":      f"({slot.row},{slot.col})",
            "Distance":  dist,
            "Waited":    v.waiting,
            "Reserved":  "Yes" if v.reservation else "No",
        })
    else:
        # Lot full — push back with aging
        v.waiting += 1
        heapq.heappush(st.session_state.heap, (v.compute_priority(), v))
        st.session_state.logs.insert(0, {
            "Step":      step_num,
            "Status":    "⏳ Waiting",
            "Vehicle":   v.type_label + f" #{v.id}",
            "Type":      v.type_name,
            "Priority":  v.priority,
            "Slot":      "—",
            "Distance":  "—",
            "Waited":    v.waiting,
            "Reserved":  "Yes" if v.reservation else "No",
        })

    # ── Aging: boost priority of all remaining vehicles ──
    aged = []
    temp_heap = []
    while st.session_state.heap:
        _, w = heapq.heappop(st.session_state.heap)
        old_p = w.priority
        w.waiting += 1
        new_p = w.priority  # compute_priority() updates self.priority
        w.compute_priority()
        if w.waiting > 2:   # aging kicks in after 2 waits
            aged.append((w.id, w.type_label, old_p, w.priority))
        heapq.heappush(temp_heap, (w.compute_priority(), w))
    st.session_state.heap = temp_heap
    st.session_state.aging_events = aged
    st.session_state.step = step_num


def run_all():
    while st.session_state.heap:
        _, v = heapq.heappop(st.session_state.heap)
        slot, dist = find_nearest_slot(st.session_state.slots)
        step_num = st.session_state.step + 1
        if slot:
            slot.occupied   = True
            slot.vehicle_id = v.id
            st.session_state.logs.insert(0, {
                "Step":      step_num,
                "Status":    "✅ Allocated",
                "Vehicle":   v.type_label + f" #{v.id}",
                "Type":      v.type_name,
                "Priority":  v.priority,
                "Slot":      f"({slot.row},{slot.col})",
                "Distance":  dist,
                "Waited":    v.waiting,
                "Reserved":  "Yes" if v.reservation else "No",
            })
        else:
            st.session_state.logs.insert(0, {
                "Step":      step_num,
                "Status":    "❌ Rejected (Full)",
                "Vehicle":   v.type_label + f" #{v.id}",
                "Type":      v.type_name,
                "Priority":  v.priority,
                "Slot":      "—",
                "Distance":  "—",
                "Waited":    v.waiting,
                "Reserved":  "Yes" if v.reservation else "No",
            })
            break
        # Age remaining
        temp_heap = []
        while st.session_state.heap:
            _, w = heapq.heappop(st.session_state.heap)
            w.waiting += 1
            w.compute_priority()
            heapq.heappush(temp_heap, (w.compute_priority(), w))
        st.session_state.heap = temp_heap
        st.session_state.step = step_num
    st.session_state.done = True


# ────────────────────────────────────────────────
# PARKING GRID (matplotlib)
# ────────────────────────────────────────────────
def draw_grid():
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")

    slots = st.session_state.slots

    for s in slots:
        if s.occupied:
            color  = "#ff6b6b"
            label  = f"V{s.vehicle_id}"
            alpha  = 0.9
        else:
            color  = "#4ade80"
            label  = f"S{s.id}"
            alpha  = 0.6

        rect = mpatches.FancyBboxPatch(
            (s.col + 0.05, ROWS - 1 - s.row + 0.05),
            0.85, 0.85,
            boxstyle="round,pad=0.05",
            facecolor=color,
            edgecolor="#1e2130",
            linewidth=2,
            alpha=alpha
        )
        ax.add_patch(rect)
        ax.text(s.col + 0.48, ROWS - 1 - s.row + 0.48, label,
                ha="center", va="center", fontsize=7,
                color="white", fontweight="bold")

    # Entrance arrow
    ax.annotate("ENTRANCE\n→", xy=(0.48, ROWS - 0.5),
                xytext=(-0.9, ROWS - 0.5),
                arrowprops=dict(arrowstyle="->", color="#a78bfa", lw=2),
                color="#a78bfa", fontsize=8, fontweight="bold",
                ha="center", va="center")

    ax.set_xlim(-1.2, COLS + 0.2)
    ax.set_ylim(-0.3, ROWS + 0.5)
    ax.axis("off")

    free  = sum(1 for s in slots if not s.occupied)
    taken = sum(1 for s in slots if s.occupied)
    ax.set_title(f"Parking Lot  |  🟢 Free: {free}   🔴 Occupied: {taken}",
                 color="#ccd6f6", fontsize=12, pad=8, fontweight="bold")

    return fig


# ────────────────────────────────────────────────
# PRIORITY BAR CHART (matplotlib)
# ────────────────────────────────────────────────
def draw_priority_chart():
    heap_vehicles = [v for _, v in st.session_state.heap]
    if not heap_vehicles:
        return None

    heap_vehicles.sort(key=lambda v: -v.priority)
    labels   = [f"{v.type_name[:3]}#{v.id}" for v in heap_vehicles[:10]]
    values   = [v.priority for v in heap_vehicles[:10]]
    colors   = {"Emergency": "#ff6b6b", "VIP": "#ffd93d", "Normal": "#4ade80"}
    bar_clrs = [colors[v.type_name] for v in heap_vehicles[:10]]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#1a1f35")

    bars = ax.barh(labels[::-1], values[::-1], color=bar_clrs[::-1],
                   edgecolor="#0f1117", linewidth=1.2, height=0.6)

    for bar, val in zip(bars, values[::-1]):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{val}", va="center", color="white", fontsize=9)

    ax.set_xlabel("Priority Score", color="#8892b0")
    ax.set_title("Top Vehicles in Priority Queue", color="#ccd6f6",
                 fontsize=11, fontweight="bold")
    ax.tick_params(colors="#8892b0")
    ax.spines[:].set_color("#2e3455")
    ax.set_facecolor("#1a1f35")
    for spine in ax.spines.values():
        spine.set_color("#2e3455")
    return fig


# ────────────────────────────────────────────────
# SESSION INIT
# ────────────────────────────────────────────────
if "slots" not in st.session_state:
    init_sim(num_vehicles=20)

# ────────────────────────────────────────────────
# SIDEBAR
# ────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    num_v = st.slider("Number of Vehicles", 5, 30, 20)

    if st.button("🔄 Reset Simulation"):
        init_sim(num_v)
        st.rerun()

    st.markdown("---")
    st.markdown("## 🧠 DAA Concepts")

    st.markdown('<div class="concept-box">📌 <b>Priority Queue (Max-Heap)</b><br>'
                'Emergency &gt; VIP &gt; Normal<br>'
                '<code>P = 5×type + 3×wait + 2×reservation</code></div>',
                unsafe_allow_html=True)

    st.markdown('<div class="concept-box">⚖️ <b>Fairness — Aging</b><br>'
                'Every step, waiting vehicles get +wait bonus so they are never starved.</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="concept-box">📐 <b>Efficiency — Greedy</b><br>'
                'Nearest free slot assigned using Euclidean distance from entrance.</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="concept-box">🔁 <b>Dynamic Update</b><br>'
                'Queue re-heapifies every step — order changes live.</div>',
                unsafe_allow_html=True)

    st.markdown("---")
    # Stats
    allocated = [l for l in st.session_state.logs if "Allocated" in l["Status"]]
    waiting   = len(st.session_state.heap)
    free_cnt  = sum(1 for s in st.session_state.slots if not s.occupied)

    st.markdown(f'<div class="metric-card"><h2>{len(allocated)}</h2>'
                f'<p>Vehicles Allocated</p></div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f'<div class="metric-card"><h2>{waiting}</h2>'
                f'<p>In Queue</p></div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f'<div class="metric-card"><h2>{free_cnt}</h2>'
                f'<p>Free Slots</p></div>', unsafe_allow_html=True)


# ────────────────────────────────────────────────
# MAIN HEADER
# ────────────────────────────────────────────────
st.markdown("# 🚗 Smart Parking Allocation System")
st.markdown("**Priority-Based · Greedy Nearest Slot · Fairness via Aging** — DAA Project Prototype")
st.markdown("---")

# Control buttons
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    if st.button("▶️ Run 1 Step", use_container_width=True,
                 disabled=st.session_state.done):
        run_step()
        st.rerun()
with c2:
    if st.button("⏩ Run All", use_container_width=True,
                 disabled=st.session_state.done):
        run_all()
        st.rerun()
with c3:
    total = len(st.session_state.slots)
    occ   = sum(1 for s in st.session_state.slots if s.occupied)
    pct   = int(occ / total * 100)
    st.markdown(f"**Lot utilization: {pct}%**")
    st.progress(pct / 100)

if st.session_state.done:
    st.success("✅ Simulation complete! All vehicles processed.")

st.markdown("---")

# ────────────────────────────────────────────────
# GRID + PRIORITY CHART
# ────────────────────────────────────────────────
col_grid, col_chart = st.columns([3, 2])

with col_grid:
    st.markdown("### 🗺️ Parking Lot — Live View")
    st.pyplot(draw_grid(), use_container_width=True)

with col_chart:
    st.markdown("### 📊 Priority Queue (Top 10)")
    fig2 = draw_priority_chart()
    if fig2:
        st.pyplot(fig2, use_container_width=True)
    else:
        st.info("Queue is empty.")

# ────────────────────────────────────────────────
# AGING EVENTS
# ────────────────────────────────────────────────
if st.session_state.get("aging_events"):
    with st.expander("⏱️ Aging Events This Step", expanded=False):
        for vid, label, old_p, new_p in st.session_state.aging_events:
            st.markdown(f"• **{label} #{vid}** priority boosted: `{old_p}` → `{new_p}` (+{new_p - old_p})")

# ────────────────────────────────────────────────
# ALLOCATION LOG
# ────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📋 Allocation Log")

if st.session_state.logs:
    df = pd.DataFrame(st.session_state.logs)

    def style_row(row):
        if "Emergency" in row["Type"]:
            return ["color: #ff6b6b; font-weight:bold"] * len(row)
        elif "VIP" in row["Type"]:
            return ["color: #ffd93d; font-weight:bold"] * len(row)
        else:
            return ["color: #4ade80"] * len(row)

    styled = df.style.apply(style_row, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=350)
else:
    st.info("No allocations yet. Click **▶️ Run 1 Step** or **⏩ Run All** to start!")

# ────────────────────────────────────────────────
# FORMULA EXPLAINER
# ────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 🔢 Priority Formula")
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    st.markdown("""
    **Formula:**
    ```
    P = 5×type + 3×waiting + 2×reservation
    ```
    """)
with col_f2:
    st.markdown("""
    **Type Scores:**
    | Type      | Score |
    |-----------|-------|
    | Emergency | 3     |
    | VIP       | 2     |
    | Normal    | 1     |
    """)
with col_f3:
    st.markdown("""
    **Example (Emergency, wait=2, reserved):**
    ```
    P = 5×3 + 3×2 + 2×1 = 15+6+2 = 23
    ```
    This vehicle goes to the **top of the heap!**
    """)
