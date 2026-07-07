# HDF5-GDS recovery + keystone log

Running log for recovering work lost when a prior working session's instance was torn down before
committing, and for rebuilding the HDF5+GDS keystone on top of the current tree. Raw source of the
lost session: **`convo-paper.md`** (committed alongside this file).

## Why this exists — the three buckets of lost/pending work
A prior session (transcript in `convo-paper.md`) did substantial work that **never reached git**; the
instance was then torn down and the artifacts (all in `$HOME`/scratch, outside the repo) were lost.
Split into three buckets:

- **Bucket 1 — lost experimental work (must re-run).** A full HDF5 + `vfd-gds` keystone experiment.
  Headline finding: **the HDF5 chunk cache silently defeats GPUDirect Storage** — a chunked dataset
  (the default layout) read with the chunk cache ON issues N `cuFileRead` calls but `nvfs_io = 0`
  (silent cuFile compat / CPU bounce, no P2P); chunk cache OFF → true GDS (`nvfs_io = N`); contiguous
  → true GDS. Verified A/B on NVIDIA's production `nv-legate/vfd-gds` fork; only GDSight's cross-layer
  `nvfs_io=0` reveals it (`gds_stats`/`iostat` look healthy). Secondary: `hpc-io/vfd-gds` never opens
  `O_DIRECT` (compat even for contiguous). Intended as the paper's scientific-HPC keystone, extending
  the Ravi/Byna/Koziol PDSW'20 "GPU Direct I/O with HDF5" VFD lineage. Code/results LOST → re-run.
- **Bucket 2 — decided-but-unapplied paper edits.** POMACS/Sedaghatgoo cite
  (`sedaghatgooScalableStorageArchitectures2026`) in bib + intro + related-work; §1 reframe (center the
  *diagnosis*, not the *fix*); async repositioning so C2 (observability gap) + C3 (pathologies) are
  load-bearing and async is "sharpest case, already shipping in NIXL," not the paper's bet. NOT in git.
- **Bucket 3 — development-history voice rewrite.** Purge every phrase implying a prior
  conversation/objection/walk-back ("necessity proven", "wrong or futile", "why this is hard",
  challenge→solution "one-to-one", "(supporting)", "known physics/textbook", "don't-bother",
  "we do not claim", the adversarial §5 "obvious fix vs. our fix" spine). Present as a design conceived
  whole from first principles, declaratively. Full A–E inventory is in `convo-paper.md` (end).

Order (per project owner): finish Bucket 1 on this instance first, then Bucket 2, then Bucket 3.

## 2026-07-07 — instance re-verified working (Bucket 0)
Fresh Chameleon bare-metal node `izzet-gdstrace-node` (A100-PCIE-40GB, driver 560.35.05, kernel
`6.8.0-1051-nvidia`, `nvidia_fs` loaded). Two per-instance steps applied:
1. `mount_gds_nvme.sh` — both local NVMe (`nvme0n1`,`nvme1n1`, 3.5T Samsung PM983-class) were **raw**
   (no fs/partition/signature — verified via blkid/wipefs/sfdisk), so `DEV=/dev/nvme1n1 FORMAT=1`
   → ext4 (4k blocks) mounted `-o data=ordered` at `/mnt/nvme1`, scratch `/mnt/nvme1/gdstrace-smoke`.
   `gdscheck -p`: **NVMe: Supported | IOMMU: disabled**.
2. `chameleon/setup_datacrumbs_runtime.sh` — eBPF caps on `~/dc-prefix/sbin/datacrumbs` +
   `/var/run/datacrumbs`. (Mandatory before any traced run.)

**Replication check — traced gdsio 4 MiB GDS read** (`tools/run_gdstrace_demos.sh gdsio`): gdsio
`XferType: GPUD`, 2.51 GiB/s (true GDS). Cross-layer trace (robust `errors='replace'` parse):
`cuFileRead=64 → nvfs_io=64` (1:1 true GDS) → `nvfs_get_p2p_dma_mapping=256` (4 P2P mappings per
4 MiB read = the ~4× device-command amplification the paper reports at large sizes). DFAnalyzer
per-op attribution: 64 ops / 256 MiB requested, overlap 3.90× (4 gdsio worker threads). **Stack works
end-to-end; known result reproduced.**

**Gotcha reconfirmed:** the `datacrumbs_run` "inline-shell-kill" — a downstream `grep | head -2` in a
pipe SIGKILLs the wrapper, but the trace still flushes (the SIGINT-first stop fix holds). Find + score
the trace separately rather than trusting the wrapper's stdout in a pipe.

## 2026-07-07 — Bucket 1: HDF5+GDS built + chunk-cache finding REPRODUCED
Built HDF5 **1.14.5** (serial) → `~/hdf5-gds` and **`nv-legate/vfd-gds`** (production fork; opens
O_DIRECT + device-pointer branch) → `~/vfd-gds` via `chameleon/build_hdf5_gds.sh`. Build gotcha: the
VFD's bundled `examples/` + `test/gds_test` don't link the CUDA runtime (undefined `cudaMalloc`) and
`gds_test` uses parallel-HDF5 collective calls absent in serial HDF5 → `-DBUILD_EXAMPLES=OFF
-DBUILD_TESTING=OFF` (only `libhdf5_vfd_gds.so` is needed). Workload `workloads/hdf5_gds.c` (reads-only;
file created host-side with default SEC2 VFD; GDS VFD only on read; toggles `H5Pset_chunk_cache`).

**Signal gotcha:** `/proc/driver/nvidia-fs/stats` needs **`rw_stats_enabled=1`** (per-boot:
`echo 1 | sudo tee /sys/module/nvidia_fs/parameters/rw_stats_enabled`) or all counters read 0 even for
genuine GDS. The live counter is the **`Reads : n=… readMiB=…`** line; the `Ops: Read=` line is a dead
legacy field (always 0) — do not parse it.

**A/B (64 MiB dataset, 256 KiB chunks), `tools/run_hdf5_gds.sh`:**
| case | nvfs Reads Δ | verdict |
|---|---|---|
| chunked, chunk-cache **ON** (HDF5 default) | **+0** | **silent compat — zero P2P** |
| chunked, chunk-cache **OFF** (fix) | +256 (+65 MiB) | true GDS (all 256 chunks P2P) |
| contiguous | +72 (+64 MiB) | true GDS |

Matches the lost session exactly: **the HDF5 raw-data chunk cache silently defeats GPUDirect Storage.**
A chunked "GDS" read issues normal `cuFileRead` calls but 0 device P2P — invisible to `gds_stats`
(sees cuFile activity) and `iostat` (sees device reads); only the cross-layer `nvfs_io=0` names it.

Perf wrinkle (the honest "so what", to quantify next): at 256 KiB chunks true-GDS chunked (0.58 GiB/s)
is *slower* than compat (0.81) — per-chunk P2P submission overhead — while contiguous (1.58) beats both.
So the fix isn't "force GDS on the chunked layout"; the lever is layout (contiguous/large extents).
Need a chunk-size sweep + CPU numbers for a clean cost story.

## Next
- Task 4: trace both cache-ON/OFF with GDSight (authoritative cuFileRead>0 vs nvfs_io=0/N cross-layer
  evidence); chunk-size sweep + CPU for the "so what". Task 5: `results/xlayer/hdf5-gds.md` + commit.
