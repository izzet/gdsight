---
name: prepare-paper-figures
description: Create, revise, and validate research paper figures from analysis data, especially Matplotlib figures destined for LaTeX papers. Use when an agent must design a new scientific plot, update an existing figure, choose between PDF, SVG, and PNG, match a paper's visual conventions, export auditable plotting data, diagnose figure legibility or font problems, or perform camera-ready checks on vector figure artifacts.
---

# Prepare Paper Figures

Produce figures that are legible at their final paper size, faithful to the
underlying data, reproducible from a clean checkout, and easy to audit. Treat
the paper, its venue, and its established figure family as a visual system.

## Establish the Figure Contract

Inspect the repository before writing plotting code:

1. Read `CLAUDE.md`, `AGENTS.md`, project memory, and venue instructions when
   present.
2. Inspect the paper source for `\includegraphics`, `figure`, `figure*`,
   `subfigure`, and the actual width used for the target figure.
3. Inspect existing build scripts and rendered figures. Identify the canonical
   figure rather than averaging together historical inconsistencies.
4. Identify the claim, comparison, unit of analysis, filters, uncertainty, and
   intended caption. Do not design the chart until these are explicit.
5. Record the required raw figure width, final LaTeX width, panel count,
   output paths, and source data.

Apply this authority order when conventions conflict:

1. Venue and publisher requirements
2. Current paper source and explicit user direction
3. Project memory and the designated canonical figure
4. Existing scripts
5. General defaults in this skill

Flag a deliberate deviation. Do not silently copy style drift from an older
script.

## Choose Output Formats

Use PDF as the primary publication artifact for plots included in LaTeX.
Preserve vector lines, markers, and text, embed searchable font data, and
avoid Type 3 fonts. Use SVG as an optional companion when the figure must be
edited in a vector application or reused on the web. Use PNG only for review
previews or for genuinely raster data such as microscopy and satellite
imagery.

Export:

- `paper/figures/<name>.pdf`: publication artifact
- `paper/figures/<name>.svg`: optional editable companion
- `paper/figures/<name>.png`: review preview at 200 to 300 dpi
- `paper/figures/data/<name>.csv`: plot-ready, auditable data
- `scripts/build_fig_<name>.py`: reproducible build script

Prefer a hybrid PDF for dense plots: rasterize only the large point cloud or
image layer while keeping axes, labels, annotations, and legends as vectors.
Do not rasterize an entire plot merely to reduce file size.

Set PDF and SVG font behavior explicitly:

```python
plt.rcParams.update({
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
})
```

Use `svg.fonttype = "none"` for editable/selectable SVG text when the target
system has the chosen fonts. Use `"path"` instead when appearance portability
matters more than text editability.

## Match the AgentIOBench Figure System

Use `scripts/build_fig_motivation.py`, its rendered figures, and the project's
Claude `feedback_figure_style` memory when available as the canonical project
references. This skill contains the lasting conventions so it remains useful
when a collaborator does not have the same external Claude memory.

### Typography

Use one 10 pt serif scale throughout the raw artifact. Let LaTeX scale the
whole artifact uniformly. Never shrink individual labels or legends to make a
crowded design fit. Fix width, height, spacing, label wording, tick count, or
panel structure instead.

```python
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": [
        "Times New Roman",
        "Nimbus Roman",
        "Liberation Serif",
        "DejaVu Serif",
    ],
    "mathtext.fontset": "stix",
    "font.size": 10,
    "axes.labelsize": 10,
    "axes.titlesize": 10,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
})
```

Use Title Case for axis labels. Use full model names. Keep units explicit.
Avoid panel titles when the LaTeX caption or subcaption can carry the framing.
Keep annotations sparse and at the same 10 pt scale.

### Dimensions

Preserve the canonical LaTeX scale factor of approximately 0.86:

| Placement | Typical final width | Raw Matplotlib width |
|---|---:|---:|
| Half-column subfigure | 1.60 in | 1.85 in |
| Full column | 3.33 in | 3.85 in |
| Full-page `figure*` | 6.44 in | 7.50 in |

Compute a different raw width as `final_width / 0.86`. Choose height from the
data and layout. Check the actual paper class instead of assuming these final
widths for another venue.

Use LaTeX `subfigure` blocks for separately captioned panels. Save each
subfigure as its own PDF when independent subcaptions improve composition.

### Canonical model encoding

```python
MODEL_ORDER = [
    "sonnet_4_5", "haiku_4_5", "gpt_4_1", "gemini_2_5_flash",
    "gemma4_31b_it", "gemma4_26b_a4b_it", "qwen3_6_27b",
]
MODEL_LABELS = [
    "Sonnet 4.5", "Haiku 4.5", "GPT-4.1", "Gemini 2.5 Flash",
    "Gemma 4 31B", "Gemma 4 26B-A4B", "Qwen3.6-27B",
]
MODEL_COLORS = [
    "#d62728", "#ff7f0e", "#2ca02c", "#1f77b4",
    "#9467bd", "#8c564b", "#e377c2",
]
```

Keep this mapping stable across figures. Add shape, hatch, or line style when
color alone would make a comparison inaccessible. Verify grayscale
distinguishability when the venue may print in grayscale.

### Marks and guides

- Use a light dotted y-grid with `linestyle=":"` and `alpha=0.35`.
- Call `ax.set_axisbelow(True)`.
- Use `ax.tick_params(axis="both", length=2.5, pad=2)`.
- Use a thin dashed parity or baseline line.
- Place an inline reference label over a white, borderless bounding box when
  that is clearer than adding a legend entry.
- Use translucent violins, deterministic jittered dots, and a manually drawn
  dashed median segment for distributions.
- Use log scales for multiplicative ratios spanning orders of magnitude.
  Label decades explicitly as `0.1×`, `1×`, `10×`, and so on.
- Avoid decorative borders, gradients, three-dimensional effects, and dense
  legends.

## Make the Figure Reproducible

Separate data derivation from rendering even when both live in one script:

```python
def load_data(...): ...
def derive_plot_data(...): ...
def write_plot_data(...): ...
def make_figure(...): ...
def save_figure(...): ...

def main():
    source = load_data(...)
    plot_data = derive_plot_data(source)
    write_plot_data(plot_data)
    fig = make_figure(plot_data)
    save_figure(fig)

if __name__ == "__main__":
    main()
```

Use `matplotlib.use("Agg")` before importing `pyplot` in headless builds. Use
`pathlib.Path`, create output directories, close figures, and fail clearly on
missing or empty inputs.

Write the exact post-filter, post-transformation values used by the plot to a
tidy CSV. Include model, task, condition, metric, unit, and value columns as
applicable. Keep raw measurements or numerators and denominators when a ratio
is plotted. Make the CSV sufficient for an independent replot.

Use explicit seeds. Do not derive a seed from Python's built-in `hash()`
because hash randomization can change jitter between processes. Prefer a
fixed per-series seed map or a stable digest:

```python
import hashlib

seed = int.from_bytes(
    hashlib.sha256(model.encode("utf-8")).digest()[:4], "little"
)
rng = np.random.default_rng(seed)
```

Avoid machine-specific absolute paths in new scripts. Resolve paths from the
repository root or accept command-line arguments. Do not treat `/tmp` as a
durable data source.

## Use a Publication-Ready Matplotlib Skeleton

Adapt this skeleton rather than copying an entire historical figure:

```python
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

STYLE = {
    "font.family": "serif",
    "font.serif": [
        "Times New Roman", "Nimbus Roman",
        "Liberation Serif", "DejaVu Serif",
    ],
    "mathtext.fontset": "stix",
    "font.size": 10,
    "axes.labelsize": 10,
    "axes.titlesize": 10,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
}
plt.rcParams.update(STYLE)

def make_figure(data):
    fig, ax = plt.subplots(figsize=(3.85, 2.5), layout="constrained")
    # Draw data and reference marks here.
    ax.set_xlabel("Independent Variable (Unit)")
    ax.set_ylabel("Dependent Variable (Unit)")
    ax.grid(axis="y", linestyle=":", alpha=0.35)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=2.5, pad=2)
    return fig

def save_figure(fig, output_stem):
    output_stem = Path(output_stem)
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    common = {"bbox_inches": "tight", "pad_inches": 0.05}
    fig.savefig(output_stem.with_suffix(".pdf"), **common)
    fig.savefig(output_stem.with_suffix(".svg"), **common)
    fig.savefig(output_stem.with_suffix(".png"), dpi=250, **common)
    plt.close(fig)
```

Prefer `fig.savefig` over the stateful `plt.savefig`. Use either constrained
layout or tight layout deliberately, not both. Check that `bbox_inches="tight"`
does not change the artifact to an unexpected width that breaks the intended
LaTeX scaling.

## Validate Before Declaring Completion

Run the build script from the repository's documented environment. Then
perform all relevant checks.

### Artifact checks

```bash
pdfinfo paper/figures/<name>.pdf
pdffonts paper/figures/<name>.pdf
pdftoppm -png -r 150 -singlefile \
  paper/figures/<name>.pdf /tmp/<name>-review
```

Confirm:

- The PDF has one page and the intended physical dimensions.
- Every font has `emb=yes` and no Type 3 font remains. Accept an embedded CID
  OpenType font when Matplotlib uses the installed Nimbus Roman OTF file.
- Text, lines, and markers remain vector objects unless a dense layer was
  intentionally rasterized.
- No label, annotation, error bar, or legend is clipped.
- The PNG preview matches the PDF.

Inspect the rendering visually at its final on-paper size, not only while
zoomed in. Check crowded ticks, rotated labels, low-contrast colors,
overlapping points, misleading axes, excess precision, and whitespace.

### Data and claim checks

- Recompute at least one plotted value directly from its source.
- Confirm filters, missing-value handling, denominators, units, and category
  order.
- Confirm that uncertainty marks and sample sizes describe the correct unit
  of analysis.
- Confirm that every caption claim is visible in the plot or supported by the
  exported data.
- Avoid unsupported causal language and do not hide inconvenient observations
  through axis limits.
- Regenerate the CSV and visual twice to verify deterministic data and layout.

### Paper integration checks

Compile from the paper directory using the project convention:

```bash
latexmk -output-directory=out paper.tex
```

Inspect the compiled page. Confirm the final font size, caption relationship,
panel labels, float placement, and legibility in a two-column view. Do not
judge scale from the standalone PDF alone.

## Respect AgentIOBench-Specific Framing

For preliminary motivation figures:

- Omit sample-size labels.
- Avoid "passing runs" and "in our campaign" framing.
- Use the subcaptions for descriptive prose and keep the grand caption to one
  framing sentence.

For characterization figures, expose the evaluated population and statistical
unit in the caption or surrounding prose. Keep plot terminology consistent
with the paper.

## Report the Result

Return:

1. The figure claim and design choice
2. Paths to the build script, PDF, optional SVG, PNG, and plotting CSV
3. The input data and filters used
4. Validation performed, including font embedding and compiled-paper review
5. Any deliberate style deviation or unresolved limitation

Do not claim publication readiness until both the standalone artifact and the
compiled paper page have been inspected.
