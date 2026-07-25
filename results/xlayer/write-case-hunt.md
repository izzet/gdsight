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

## Remaining leads, ordered by how plausibly undocumented

1. **BAR1 / GPU-memory pressure.** The only resource that is GDS-exclusive. Does cuFile degrade or
   silently fall back under registered-buffer pressure? Caveat: BAR1 is 64 GiB against 40 GB of HBM on
   this A100, so it may not be exhaustable here.
2. **Counters that never fire.** `dr=`, `r_sparse=`, `r_inline=` read 0 in every run recorded so far. A
   condition that makes one fire is behaviour nobody documents.
3. **Concurrency scaling of the P2P write path itself.** Docs characterise single-stream. A plateau or
   collapse at N threads would be new.
4. **Cliffs in documented knobs** (`max_io_queue_depth`, cuFile internal cache size). Documented as
   knobs, never characterised quantitatively.

## Method traps that cost time here

- `gdsio -U` is **option-order sensitive**: `-I`/`-x` reset it, so `-U` before them silently measures
  aligned behaviour (0.127 vs 0.021 GiB/s at 4 KiB). Place `-U` last.
- `gds_stats` prints nothing until `cufile_stats >= 1`; the `posix=`/`unalign=` counters are on the
  **per-GPU** line, not in the GLOBAL block.
- Verify against the NVIDIA docs **before** writing up a mechanism as a finding. Three candidates in
  this search turned out to be documented, twice after the write-up had already been drafted.
