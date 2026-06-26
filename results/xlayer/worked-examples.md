# Worked examples: same problem, what each tool tells you to do, what GDS-Trace adds

For each use case: the **symptom**, the **fix a practitioner derives from each existing tool** (and why it
is wrong, incomplete, or mis-directed), then **what GDS-Trace adds on top** and **the solution that
unlocks**. The pattern is consistent — current tools report *true but incomplete* facts (an aggregate, or
one layer); GDS-Trace adds the missing **per-op cross-layer link** that turns those facts into a
specific, correct, *measured* fix. Numbers are measured on this node (A100 + NVMe, ext4 `data=ordered`);
"measured" = we ran the fix, "prescribed" = the fix follows directly from the attributed mechanism.

## Summary matrix

| | **UC-A: KV pipeline bandwidth-starved** | **UC-B: GDS on, but no speedup** | **UC-C: p99 SLO breaches, p50 fine** |
|---|---|---|---|
| `gds_stats`/cuFile | "all GDS, healthy" → *do nothing* | "200 reads, all GDS, healthy" → *do nothing* | "API latency normal" → *do nothing* |
| `iostat`/device agg | "device busy" → *add concurrency / faster drive* | "1.05×, storage light" → *look at GPU* | "util moderate" → *do nothing* |
| spec sheet / `sysfs` | "512-B blocks, 2560 B aligned, 1.0×" → *do nothing* | — | — |
| timing tracer (`corr_id`) | 87% unattributed → *no signal* | **0% correct, 94% mis-billed to large class** → *optimize the large reads (WRONG)* | mis/!attributes async → *no signal* |
| p50/mean dashboards | — | — | "p50 141 µs, healthy" → *do nothing* |
| **GDS-Trace (ours)** | effective grid = **4096 B**, class-B **3.2×** | **91% of ops bypass GDS → POSIX**, 2.67× | class-B **p99 19×** via HoL behind large reads |
| **→ solution** | align/pad/coalesce to measured 4096 → **1.0×, 1.8× goodput** (measured) | coalesce small reads above threshold → P2P + 1.0× (prescribed) | segregate classes → **p99 4080→216 µs** (measured) |

---

## UC-A — "Our KV-offload pipeline is bandwidth-starved" (cause: device geometry)
**Symptom.** A NIXL `cuda_gds` KV pipeline requests 4.88 MiB but the drive reads 15.6 MiB (31% bandwidth
efficiency); the GPU waits on storage.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| `gds_stats` | all reads on GDS, healthy | none — GDS is engaged | problem persists |
| `iostat` / nvidia-fs stats | device moving ~15 MiB, busy | **add concurrency / faster drive** | **measured: 2× batch → 15.6 MiB unchanged, no goodput gain** |
| device `sysfs` / NVMe spec | block size **512 B** → 2560 B is 5 aligned sectors | none — "already aligned, 1.0×" | wrong: real cost is 3.2× |
| Nsight / cuFile API | batch submits normal | none | problem persists |

**What GDS-Trace adds.** Per-op, requested-vs-device bytes on the true-P2P path: the **effective** read
granularity is **4096 B** (measured — every aligned read moves 4096 B, not the documented 512), so the KV
class amplifies **3.2×**, with offsets at `off mod 4K = 3072`. The closed form names the cause and the
fix granularity (4096, not the spec's 512).
**Solution (measured).** Align/pad/coalesce to the *measured* 4096-grid → device bytes 15.6→4.94 MiB
(`A_byte` 3.2→1.0×) and **1.8× goodput**. The textbook "align to the block size" mis-fires because the
documented block size (512) is the wrong number; only measurement gives 4096. (`diagnosis-walkthrough.md`)

---

## UC-B — "GDS is enabled but the GPU still isn't fed" (cause: library policy / silent bypass)
**Symptom.** A kvikio retrieval/inference loader (default config) shows no GDS speedup; GPU idle waiting
on input; loaders busy.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| `gds_stats` | 200 `cuFileRead`, all GDS, 0 err — "healthy" | none / look at compute | misses it: blind to non-cuFile I/O |
| `iostat` | 1.05× device/app, low util | "storage fine → look at GPU" | dead end |
| `nvidia-smi` | GPU idle, waiting on input | more prefetch / loader threads | doesn't touch the bypass |
| timing tracer (`corr_id`) | **0% correct on small class; 94% MIS-billed to the large class** | **"optimize the large reads"** | **WRONG target** — large reads are already optimal |

**What GDS-Trace adds.** Per-op LBA shows **2000 of 2200 ops (91%) never entered GDS** — they fell below
kvikio's 16 KiB threshold and ran as POSIX `pread` (CPU-staged, no P2P) — *and* those reads amplify
**2.67×** at the device. The aggregate (1.05×) and the API view (healthy) both hide this; timing actively
mis-points you at the wrong class.
**Solution (prescribed; mechanism measured).** Coalesce the small embedding/KV reads into ≥-threshold,
aligned chunks so they take the P2P path *and* stop amplifying (the same coalescing lever measured in
UC-A: → GDS, `A_byte`→1.0×). Kvikio-specific before/after validation is a quick follow-up.

---

## UC-C — "p99 SLO breaches, but the median is fine" (cause: cross-op device interference)
**Symptom.** A latency-sensitive small-read path (3 KiB scattered) intermittently violates its p99 SLO,
while p50 and device utilization look normal.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| p50/mean dashboards | p50 = 141 µs, healthy | none | misses it: tail-only |
| `iostat` | device util moderate, no saturation | none | dead end |
| `gds_stats` | aggregate API latency normal | none | dead end |

**What GDS-Trace adds.** Per-op tail attribution: the small class's **p99 inflates 19×** (216→4080 µs,
p50 untouched) and the cause is **head-of-line blocking** — slow small ops are precisely those whose
`[ts,ts+dur]` window overlaps a large-region NVMe command (FAST ops: 0 overlapping large cmds; SLOW ops:
stuck behind a large read). The cost is attributed per op to the specific large reads it queued behind.
**Solution (measured).** Segregate the classes (separate cuFile streams / size-based QoS so small reads
don't share the queue with large ones) → small-op p99 returns to the isolated **216 µs** (a 19×
recovery; the "alone" column). (`interference.md`)

---

## The throughline
In every case the existing tools are *not lying* — they report a correct aggregate or single-layer fact.
But that fact supports only generic moves (add hardware, add concurrency, "look elsewhere") or, for
timing correlation, an actively wrong one. GDS-Trace's per-op cross-layer attribution is the piece that,
**on this system**, converts those true-but-insufficient observations into the one correct, specific,
measured fix — and, as UC-A shows, even the corrective *parameter* (4096, not the documented 512) is a
measured quantity no single layer reports.
