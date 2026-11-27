# Cross-layer GDS data-path characterization

## Setup

Per-op correlation across cuFile, nvidia-fs and NVMe (`corr_id`) is used to measure how one
application read turns into device commands and bytes across the parameter space. gdsio serves as a
clean load generator (parameter sweeps rather than selected workloads). Tool: `tools/xlayer_amp.py`;
sweep: `tools/xlayer_sweep.sh`; data: `results/xlayer/amp_*.csv`.

## Result 1: command amplification (GDS path, aligned sequential, single PM983)

| cuFile size | A_cmd (nvme cmds/op) | A_byte | device max cmd |
|---|--:|--:|--:|
| 4 KiB | 1.00 | 1.000 | 4 KiB |
| 64 KiB | 1.00 | 1.000 | 64 KiB |
| 256 KiB | 1.00 | 1.000 | 256 KiB |
| 1 MiB | 1.00 | 1.000 | 1024 KiB |
| 4 MiB | 4.00 | 1.000 | 1280 KiB |

The command count follows `A_cmd(S) = ⌈S / cap⌉` with `cap = 1280 KiB`, and `A_byte = 1.000` (no
byte inflation on the aligned GDS path). About 4 device commands per run are unattributed
(background/metadata); this is negligible.

## Result 2: which stacked limit binds

There are two chunking limits above the device, plus the hardware cap:
- cuFile `max_direct_io_size_kb` (NVIDIA docs: GDS issues IO in chunks of this; ≤16 MiB)
- block-layer `max_sectors_kb` = 1280 (soft cap)
- hardware `max_hw_sectors_kb` = 2048 (NVMe MDTS proxy)

The per-op data shows that the block-layer soft cap (1280 KiB) binds, below both the cuFile chunk
size and the hardware MDTS (2048). Default large GDS reads are therefore split into ⌈S/1280⌉ commands
where the hardware would allow ⌈S/2048⌉. Setting `max_sectors_kb=2048` confirms this causally: 4 MiB
went from A_cmd 4 to 2 and 2 MiB from 2 to 1 (device command size 1280 to 2048 KiB), as predicted.
`tools/xlayer_sweep.sh ... TAG=x0_hw2048`.

### Scope

- This is not a discovery of the knob. `max_sectors_kb` is a documented general NVMe tuning
  parameter, and cuFile documents `max_direct_io_size_kb`.
- What the measurement adds: (1) per-op attribution of device-command count to the cuFile op that
  caused it (gds_stats sees the cuFile op and iostat sees device IOs in aggregate; neither ties them
  together, and no existing tool does); (2) using that attribution to measure which of the stacked
  limits actually binds (the block soft cap, below the cuFile chunk and the hardware MDTS) and to
  confirm it with the lever; (3) instrument validation: the tool recovers a known, spec-defined
  mechanism (⌈S/cap⌉), which is evidence that the per-op correlation is correct.
- Open question at this point: does over-splitting cost anything? A lower A_cmd matters only if it
  changes throughput, latency or CPU. On a single device at the bandwidth ceiling it may not, so this
  has to be measured (Result 3) before treating the knob as a useful lever.

## Result 3: over-splitting has no measurable cost on a single drive

Untraced gdsio throughput and per-op latency, GDS sequential, cold cache, N=5, `max_sectors_kb` 1280
vs 2048 (`tools/amp_perf.sh`, `results/xlayer/amp_perf.csv`). Pairwise (same size, same threads):

| size | A_cmd 1280→2048 | GiB/s @1280 | GiB/s @2048 | latency @1280 | @2048 |
|---|---|--:|--:|--:|--:|
| 4M, 1 thread | 4→2 | 2.443 | 2.388 | 1584 µs | 1622 µs |
| 8M, 1 thread | 7→4 | 2.557 | 2.489 | 3004 µs | 3091 µs |
| 16M,1 thread | 13→8 | 2.672 | 2.705 | 5660 µs | 5590 µs |
| 4M, 4 thread | 4→2 | 2.937 | 2.933 | 5211 µs | 5235 µs |
| 16M,4 thread | 13→8 | 2.924 | 2.932 | 19770 µs | 19705 µs |

Halving the device-command count changes throughput and latency by about zero (differences are
within noise; at 1 thread the more-split configuration is marginally faster). The single device is
bandwidth-bound and per-command overhead is fully hidden. `max_sectors_kb` is therefore not a GDS
speed lever on a single drive.

A secondary observation: single-thread throughput rises with op size (1M 1.90 to 16M 2.67 GiB/s),
while 4 threads sit at the ~2.93 ceiling for all sizes. Small single-thread ops are bound by per-op
overhead and latency, not by per-command cost (the cap makes no difference even below the ceiling).
The bottleneck is the cuFile op rate and queue depth, not command splitting.

Command amplification would matter in command-rate-bound regimes: striped/multi-device setups (where
the fabric is not the limit), completion-CPU-bound cases, or very high queue depth. A single A100
with one PM983 cannot exercise these; they are out of scope here and require other hardware.

## Result 4: alignment read amplification (A_byte)

Controlled cuFile (mis)alignment via `workloads/gds_align_probe.c` (full control of file_offset,
size and device-pointer offset), traced, with per-op `A_byte = device bytes / requested bytes`
(`tools/align_sweep.sh`, `results/xlayer/align.csv`). 2000 ops per case, single PM983.

| case | iosize | file-off mis | dev-ptr mis | A_byte | device reads |
|---|--:|--:|--:|--:|---|
| aligned | 64K | 0 | 0 | 1.000 | exact |
| offset +512 | 64K | 512 | 0 | 1.0625 | +1 block (68K for 64K) |
| offset +100 | 64K | 100 | 0 | 1.0625 | +1 block (any sub-4K offset) |
| sub-4K | 512 | 0 | 0 | 8.000 | full 4K block for a 512 B read |
| sub-4K + off | 512 | 512 | 0 | 8.000 | full 4K block |
| size 4095 | 4095 | 0 | 0 | 1.0002 | rounds to 4K |
| dev-ptr +512 | 64K | 0 | 512 | 1.000 | GPU-pointer misalignment costs nothing at the device |

The amplification follows `A_byte = ⌈(file_off mod 4K + size)/4K⌉·4K / size`. The device read
granularity is 4 KiB: sub-4K reads pay up to 8× (512 B to 4 KiB), and any sub-4K-misaligned file
offset adds exactly one block.

**Mechanism.** This is block-layer 4 KiB read rounding on the true P2P path, not cuFile's internal
bounce cache. For all misaligned cases the trace shows `nvfs_get_p2p_dma_mapping` firing per op and
the bounce indicator (`nvfs_mgroup_pin_shadow_pages`) at baseline noise, i.e. no host staging. Only
file-offset/size misalignment inflates device bytes (`A_byte>1`). GPU-pointer misalignment does not
(`A_byte=1.000`, P2P), necessarily so, since the GPU pointer only relocates where data lands in GPU
memory and cannot change which device blocks are fetched. No claim is made about cuFile's documented
internal-cache/staging path: it was not observed to trigger for 512 B misalignment on this
configuration, and device-read bytes are reported directly rather than reinterpreted against the
docs. NVIDIA's guides treat file_offset/size/devPtr alignment as equivalent triggers for staging;
that is a different axis from the device-read-byte amplification measured here.

**Cost.** Unlike A_cmd, A_byte is a direct device-bandwidth tax: at A_byte=8, 7/8 of consumed device
bandwidth moves bytes the application did not request. No separate performance run is needed, since
the amplification is the waste (in contrast to Result 3, where extra commands were free). How this
translates to end-application throughput depends on whether the regime is bandwidth- or IOPS-bound,
but the device-bandwidth waste is direct.

### Scope

Sub-block read amplification is a known storage phenomenon (true for any O_DIRECT reader, CPU
included) and is not claimed as a discovery. What this result adds: (1) per-op cross-layer
attribution of it on the GDS path. `gds_stats` reports the 512 B request and `posix=0` ("healthy");
`iostat` sees 4 KiB device reads in aggregate but cannot tie them to the op or know they are 8× the
request; only per-op cuFile-to-NVMe byte correlation identifies it. (2) It is predictable from access
geometry (closed form, no fitting; see Result 5). (3) It connects to real GDS workloads:
small-granularity access (WSI tiles ~6.7 KB, embedding/KV gathers) pays this silently. The file-side
vs GPU-pointer distinction is a clarification only.

### Relation to the kvikio bypass

Small-granularity access costs GDS in two distinct cross-layer ways that aggregate tools miss:
(1) below kvikio's 16 KB threshold, a silent POSIX bypass (the cuCIM naive path); (2) below the
4 KiB device granularity, read amplification (this result). Per-op attribution across cuFile,
nvidia-fs and NVMe surfaces both.

## Result 5: the amplification law on a real workload (CMU-1.svs WSI tiles)

The level-0 tile geometry was extracted from a real Aperio SVS (CMU-1.svs, 23,220 JPEG tiles, mean
6.77 KB, range 2.3–33.7 KB): 0.0% have a 4K-aligned file offset and 0.0% a 4K-multiple size (JPEG
tiles are packed back to back at arbitrary byte offsets). Those exact reads were replayed through GDS
(`workloads/gds_replay.c` reading `/tmp/tiles.csv`), traced, and per-op A_byte measured:

| | mean A_byte | median | bytes-weighted (device/requested) |
|---|--:|--:|--:|
| predicted from geometry (the law) | 2.326 | 1.745 | 1.605 |
| measured (traced through GDS) | 2.326 | 1.745 | 1.605 |

The match is exact, and it is not a fit. The predicted column is the closed-form law
`⌈(off mod 4K + size)/4K⌉·4K/size` evaluated on each tile's offset and size from the SVS directory;
it has zero free parameters and is computed before the run, not regressed against the measurement.
The four-significant-figure agreement therefore validates that the per-op attribution recovers the
true device behavior. A real digital-pathology slide read through GDS moves 1.6× the requested bytes
at the device (~38% of device read bandwidth spent on alignment padding), with 78.5% of tiles
amplified >1.5×. `gds_stats` reports the 149.8 MiB requested and `posix=0` ("healthy"); `iostat` sees
the inflated device bytes only in aggregate; only per-op cuFile-to-NVMe byte correlation attributes
the 1.6× to the tile reads and predicts it from geometry.

### Scope

The 4K read amplification is a property of small, unaligned reads on a 4K-block device. It is not
GDS-specific (a CPU O_DIRECT reader of the same tiles pays it too), and GDS is not claimed to cause
it. What this result adds: (1) per-op cross-layer attribution of the tax in a real GDS workload,
which no existing tool provides; (2) it is predictable from access geometry (a validated closed-form
law); (3) the fix is layout (4K-align/pad tiles, predicted A_byte of 1.0), not a GDS flag. The law is
general and appears in real data; the SVS is the test vehicle, not a constructed case.

### Fix options

| strategy | A_byte (device/useful) | |
|---|--:|---|
| naive per-tile, real offsets | 1.605 | measured ✓ |
| per-tile, 4K-aligned offsets | 1.246 | removes offset-spill; size-rounding floor remains |
| coalesced (contiguous span) | 1.000 | tiles are packed back-to-back ⇒ ~no waste |

Coalescing is the fix, and it is the same lever as in the cuCIM bypass finding (`read_region`
coalesces). Naive per-tile access pays both (a) the silent POSIX bypass below kvikio's 16 KB threshold
and (b) 1.6× device read amplification below the 4 KiB device granularity; coalescing eliminates
both. The per-op cross-layer tracer quantifies the device-layer 1.6× that `gds_stats`/`iostat` cannot
attribute, and predicts it from access geometry.

## Result 6: compat-fallback divergence is invisible at the device layer

Same aligned 64K reads on the same /mnt/nvme1 file, true GDS vs `CUFILE_FORCE_COMPAT_MODE=true`
(forced silent bounce; cuFileRead still fires). `/tmp/compat_div.sh`.

| | nvfs_io | A_byte | A_cmd | device cmd | attribution |
|---|--:|--:|--:|--:|--:|
| true GDS | 2000 | 1.000 | 1.00 | 64K | 2000 ✓ |
| forced compat bounce | 0 | 1.000 | 1.00 | 64K | 2000 ✓ |

The device stream is identical (same A_byte, A_cmd, command sizes and byte count), so `iostat`,
diskstats and any device-layer tool cannot distinguish true GDS from a silent bounce. The only
cross-layer difference is the nvidia-fs layer: present (nvfs_io=2000) for GDS, absent (0) for compat.
The per-op signature of a silent fallback is therefore "cuFileRead present, nvfs_io absent, nvme
present". Per-op attribution survives the bounce (corr_id propagates in-thread, so all 2000 ops are
attributed), and the tracer labels each op's path.

### Scope

For a read that enters cuFile and bounces, `gds_stats`/`cuFileGetStats` already exposes the fallback
through its aggregate `posix` counter, so the tracer is not the only way to know compat mode
happened. What it adds is (1) per-op granularity (which op, correlated with its device commands) and
(2) coverage of the case `gds_stats` cannot see: reads that bypass cuFile entirely (e.g. kvikio's
<16 KB threshold, which issues `pread64` without entering cuFile, as in the cuCIM case). The new
element here is the demonstration that silent fallback is invisible at the device layer and is
detectable only by the presence or absence of the middle (nvidia-fs) layer, which requires the
cross-layer view. This is a supporting result.

## Further directions

- GDS vs bounce device divergence: same application I/O on `-x0` (GDS) vs `-x2` (bounce). Does the
  page-cache/readahead path inflate device bytes (A_byte>1) or change the LBA/command stream relative
  to O_DIRECT GDS?
- Disentangle the two chunk limits: vary cuFile `max_direct_io_size_kb` in cufile.json and re-measure
  to confirm that the block layer binds independently.
- Amplification and performance: correlate A_cmd with throughput/latency to see whether the split
  costs anything.

Sources: NVIDIA GDS Configuration/Best-Practices/Troubleshooting guides
(docs.nvidia.com/gpudirect-storage/).
