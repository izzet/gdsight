# Worked examples: existing tools versus per-op cross-layer attribution

> **Note on values.** The walkthroughs remain valid, but several numbers below have since been
> re-measured. Current values: `corr_id` mis-billing 85-96% (not a single 94%), NIXL class B 4.00x at
> 2048 B (not 3.2x), tail inflation 22.35x with p99 214.3 -> 4790.7 us (not 19x, 216 -> 4080).
> Sources: `ucb_kvikio_fix.txt`, `nixl_crosscheck_reps.csv`, `tail_reps.csv`.

For each use case this note gives the symptom, the fix a practitioner would derive from each existing
tool (and why it is wrong, incomplete, or misdirected), what GDSight adds, and the resulting fix.
Existing tools report true but incomplete facts (an aggregate, or one layer); GDSight adds the per-op
cross-layer link that turns those facts into a specific, measured fix.

Numbers are measured on this node (A100 + NVMe, ext4 `data=ordered`). "Measured" means the fix was
run; "prescribed" means the fix follows directly from the attributed mechanism.

## Summary matrix

| | UC-A: KV pipeline bandwidth-starved | UC-B: GDS on, but no speedup | UC-C: p99 SLO breaches, p50 fine | UC-D: high NVMe command rate (inverse case) |
|---|---|---|---|---|
| `gds_stats`/cuFile | "all GDS, healthy" → do nothing | "200 reads, all GDS, healthy" → do nothing | "API latency normal" → do nothing | — |
| `iostat`/blktrace/device | "device busy" → add concurrency / faster drive | "1.05×, storage light" → look at GPU | "util moderate" → do nothing | "many cmds/read, high IOPS" → raise `max_sectors_kb` / merge I/O |
| spec sheet / `sysfs` / lore | "512-B blocks, 2560 B aligned, 1.0×" → do nothing | — | — | "bigger I/O is better" → raise cap to MDTS (2048) |
| timing tracer (`corr_id`) | 87% unattributed → no signal | 0% correct, 94% mis-billed to large class → optimize the large reads (wrong target) | mis-/un-attributes async → no signal | — |
| p50/mean dashboards | — | — | "p50 141 µs, healthy" → do nothing | — |
| GDSight | effective grid = 4096 B, class-B 3.2× | 91% of ops bypass GDS → POSIX, 2.67× | class-B p99 19× via HoL behind large reads | `A_cmd`=⌈S/1280⌉ but conserved-occupancy |
| Resulting fix | align/pad/coalesce to measured 4096 → 1.0×, 1.8× goodput (measured) | coalesce B above threshold → POSIX→GDS, 2.67→1.0×, 2000→94 cmds (measured) | segregate classes → p99 4080→216 µs (measured) | do not raise the cap; throughput/tail-neutral here (2.44→2.39 GiB/s; p99 4080→4058); only helps on cmd-rate-bound HW (measured) |

## UC-A: KV-offload pipeline is bandwidth-starved (cause: device geometry)

**Symptom.** A NIXL `cuda_gds` KV pipeline requests 4.88 MiB but the drive reads 15.6 MiB (31%
bandwidth efficiency); the GPU waits on storage.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| `gds_stats` | all reads on GDS, healthy | none; GDS is engaged | problem persists |
| `iostat` / nvidia-fs stats | device moving ~15 MiB, busy | add concurrency / faster drive | measured: 2× batch leaves 15.6 MiB unchanged, no goodput gain |
| device `sysfs` / NVMe spec | block size 512 B, so 2560 B is 5 aligned sectors | none; "already aligned, 1.0×" | wrong: real cost is 3.2× |
| Nsight / cuFile API | batch submits normal | none | problem persists |

**What GDSight adds.** Per-op requested versus device bytes on the true-P2P path. The effective read
granularity is 4096 B (measured: every aligned read moves 4096 B, not the documented 512), so the KV
class amplifies 3.2×, with offsets at `off mod 4K = 3072`. The closed form names the cause and the fix
granularity (4096, not the spec's 512).

**Fix (measured).** Align/pad/coalesce to the measured 4096 grid: device bytes go from 15.6 to 4.94 MiB
(`A_byte` 3.2 to 1.0×) with 1.8× goodput. The usual advice to align to the block size misfires because
the documented block size (512) is the wrong number; only measurement gives 4096.
(`diagnosis-walkthrough.md`)

## UC-B: GDS is enabled but the GPU is still not fed (cause: library policy, silent bypass)

**Symptom.** A kvikio retrieval/inference loader (default config) shows no GDS speedup; the GPU idles
waiting on input; loaders are busy.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| `gds_stats` | 200 `cuFileRead`, all GDS, 0 err, "healthy" | none / look at compute | misses it: blind to non-cuFile I/O |
| `iostat` | 1.05× device/app, low util | "storage fine, look at GPU" | dead end |
| `nvidia-smi` | GPU idle, waiting on input | more prefetch / loader threads | does not touch the bypass |
| timing tracer (`corr_id`) | 0% correct on small class; 94% mis-billed to the large class | "optimize the large reads" | wrong target; the large reads are already optimal |

**What GDSight adds.** Per-op LBA shows that 2000 of 2200 ops (91%) never entered GDS: they fell below
kvikio's 16 KiB threshold and ran as POSIX `pread` (CPU-staged, no P2P), and those reads amplify 2.67×
at the device. The aggregate (1.05×) and the API view (healthy) both hide this; timing points at the
wrong class (0% correct, 95% mis-billed to class A).

**Fix (measured).** Coalesce the small embedding/KV reads into at-or-above-threshold, 4K-aligned
chunks. Real kvikio, same class-A workload, only class B coalesced (`tools/run_ucb_kvikio_fix.sh`):

| class-B metric | baseline (default) | fixed (coalesce 64 KiB) |
|---|--:|--:|
| path | 2000 reads → POSIX (silent bypass) | 94 reads → GDS P2P |
| device amplification (LBA) | 2.67× (15.6 MiB) | 1.00× (5.9 MiB) |
| device commands | 2000 | 94 (21× fewer) |
| corr_id correctness | 0% (95% mis-billed) | 100% |

The fix moves class B onto the fast path and eliminates the amplification. `gds_stats`, which was blind
to the problem, now sees class B (294 cuFileRead vs 200), because the fix put it on GDS. Wall-clock
improvement is ~1.2× here (B is a small byte share; the gain is larger under a heavier small-read mix
or bandwidth contention).

## UC-C: p99 SLO breaches while the median is fine (cause: cross-op device interference)

**Symptom.** A latency-sensitive small-read path (3 KiB scattered) intermittently violates its p99 SLO,
while p50 and device utilization look normal.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| p50/mean dashboards | p50 = 141 µs, healthy | none | misses it: tail-only |
| `iostat` | device util moderate, no saturation | none | dead end |
| `gds_stats` | aggregate API latency normal | none | dead end |

**What GDSight adds.** Per-op tail attribution: the small class's p99 inflates 19× (216 to 4080 µs,
p50 untouched), and the cause is head-of-line blocking. Slow small ops are exactly those whose
`[ts,ts+dur]` window overlaps a large-region NVMe command (fast ops: 0 overlapping large cmds; slow ops:
queued behind a large read). The cost is attributed per op to the specific large reads it queued
behind.

**Fix (measured).** Segregate the classes (separate cuFile streams or size-based QoS so small reads do
not share the queue with large ones). Small-op p99 returns to the isolated 216 µs (a 19× recovery; the
"alone" column). (`interference.md`)

## UC-D (inverse case): iostat shows a high NVMe command rate, and the fix is not worth applying

**Symptom.** Large GDS reads each fan out into several NVMe commands (a 4 MiB read issues 4 device
commands); `iostat`/`blktrace` show a high command/IOPS rate. The natural storage-tuning move is to
cut the command count.

| Tool | What it shows (true) | Fix it leads to | Outcome |
|---|---|---|---|
| `iostat` / `blktrace` | many commands per read, high IOPS | raise `max_sectors_kb` / merge I/O | appears clearly beneficial |
| NVMe tuning lore / docs | soft cap 1280 KiB < hardware MDTS 2048 | raise the cap to 2048 | "bigger I/O, fewer cmds, faster" |

**What GDSight adds.** Per op, `A_cmd = ⌈S / 1280 KiB⌉`: the block-layer soft cap (1280) binds, below
the hardware MDTS (2048), and `A_byte = 1.000` (command splitting moves no extra bytes). Raising the cap
from 1280 to 2048 halves the command count (4 MiB: 4 to 2 cmds), but measured it is throughput- and
tail-neutral: 2.443 to 2.388 GiB/s, p99 4080 to 4058 µs. Command chunking is conserved-occupancy: the
same bytes hold the device for the same time, and slow ops overlap fewer but larger commands (7.96 to
4.96) for the same tail. The device is bandwidth-bound; per-command overhead is fully hidden.

**Recommendation.** Do not raise the cap; the obvious fix has no effect on this single drive. The same
per-op occupancy view indicates where it would matter (command-rate-bound multi-device or PCIe-fabric
hardware), so the effort is spent only where it pays. (`findings.md` R1/R3, `interference.md`.)

## Summary

In every case the existing tools report a correct aggregate or single-layer fact. That fact supports
only generic moves (add hardware, add concurrency, look elsewhere) or, for a command-rate symptom, a
plausible but ineffective one. On this system, per-op cross-layer attribution turns those observations
into the right decision: a specific, measured fix when one exists (UC-A/B/C, including the parameter
itself, 4096 rather than the documented 512), and a clear "not worth doing" when it does not (UC-D).
The result is correct prioritization in both directions, fixing real problems and ruling out false
ones, which no single-layer or aggregate view provides.
