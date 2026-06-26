#!/usr/bin/env python3
"""Generate paper figures from committed results. Outputs PDF (paper) + PNG (viewing) in results/figures/.
  Fig 1 (fig1_diagnosis): Table-2 differential diagnosis — obvious fix vs attributed fix, 3 use cases.
  Fig 2 (fig2_granularity): the documented 512-B block vs the measured 4096-B effective grid.
Numbers: UC-A from results/xlayer/nixl_diagnosis.csv; UC-B from ucb_kvikio_fix.txt; UC-C from
interference.md; granularity from effective-granularity.txt (all committed)."""
import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "results", "figures"); os.makedirs(OUT, exist_ok=True)
GRAY, BAD, GOOD, BASE = "#9e9e9e", "#d1495b", "#2e8540", "#5b6b8c"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})

def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"), bbox_inches="tight", dpi=200)
    print("wrote", os.path.join(OUT, name) + ".{pdf,png}")

# ---- Fig 1: differential diagnosis (obvious fix vs attributed fix) ----
# UC-A from CSV
da = {r["arm"]: r for r in csv.DictReader(open(os.path.join(HERE,"results/xlayer/nixl_diagnosis.csv")))}
ucaA = [float(da[k]["A_byte"]) for k in ("BASELINE","OBVIOUS:batch2x","OURS:align4K","OURS:coalesce")]
ucaG = [float(da[k]["goodput_MiBps"]) for k in ("BASELINE","OBVIOUS:batch2x","OURS:align4K","OURS:coalesce")]

fig, ax = plt.subplots(1, 3, figsize=(9.2, 2.9))

# (a) UC-A
labs = ["baseline", "obvious:\n2× batch", "ours:\nalign", "ours:\ncoalesce"]
cols = [BASE, BAD, GOOD, GOOD]
b = ax[0].bar(labs, ucaA, color=cols)
ax[0].axhline(1.0, ls="--", lw=0.8, color="k", alpha=.5)
ax[0].set_ylabel("device read amplification (A_byte)")
ax[0].set_title("(a) UC-A  NIXL KV — device geometry", fontsize=9)
for i, (r, g) in enumerate(zip(b, ucaG)):
    mark = {1: " ✗", 2: " ✓", 3: " ✓"}.get(i, "")
    mc = BAD if i == 1 else (GOOD if i in (2, 3) else "k")
    ax[0].text(r.get_x()+r.get_width()/2, r.get_height()+0.05, f"{g:.0f} MiB/s{mark}",
               ha="center", va="bottom", fontsize=6.5, color=mc)
ax[0].set_ylim(0, 4.2)

# (b) UC-B
b2 = ax[1].bar(["baseline\n(POSIX bypass)", "ours: coalesce\n(GDS P2P)"], [2.67, 1.00], color=[BAD, GOOD])
ax[1].axhline(1.0, ls="--", lw=0.8, color="k", alpha=.5)
ax[1].set_ylabel("class-B amplification (A_byte)")
ax[1].set_title("(b) UC-B  kvikio — silent bypass", fontsize=9)
for r, mb, pp in zip(b2, [15.6, 5.9], ["2000 cmds\nPOSIX", "94 cmds\nGDS"]):
    ax[1].text(r.get_x()+r.get_width()/2, r.get_height()+0.04, f"{mb} MiB\n{pp}", ha="center", va="bottom", fontsize=6.5)
ax[1].set_ylim(0, 3.2)

# (c) UC-C
b3 = ax[2].bar(["ours: segregate\n(fix)", "+1 MiB\ninterferer", "+4 MiB\ninterferer"], [216, 1182, 4080],
               color=[GOOD, BAD, BAD])
ax[2].set_ylabel("small-op p99 latency (µs)")
ax[2].set_title("(c) UC-C  tail — head-of-line", fontsize=9)
ax[2].text(2, 4080+90, "19×", color=BAD, fontsize=9, ha="center", fontweight="bold")
ax[2].axhline(141, ls=":", lw=0.9, color="k", alpha=.6)
ax[2].text(0.05, 280, "p50 ≈141 µs (flat)", fontsize=6.5, color="k")
ax[2].set_ylim(0, 4600)
fig.tight_layout()
save(fig, "fig1_diagnosis")

# ---- Fig 2: documented 512-B block vs measured 4096-B effective grid ----
req = [512, 2048, 2560, 4096, 6144]
measured = [4096, 4096, 4096, 4096, 8192]                 # GDS-Trace, effective-granularity.txt
spec512  = [((s+511)//512)*512 for s in req]             # if you trust the documented 512-B block
fig2, axg = plt.subplots(figsize=(4.3, 3.0))
axg.plot(req, [m/1024 for m in spec512], "o--", color=GRAY, label="documented 512-B block\n(predicts 1.0×, 'no problem')")
axg.plot(req, [m/1024 for m in measured], "s-", color=BAD, label="measured (GDS-Trace)\n→ 4096-B effective grid")
axg.fill_between(req, [m/1024 for m in spec512], [m/1024 for m in measured], color=BAD, alpha=.10)
for s, m in zip(req, measured):
    axg.annotate(f"{m//1024}K", (s, m/1024), textcoords="offset points", xytext=(4,4), fontsize=6.5, color=BAD)
axg.set_xlabel("requested read size (B)"); axg.set_ylabel("device bytes moved per read (KiB)")
axg.set_title("Effective granularity is measured, not documented", fontsize=9)
axg.legend(fontsize=6.8, loc="upper left", frameon=False)
axg.set_xticks(req); axg.set_xticklabels([str(s) for s in req], fontsize=7)
fig2.tight_layout()
save(fig2, "fig2_granularity")
