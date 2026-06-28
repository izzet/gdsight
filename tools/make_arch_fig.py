#!/usr/bin/env python3
"""GDSight architecture diagram -> results/figures/fig_arch.{pdf,png}.
Layered GDS data path (one read) + 3 probe taps + corr_id eBPF maps + offline two-basis analyzer."""
import os, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "results", "figures"); os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.size": 9})
USER, KERN, PROBE, MAPS, GPU, ANALY = "#dfe7f3", "#f3e7df", "#fde7ea", "#e7f3ea", "#efe7f7", "#fff6d6"
EDGE = "#444"

fig, ax = plt.subplots(figsize=(9.6, 6.6)); ax.set_xlim(0, 12); ax.set_ylim(0, 11); ax.axis("off")

def box(x, y, w, h, text, fc, fs=8.5, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=fc, ec=EDGE, lw=1.1))
    ax.text(x+w/2, y+h/2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal")

def arrow(x1, y1, x2, y2, style="-|>", color=EDGE, lw=1.4, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=13,
                                 color=color, lw=lw, ls=ls, shrinkA=2, shrinkB=2))

# ---- left: GDS data path (one read), top->down ----
ax.text(2.3, 10.6, "GDS data path (one read)", ha="center", fontsize=9.5, fontweight="bold")
box(0.5, 9.5, 3.6, 0.7, "App:  cuFileRead(gpu_buf, size, offset)", USER, 8)
box(0.5, 8.3, 3.6, 0.7, "libcufile.so   (user)", USER, 9, True)
ax.plot([0.4, 4.2], [8.05, 8.05], ls=(0,(4,3)), color="#888", lw=1)
ax.text(0.45, 8.12, "user", fontsize=6.5, color="#888"); ax.text(3.7, 7.86, "kernel", fontsize=6.5, color="#888")
box(0.5, 7.1, 3.6, 0.7, "nvidia-fs.ko   (kernel)", KERN, 9, True)
box(0.5, 5.9, 3.6, 0.7, "block / NVMe layer", KERN, 9, True)
box(0.5, 4.7, 3.6, 0.7, "NVMe SSD", KERN, 9, True)
for y in (9.5, 8.3, 7.1, 5.9):  # data flow down the stack
    arrow(2.3, y, 2.3, y-0.5)
# P2P DMA to GPU
box(9.4, 4.7, 2.2, 0.7, "GPU device memory\n(BAR1 on our stack)", GPU, 8.0, True)
arrow(4.1, 5.05, 9.4, 5.05, style="-|>", color="#7a4fb0", lw=2.6)
ax.text(6.9, 5.25, "P2P DMA (no CPU bounce)", ha="center", fontsize=7.5, color="#7a4fb0")

# ---- middle: probe taps ----
def tap(y, label, cap):
    box(4.6, y, 2.7, 0.66, label, PROBE, 7.6, True)
    ax.text(5.95, y-0.16, cap, ha="center", va="top", fontsize=6.3, color="#7a2230")
    arrow(4.1, y+0.33, 4.6, y+0.33, color="#b04a5a")          # layer -> probe
    arrow(7.3, y+0.33, 8.5, y+0.33, color="#b04a5a", ls=(0,(3,2)))  # probe -> maps
tap(8.33, "uprobe  cuFile*", "offset, size · corr_id=ktime · begin()")
tap(7.13, "kprobe  nvfs_io / p2p / shadow", "P2P vs host-bounce · corr=current()")
tap(5.93, "kprobe  nvme_setup_cmd", "sector, size · corr=current()")

# ---- right: corr_id eBPF maps ----
box(8.5, 6.7, 3.1, 2.5, "", MAPS)
ax.text(10.05, 8.95, "corr_id maps (eBPF)", ha="center", fontsize=8.2, fontweight="bold")
ax.text(10.05, 8.5, "cufile_active_op[tid]→id", ha="center", fontsize=7)
ax.text(10.05, 8.15, "(same thread = sync, exact)", ha="center", fontsize=6.2, color="#555")
ax.text(10.05, 7.7, "cufile_op_count[tgid]", ha="center", fontsize=7)
ax.text(10.05, 7.4, "cufile_proc_op[tgid]", ha="center", fontsize=7)
ax.text(10.05, 7.05, "(worker-thread fallback, n==1)", ha="center", fontsize=6.2, color="#555")
ax.text(8.55, 6.78, "write ◀ uprobe   ·   read ◀ kprobes", fontsize=6.0, color="#777")

# ---- bottom: trace + offline analyzer ----
arrow(2.3, 4.7, 2.3, 3.95, lw=1.6)
box(0.5, 3.25, 3.6, 0.62, "DFTracer  →  trace.pfw.gz", "#eeeeee", 8.3, True)
ax.text(4.25, 3.56, "events (ring buffer)", fontsize=6.5, color="#777")
arrow(2.3, 3.25, 2.3, 2.7, lw=1.6)
box(0.4, 0.5, 11.2, 2.1, "", ANALY)
ax.text(6.0, 2.35, "DFAnalyzer (offline)  —  per-op cross-layer attribution", ha="center", fontsize=9, fontweight="bold")
box(0.9, 1.25, 5.0, 0.85, "TIME basis: group device events by corr_id\n→ SYNC: exact (100%, incl. 16-thread)", "#e7f0ff", 7.8)
box(6.3, 1.25, 5.0, 0.85, "ADDRESS basis: nvme.sector —FIEMAP→ op\n(expanded coverage) → ASYNC: robust (overlap→cmd-to-set)", "#ffeef0", 7.8)
ax.text(6.0, 0.78, "complementary, disjoint failure modes → cover every regime but async overlapping-coverage (→ command-to-set)",
        ha="center", fontsize=7.2, style="italic", color="#444")

ax.text(0.1, 0.12, "plugins only · statically linked into one datacrumbs.bpf.o · no cuFile/nvidia-fs/kernel changes · "
        "pid-filter or trace-all (unmodified apps)", fontsize=6.6, color="#666")
for ext in ("pdf", "png"):
    fig.savefig(os.path.join(OUT, f"fig_arch.{ext}"), bbox_inches="tight", dpi=200)
print("wrote", os.path.join(OUT, "fig_arch.{pdf,png}"))
