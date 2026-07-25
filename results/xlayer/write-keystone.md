# The GDS write path: per-op attribution of read-modify-write amplification

Reads were the only path the evaluation covered. This closes that gap. **It is an attribution result,
not a discovery**: the mechanism is documented by NVIDIA, and cuFile's own statistics expose the
fallback. What no existing tool supplies is the device-side consequence, per operation.

## What is already known (state this first, and plainly)

NVIDIA documents the mechanism in as many words: *"When the write workload is unaligned, GDS uses
Read-Modify-Write internally using POSIX mode"*, and *"GDS might use the internal cache when ... the
file_offset ... size ... or devPtr_base issued in cuFileRead/cuFileWrite is not 4K aligned"*
([Best Practices Guide](https://docs.nvidia.com/gpudirect-storage/best-practices-guide/index.html),
[Overview Guide](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html)).

Worse for any "silent bypass" claim: **`gds_stats -l 3` reports the fallback directly.** Its per-GPU
line carries `posix=` and `unalign=` counters, measured here:

| case | gds_stats per-GPU write line | verdict |
|---|---|---|
| 4 KiB unaligned | `n=29507 posix=29505 unalign=29505 MiB=57` | fallback fully visible (99.99%) |
| 16 KiB unaligned | `n=31246 posix=20831 unalign=31246 MiB=162` | fallback fully visible |
| aligned | `posix=0 unalign=0` | clean |

So on the **write** path the bypass is *not* silent to cuFile, unlike the kvikio read-side case where
the shortcut happens in the library **above** cuFile and is therefore genuinely invisible to it. That
distinction is review item R10 and must not be blurred.

## What no tool supplies: the device-side cost, per op

cuFile knows it took the POSIX path. It does not know, and does not report, what that costs the device.
For the same runs, cuFile stats report **`Read: n=0 ... MiB=0`** - zero reads - while the device
executes thousands of 4 KiB read commands.

**Result 1, 4 KiB unaligned** (16,384 `cuFileWrite`, 64.0 MiB requested):

| layer | observation |
|---|---|
| cuFile stats | 57 MiB written, **0 reads**, posix/unalign flagged |
| GDS (nvidia-fs) | `nvfs_io` = 6 (the work left the GDS path) |
| POSIX | 32,756 `pwrite64` + 32,756 `fdatasync` |
| device | **46,956 cmds = 14,194 READ (55.4 MiB) + 32,762 WRITE (128.0 MiB)** |
| amplification | 0.87x read + 2.00x write = **2.87x** device bytes per requested byte |
| attribution | **46,956 / 46,956 (100%)**, incl. **14,194 / 14,194** RMW reads |

**Result 2, 16 KiB unaligned** (4,096 `cuFileWrite`, 64.0 MiB requested). Here `nvfs_io` is a clean
**4,096 / 4,096**: cuFile splits each misaligned write into an aligned P2P body plus two POSIX edge
fragments, so the *nvidia-fs op counter* looks like a healthy 1:1 GDS workload even though 8,190 POSIX
writes happen alongside it:

| layer | observation |
|---|---|
| GDS (nvidia-fs) | `nvfs_io` = 4,096, `p2p` = 4,096 - a 1:1 that hides the split |
| POSIX | 8,190 `pwrite64` (two per write) |
| device | **19,128 cmds = 6,842 READ (26.7 MiB) + 12,286 WRITE (80.0 MiB)** |
| amplification | 0.42x read + 1.25x write = **1.67x** |
| attribution | **19,128 / 19,128 (100%)**, incl. **6,842 / 6,842** RMW reads |

## The reads really are read-modify-write (three independent checks)

"A write workload issues device reads" is only meaningful if those reads are RMW of the blocks being
written, rather than metadata, readahead, or a co-tenant's traffic. Using the per-op `corr_id`
(`tools/rmw_proof.py`):

| check | 4 KiB | 16 KiB |
|---|---|---|
| reads paired with a write to the **same sector in the same op** | **14,194 / 14,194 (100%)** | **6,842 / 6,842 (100%)** |
| reads landing inside the data file's extents (rules out metadata) | **100%** | **100%** |
| read size exactly one 4 KiB FS block | 14,194 | 6,842 |
| ops that needed RMW at all | 9,003 / 16,384 (54.9%) | 3,772 / 4,096 (92.1%) |

Same-op, same-sector read-then-write is the RMW signature and nothing else produces it.

## Cost, and its size dependence

Documentation says "there might be some performance impact". Measured, with `-U` (unaligned offsets):

| size | aligned | unaligned | slowdown | nvidia-fs ops engaged (unaligned) |
|---|---|---|---|---|
| 4 KiB | 0.127 GiB/s | 0.021 GiB/s | **6.1x** | 6 / 16,384 |
| 16 KiB | 0.353 | 0.045 | **7.8x** | 4096 / 4096 |
| 64 KiB | 0.679 | 0.142 | 4.8x | 1024 / 1024 |
| 1 MiB | 0.928 | 0.651 | 1.4x | 64 / 64 |

Aligned control on the true GDS write path: `cuFileWrite`=64 -> `nvfs_io`=64 -> `p2p`=64 -> 64 NVMe
commands, all `op=1`, 1.0x, 0.928 GiB/s.

## The defensible claim

Not "we found a hidden pathology". Rather: a documented qualitative behaviour ("some performance
impact") becomes a **measured, per-operation, device-level quantity** - 2.87x amplification, a third of
the device commands being reads on a pure-write workload, each attributed to the exact `cuFileWrite`
that caused it. This is the write-side counterpart to the read amplification section, which already
takes the same stance: the 4 KiB grid is known physics, but no tool reports which op pays it or how much.

The device-side **direction** field is what makes it expressible at all. Without it a device command is
bytes at a sector, and "this write caused 14,194 device reads" cannot be stated.

## Honest notes

- The fallback is **visible** to `gds_stats -l 3` (`posix=`, `unalign=`). Do not call it silent.
- Read amplification is below 1.0x *in bytes* because the fallback path is buffered, so the page cache
  absorbs part of the RMW. The device read *commands* are real and are what is attributed.
- 100% attribution is the single-threaded sync regime, where the time basis is exact. Concurrent async
  writers would fall back to the address basis as on the read path.
- `gdsio`'s `-U` is **option-order sensitive**: `-I`/`-x` reset it, so `-U` placed before them is
  silently ignored and the run measures aligned behaviour. All numbers here place `-U` last. This
  invalidated an earlier draft of the gds_stats comparison and is worth guarding in any rerun.
- Single drive, single writer. Magnitudes do not transfer.

## Reproduce

```
tools/run_write_keystone.sh    # both cases + aligned control (traced, per-op)
tools/run_write_sweep.sh       # size x alignment sweep
tools/rmw_proof.py TRACE FILE  # the three RMW checks
```
Requires the direction-aware block probe (`req->cmd_flags & REQ_OP_MASK`); see
`DATACRUMBS-GDS-BUILD-LOG.md` for the build ordering it depends on.
