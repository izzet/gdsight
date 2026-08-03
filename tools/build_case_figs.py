#!/usr/bin/env python3
"""Build the per-case evaluation figures as half-column square panels, plus the granularity figure.

Design contract (measured from IEEEtran[conference], not assumed):
  \\columnwidth = 252pt = 3.487in, \\textwidth = 516pt = 7.140in
  half-column subfigure  final 1.67in (0.48\\columnwidth)  -> raw 1.95in at the 0.86 scale rule
  full column            final 3.49in                      -> raw 4.06in

One 10pt serif scale throughout. Nothing is shrunk per-label to make a crowded panel fit: where a panel
was crowded the STRUCTURE changed instead, which is why every case panel uses horizontal bars. That puts
category labels on the y-axis where they have room, and keeps all six panels one visual family.

No panel titles. The LaTeX subcaptions carry the framing.

Error bars appear on rates only. Amplification and command counts are deterministic geometry -- re-measured
2026-07-29 they reproduce the analytic prediction exactly (4.000 / 2.000 / 1.008) -- so drawing error bars
on them would imply a variance that does not exist. Which panels carry them is itself informative.

Outputs per figure: .pdf (publication), .svg (editable), .png (review), and data/<name>.csv (auditable).
Usage: python3 tools/build_case_figs.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
XL = ROOT / "results" / "xlayer"
OUT = ROOT / "results" / "figures"
DATA = OUT / "data"

# Okabe-Ito colorblind-safe. Meaning never rests on colour alone: every bar is labelled on the y-axis and
# the value is printed at the bar end.
GRAY, BAD, GOOD, BASE = "#999999", "#d55e00", "#009e73", "#0072b2"

STYLE = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Nimbus Roman", "Liberation Serif", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 10, "axes.labelsize": 10, "axes.titlesize": 10,
    "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    "axes.spines.top": False, "axes.spines.right": False,
}
plt.rcParams.update(STYLE)

SQ = (1.95, 1.95)          # half-column square, raw
COL = (4.06, 2.55)         # full column, raw


# ------------------------------------------------------------------ data derivation
def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"missing input: {path}")
    with path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"empty input: {path}")
    return rows


def mean_sd(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    m = sum(vals) / n
    if n < 2:
        return m, 0.0
    return m, (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5


def derive() -> dict:
    """Every plotted value, with its provenance. Rates get (mean, sd); geometry gets a bare value."""
    d: dict = {}

    # kvikio: deterministic geometry. Source results/xlayer/ucb_kvikio_fix.txt, re-run 2026-07-29.
    d["kvikio"] = {"labels": ["POSIX\nbypass", "coalesced"], "vals": [2.67, 1.00],
                   "notes": ["2000 cmds", "94 cmds"], "src": "ucb_kvikio_fix.txt"}

    # HDF5 bandwidth: rate, n=5 at 512 MiB. Source hdf5_cpu_reps.csv.
    rows = read_csv(XL / "hdf5_cpu_reps.csv")
    by = {}
    for r in rows:
        by.setdefault(r["case"], []).append(float(r["BW_GiBps"]))
    order = [("cacheON", "chunk\ncache"), ("cacheOFF", "cache\noff"), ("contiguous", "contiguous")]
    d["hdf5"] = {"labels": [lab for _, lab in order],
                 "vals": [mean_sd(by[k])[0] for k, _ in order],
                 "err": [mean_sd(by[k])[1] for k, _ in order],
                 "n": len(by[order[0][0]]), "src": "hdf5_cpu_reps.csv"}

    # NIXL amplification: deterministic. Source nixl_diagnosis.csv, re-measured at SB=2048.
    rows = read_csv(XL / "nixl_diagnosis.csv")
    arm = {r["arm"]: r for r in rows}
    keys = ["BASELINE", "OBVIOUS:batch2x", "OURS:align4K", "OURS:coalesce"]
    d["nixl"] = {"labels": ["baseline", "2$\\times$ batch", "align 4 KiB", "coalesce"],
                 "vals": [float(arm[k]["A_byte"]) for k in keys], "src": "nixl_diagnosis.csv"}

    # Unaligned writes: deterministic, split by direction. Source results/xlayer/write-keystone.md.
    d["writes"] = {"labels": ["aligned", "16 KiB\nunaligned", "4 KiB\nunaligned"],
                   "wr": [1.00, 1.25, 2.00], "rd": [0.00, 0.42, 0.87], "src": "write-keystone.md"}

    # Tail p99: rate, n=3 per condition. Source tail_reps.csv.
    rows = read_csv(XL / "tail_reps.csv")
    by = {}
    for r in rows:
        if r["metric"] == "small_p99":
            by.setdefault(r["condition"], []).append(float(r["value"]))
    order = [("alone", "isolated"), ("large_1MiB_2", "+2$\\times$1 MiB"),
             ("large_4MiB_2", "+2$\\times$4 MiB"), ("large_4MiB_4", "+4$\\times$4 MiB")]
    p99 = [mean_sd(by[k]) for k, _ in order]
    base_m, base_s = p99[0]
    # Plot the RATIO to the isolated case: the claim is the inflation factor, so put it on the axis
    # directly rather than making a reader divide. Relative errors add in quadrature; the reference
    # condition is exactly 1 by construction and carries no error bar.
    ratio = [m / base_m for m, _ in p99]
    ratio_err = [0.0] + [r * ((s / m) ** 2 + (base_s / base_m) ** 2) ** 0.5
                         for r, (m, s) in zip(ratio[1:], p99[1:])]
    d["tail"] = {"labels": [lab for _, lab in order],
                 "p99": [m for m, _ in p99], "p99_err": [s for _, s in p99],
                 "vals": ratio, "err": ratio_err,
                 "n": len(by[order[0][0]]), "src": "tail_reps.csv"}

    # max_sectors_kb null: rate, existing reps. Source amp_perf.csv, 4 MiB single-thread rows.
    rows = [r for r in read_csv(XL / "amp_perf.csv") if r["size"] == "4M"]
    cap = {r["cap_kb"]: r for r in rows}
    d["cmdrate"] = {"labels": ["1280 KiB\n(default)", "2048 KiB\n(raised)"],
                    "vals": [float(cap["1280"]["GiBps_mean"]), float(cap["2048"]["GiBps_mean"])],
                    "err": [float(cap["1280"]["GiBps_sd"]), float(cap["2048"]["GiBps_sd"])],
                    "notes": [f"{cap['1280']['A_cmd']} cmds", f"{cap['2048']['A_cmd']} cmds"],
                    "src": "amp_perf.csv"}

    # Granularity: measured device bytes per aligned read. Source effective-granularity.txt.
    req = [512, 2048, 2560, 4096, 6144]
    d["granularity"] = {"req": req, "measured": [4096, 4096, 4096, 4096, 8192],
                        "sector": [((s + 511) // 512) * 512 for s in req],
                        "src": "effective-granularity.txt"}
    return d


def write_plot_data(d: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    def dump(name, header, rows):
        with (DATA / f"{name}.csv").open("w", newline="") as f:
            w = csv.writer(f); w.writerow(header); w.writerows(rows)

    k = d["kvikio"]
    dump("case_kvikio", ["condition", "metric", "unit", "value", "commands", "source"],
         [[l.replace("\n", " "), "device_byte_amplification", "x", v, n, k["src"]]
          for l, v, n in zip(k["labels"], k["vals"], k["notes"])])
    h = d["hdf5"]
    dump("case_hdf5", ["condition", "metric", "unit", "mean", "sd", "n", "source"],
         [[l.replace("\n", ""), "read_bandwidth", "GiBps", f"{v:.4f}", f"{e:.4f}", h["n"], h["src"]]
          for l, v, e in zip(h["labels"], h["vals"], h["err"])])
    n = d["nixl"]
    dump("case_nixl", ["condition", "metric", "unit", "value", "source"],
         [[l.replace("$\\times$", "x"), "device_byte_amplification", "x", f"{v:.3f}", n["src"]]
          for l, v in zip(n["labels"], n["vals"])])
    w = d["writes"]
    dump("case_writes", ["condition", "write_amplification", "read_amplification", "total", "unit", "source"],
         [[l.replace("\n", " "), a, b, round(a + b, 3), "x", w["src"]]
          for l, a, b in zip(w["labels"], w["wr"], w["rd"])])
    t = d["tail"]
    dump("case_tail",
         ["condition", "p99_mean_us", "p99_sd_us", "inflation_vs_isolated", "inflation_sd", "n", "source"],
         [[l.replace("$\\times$", "x"), f"{m:.1f}", f"{s:.1f}", f"{r:.2f}", f"{re_:.2f}", t["n"], t["src"]]
          for l, m, s, r, re_ in zip(t["labels"], t["p99"], t["p99_err"], t["vals"], t["err"])])
    c = d["cmdrate"]
    dump("case_cmdrate", ["condition", "metric", "unit", "mean", "sd", "commands", "source"],
         [[l.replace("\n", " "), "throughput", "GiBps", f"{v:.3f}", f"{e:.3f}", nn, c["src"]]
          for l, v, e, nn in zip(c["labels"], c["vals"], c["err"], c["notes"])])
    g = d["granularity"]
    dump("granularity", ["requested_B", "measured_device_B", "sector_prediction_B", "source"],
         [[a, b, s, g["src"]] for a, b, s in zip(g["req"], g["measured"], g["sector"])])


# ------------------------------------------------------------------ rendering
def hbar_axes(ax, labels):
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()                       # first condition at the top, reading order
    ax.grid(axis="x", linestyle=":", alpha=0.35)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=2.5, pad=2)


def panel_bars(d_key, d, xlabel, colors, *, err=False, baseline=None, fmt="{:.2f}",
               pad=1.06, xticks=None):
    s = d[d_key]
    fig, ax = plt.subplots(figsize=SQ, layout="constrained")
    y = range(len(s["labels"]))
    ax.barh(y, s["vals"], xerr=s.get("err") if err else None, color=colors,
            error_kw={"ecolor": "0.25", "elinewidth": 0.9, "capsize": 2})
    hbar_axes(ax, s["labels"])
    if baseline is not None:
        ax.axvline(baseline, ls="--", lw=0.8, color="k", alpha=0.5)
    ax.set_xlabel(xlabel)
    hi = max(v + (e if err else 0) for v, e in zip(s["vals"], s.get("err", [0] * len(s["vals"]))))
    ax.set_xlim(0, hi * pad * 1.18)
    if xticks is not None:
        ax.set_xticks(xticks)
    for i, v in enumerate(s["vals"]):
        e = s.get("err", [0] * len(s["vals"]))[i] if err else 0
        ax.text(v + e + hi * 0.03, i, fmt.format(v), va="center", ha="left")
    return fig


def make_figures(d: dict) -> dict:
    figs = {}

    figs["case_kvikio"] = panel_bars("kvikio", d, "Amplification ($\\times$)",
                                     [BAD, GOOD], baseline=1.0, fmt="{:.2f}$\\times$",
                                     xticks=[0, 1, 2, 3])
    figs["case_hdf5"] = panel_bars("hdf5", d, "Bandwidth (GiB/s)",
                                   [BASE, BAD, GOOD], err=True, fmt="{:.2f}",
                                   xticks=[0, 0.5, 1.0, 1.5])
    figs["case_nixl"] = panel_bars("nixl", d, "Amplification ($\\times$)",
                                   [BASE, BAD, GOOD, GOOD], baseline=1.0, fmt="{:.2f}$\\times$",
                                   xticks=[0, 2, 4])
    figs["case_cmdrate"] = panel_bars("cmdrate", d, "Throughput (GiB/s)",
                                      [BASE, GRAY], err=True, fmt="{:.2f}",
                                      xticks=[0, 1, 2])
    figs["case_tail"] = panel_bars("tail", d, "p99 Inflation ($\\times$)", [GOOD, BAD, BAD, BAD],
                                   err=True, baseline=1.0, fmt="{:.1f}$\\times$",
                                   xticks=[0, 10, 20])

    # writes: stacked horizontally so the READ component of a pure-write workload is visible. That
    # component is the whole point and it cannot be drawn without the block probe's direction field.
    s = d["writes"]
    fig, ax = plt.subplots(figsize=SQ, layout="constrained")
    y = range(len(s["labels"]))
    ax.barh(y, s["wr"], color=BASE, label="writes")
    ax.barh(y, s["rd"], left=s["wr"], color=BAD, hatch="///", edgecolor="white",
            linewidth=0.0, label="reads (RMW)")
    hbar_axes(ax, s["labels"])
    ax.axvline(1.0, ls="--", lw=0.8, color="k", alpha=0.5)
    ax.set_xlabel("Amplification ($\\times$)")
    tot = [a + b for a, b in zip(s["wr"], s["rd"])]
    ax.set_xlim(0, max(tot) * 1.30)
    for i, t in enumerate(tot):
        ax.text(t + max(tot) * 0.03, i, f"{t:.2f}$\\times$", va="center", ha="left")
    # No legend: at 1.95in a two-entry legend is wider than the free space and crowds the value labels.
    # The hatch distinguishes the read component without relying on colour, and the subcaption names it.
    figs["case_writes"] = fig

    # granularity: full column. The claim is that the 512 B sector mispredicts and the 4 KiB grid does not.
    s = d["granularity"]
    fig, ax = plt.subplots(figsize=COL, layout="constrained")
    ax.plot(s["req"], [v / 1024 for v in s["sector"]], "o--", color=GRAY,
            label="512 B sector prediction")
    ax.plot(s["req"], [v / 1024 for v in s["measured"]], "s-", color=BAD,
            label="measured (4 KiB grid)")
    ax.fill_between(s["req"], [v / 1024 for v in s["sector"]], [v / 1024 for v in s["measured"]],
                    color=BAD, alpha=0.10)
    ax.set_xlabel("Requested Read Size (B)")
    ax.set_ylabel("Device Bytes per Read (KiB)")
    # 2048 and 2560 sit close enough to collide at this width, so slant the labels rather than drop a
    # data point or shrink the font.
    ax.set_xticks(s["req"])
    ax.set_xticklabels([str(v) for v in s["req"]], rotation=30, ha="right")
    ax.grid(axis="y", linestyle=":", alpha=0.35)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=2.5, pad=2)
    ax.legend(frameon=False, loc="upper left")
    figs["granularity"] = fig
    return figs


def save_figure(fig, stem: Path) -> None:
    """Save at the EXACT figsize.

    Deliberately no bbox_inches="tight": it trims each panel by a different amount, so panels placed at
    the same LaTeX width end up scaled by different factors and their text comes out at different sizes.
    Constrained layout already reserves room for labels, so the fixed size is the honest one.
    """
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".pdf"))
    fig.savefig(stem.with_suffix(".svg"))
    fig.savefig(stem.with_suffix(".png"), dpi=250)
    plt.close(fig)


def main() -> None:
    d = derive()
    write_plot_data(d)
    for name, fig in make_figures(d).items():
        save_figure(fig, OUT / name)
        print("wrote", (OUT / name).relative_to(ROOT), "(.pdf .svg .png)")
    print("plot data ->", DATA.relative_to(ROOT))


if __name__ == "__main__":
    main()
