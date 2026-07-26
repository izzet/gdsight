# Hunting a write case: criteria, negatives, remaining leads

Record of the search for a write-path case strong enough to sit beside the read keystones. Written so a
later session does not repeat the dead ends. Companion to `write-keystone.md` (what was found) and
`observer-visibility-audit.md` (what the observers actually report).

## The bar a candidate has to clear

1. **Different engine** from the existing keystones. kvikio already carries a read keystone, so a second
   kvikio case of the same class is redundant, and HDF5 is likewise already used.
2. **Different finding class.** Not library-threshold routing, not middleware host-staging, not
   head-of-line blocking, all of which the paper already demonstrates.
3. **Survives "strip out the GPU".** If the identical phenomenon occurs for a CPU writer, it is a
   storage finding wearing GDS clothes. In practice this means it must turn on **P2P versus host
   staging**, which has no CPU analogue.

Test 3 is the one candidates keep failing.

## Ruled out (measured, do not retest)

| candidate | result | why it fails |
|---|---|---|
| **cuFile buffer registration** (`gdsio -b` skips `cuFileBufRegister`) | registered and unregistered both give **256 nvfs writes, 0.934 vs 0.925 GiB/s** | cuFile handles unregistered device buffers transparently on this stack. No silent loss of P2P, so there is no pathology at all. |
| **Bounce-buffer pool contention** (do compat writers stall P2P writers through the shared cuFile POSIX pool?) | aligned P2P writer retains **99.7%** of solo throughput (1.006 -> 1.004); the compat writer collapses to **7.4%** (13.5x) | The asymmetry is total and in the wrong direction. If the two paths contended on a shared pool the P2P side would suffer too. This is a latency-bound small-op writer queuing behind large device commands, i.e. **head-of-line blocking**, which Sec V-D already reports for reads (p99 19x). Fails test 2. |
| **Unaligned-write RMW** (`gdsio -U`) | real, 2.87x device amplification, 6.1-7.8x throughput cost | Documented verbatim by NVIDIA, *and* `gds_stats -l 3` reports it (`posix=`, `unalign=`). Fails test 3 partly and is not a discovery. Kept as supporting characterisation. |
| **kvikio sub-threshold writes** | strong: `gds_stats` clean (`posix=0 unalign=0 err=0`) while 60% of ops bypass GDS and drive 89.7 MiB of device reads, 100% address-attributed | Fails test 1 (second kvikio case) and largely test 3 (the mechanism is library policy plus filesystem RMW; remove the GPU and it reproduces). |
| **Page-cache coherence** (GDS write to a file with dirty page-cache pages) | real and GDS-intrinsic: **1.8x** throughput loss (0.949 -> 0.520 at 1 MiB, 0.684 -> 0.372 at 64 KiB), `pg-cache` counter fires | Passes test 3 and test 2. But NVIDIA documents it: applications should avoid mixing O_DIRECT and normal I/O to the same file, and "overall I/O throughput might be slower". Best remaining *attribution* candidate, not a discovery. |
| **P2P write concurrency ceiling** (lead 3) | P2P writes pin at **1.00 GiB/s from 2 to 32 threads**, spread <0.5% over 12 runs, while latency scales exactly linearly (1.04 ms at 1 thread -> 31.3 ms at 32). Host staging (`-x 2`) is noisy (0.95-1.23 at 8 threads) and averages the same or slightly higher. | The apparent 24% gap to `gdsio`'s CPU_ONLY path (1.33 GiB/s) is a **benchmark artifact, not a GDS effect**: at ~91% device utilisation in all three paths, CPU_ONLY issues 509 KiB commands at queue depth 12.9 while P2P issues 814 KiB at depth 7.8 (= exactly 1 in flight per thread, which is what synchronous `cuFileWrite` should give). Different command-size distributions from `gdsio`'s own code paths, same saturated drive. No pathology. |
| **`max_io_queue_depth` cliff** (lead 4) | qd=32 and qd=128 are indistinguishable (0.9946 vs 0.9892 GiB/s); qd>256 is rejected loudly at init (`invalid value ... min: 1 max: 256`, `cuFile driver open error: -22`) | The knob does not set the plateau, and out-of-range values fail closed with an accurate message rather than degrading silently. Nothing to attribute. |

## The pattern this search established

Every mechanism reached so far is documented: unaligned-write RMW via POSIX, the 4 KiB alignment
fallback, mixed O_DIRECT/buffered coherence, kvikio's threshold, HDF5's chunk cache. NVIDIA has
enumerated the failure modes. This is independent confirmation of the spine the paper already adopted
(`PDSW26-PAPER-PLAN.md`): **the contribution is the attribution method, not the discovery of a
pathology.** Select the write case for what it demonstrates about attribution, not for novelty.

On that criterion the page-cache case is the strongest untested option, because the cost is inflicted by
**a different actor on a different I/O interface**: your GDS writes slow down because something else
touched the same file buffered. The aggregate `pg-cache` counter says it happened; nothing says which
writes collided, over which ranges, or who the other writer was. Answering that needs the unified
cuFile U POSIX op table, which is the hardest attribution problem in the paper.

## Verdict: stop hunting

Six candidates, six negatives. Leads 3 and 4 are now measured and closed above; lead 1 is dead on this
hardware by inspection (`nvidia-smi` reports **BAR1 = 65,536 MiB against 40 GB of HBM**, so every byte of
device memory can be mapped at once and the resource cannot be put under pressure — `Bar1-map` has read
`err=0 active=0` in every run of the whole campaign). Lead 2 is the only one left and it is a fishing
expedition: a counter that never fires is weak evidence of anything, and there is no hypothesis for what
would make one fire.

The search is complete enough to conclude from. **Choose the write case for what it demonstrates about
attribution and stop looking for novelty** — this is D5 in `../../paper/ADVISOR-FEEDBACK.md`, and the
cost of continuing is now measured against a deadline five days out with the re-spine untouched.

Recommendation, in order:

1. **Page-cache coherence** as the write case. Real, GDS-intrinsic, 1.8x, and the attribution problem is
   the hardest in the paper: the cost is inflicted by *a different actor on a different I/O interface*,
   so nothing but a unified cuFile-and-POSIX op table can name the collision. Verify what `gds_stats`
   reports there **before** drafting.
2. **kvikio sub-threshold writes** as the fallback. Measures beautifully (100% address attribution,
   0% time attribution) and is written up already; its only defect is being a second kvikio case.

## Method traps that cost time here

- `gdsio -U` is **option-order sensitive**: `-I`/`-x` reset it, so `-U` before them silently measures
  aligned behaviour (0.127 vs 0.021 GiB/s at 4 KiB). Place `-U` last.
- `gds_stats` prints nothing until `cufile_stats >= 1`; the `posix=`/`unalign=` counters are on the
  **per-GPU** line, not in the GLOBAL block.
- Verify against the NVIDIA docs **before** writing up a mechanism as a finding. Three candidates in
  this search turned out to be documented, twice after the write-up had already been drafted.
