# The GDS write path: two pathologies the built-in tools cannot explain

Reads were the only path the paper evaluated. This closes that gap with a write keystone, and the
write path turns out to carry a *stronger* result than the read side: a pure-write workload makes the
device perform **reads**, and no API-level or GDS-native counter can show it.

**Config.** A100-PCIE-40GB, CUDA 12.6 / libcufile 2.12 / nvidia-fs 2.28, ext4 `data=ordered` (4 KiB FS
block, 512 B sector) on a local Samsung PM983-class NVMe, `gdsio` GPU_DIRECT (`-x 0`) random writes
(`-I 3`), 64 MiB requested per case, caches dropped before each run. `-U` selects unaligned (non-4 KiB)
random offsets. Device counts come from the per-op trace filtered to the workload's pid, **not** from
`iostat`: the node's NVMe is shared with other tenants whose traffic pollutes `/proc/diskstats`.

## Result 1 (4 KiB unaligned) - the silent GDS write bypass

| layer | observation |
|---|---|
| application | **16,384 `cuFileWrite`**, 64.0 MiB requested |
| GDS (nvidia-fs) | **`nvfs_io` = 6**, `p2p_mapping` = 6 |
| POSIX fallback | **32,756 `pwrite64`** + 32,756 `fdatasync` (2 per write) |
| device | **46,956 commands = 14,194 READ (55.4 MiB) + 32,762 WRITE (128.0 MiB)** |
| amplification | 0.87x read + **2.00x write** = **2.87x** device bytes per requested byte |
| attribution | **46,956 / 46,956 (100%)**, including **14,194 / 14,194** of the RMW reads |

The application issues 16,384 GDS writes and 6 of them reach the GDS path. cuFile silently serves the
rest through POSIX `pwrite64`, splitting every 4 KiB write into two block writes. Throughput drops from
**0.127 GiB/s aligned to 0.021 GiB/s (6.1x)**.

**What the built-in tools report for this run.** nvidia-fs `Writes` moved by **+6 ops and +0 MiB**;
nvidia-fs `Reads` did not move **at all**. `gds_stats` is therefore not merely imprecise here, it is
structurally blind: the work left the GDS path, so the GDS-native counter never sees it. `iostat` does
see device traffic, but it cannot say which operation caused it, cannot separate it from the other
tenant's I/O, and cannot explain why a write-only workload is issuing reads.

## Result 2 (16 KiB unaligned) - healthy-looking GDS that is 7.8x slow

This is the sharper case, because here the GDS counter says everything is fine.

| layer | observation |
|---|---|
| application | 4,096 `cuFileWrite`, 64.0 MiB requested |
| GDS (nvidia-fs) | **`nvfs_io` = 4,096, `p2p_mapping` = 4,096 - a clean 1:1, "100% GDS engaged"** |
| POSIX (hidden) | **8,190 `pwrite64`** - two per write, behind the API's back |
| device | **19,128 commands = 6,842 READ (26.7 MiB) + 12,286 WRITE (80.0 MiB)** |
| amplification | 0.42x read + 1.25x write = **1.67x** |
| attribution | **19,128 / 19,128 (100%)**, including **6,842 / 6,842** of the RMW reads |

cuFile splits each misaligned 16 KiB write into an aligned P2P body plus two unaligned edge fragments,
serves the body over true GDS and the **edges through POSIX**, and the edges force the device to read
whole 4 KiB blocks before writing them back. `gds_stats` counts only the body, so it reports a perfectly
healthy 1:1 GDS workload while throughput sits at **0.041 GiB/s against 0.353 GiB/s aligned (7.8x)**.

## The bypass is size-dependent, not a single chosen point

nvidia-fs `Writes` delta (GDS-specific, so other tenants cannot pollute it) against `gdsio` op count:

| size | aligned tput | unaligned tput | slowdown | GDS ops engaged (unaligned) |
|---|---|---|---|---|
| 4 KiB | 0.127 GiB/s | 0.021 GiB/s | 6.1x | **6 / 16,384 (bypass)** |
| 16 KiB | 0.353 | 0.045 | 7.8x | 4096 / 4096 (healthy) |
| 64 KiB | 0.679 | 0.142 | 4.8x | 1024 / 1024 (healthy) |
| 1 MiB | 0.928 | 0.651 | 1.4x | 64 / 64 (healthy) |

Two distinct pathologies separated by size. At or below the 4 KiB FS block a misaligned write leaves GDS
entirely. Above it GDS stays engaged and the cost moves to device-side read-modify-write, which decays
as the aligned body grows relative to the two edges. Misalignment costs something at **every** size
tested, and at no size does any GDS-native counter reveal why.

## Aligned control (true GDS write path)

`cuFileWrite` = 64 -> `nvfs_io` = 64 -> `p2p_mapping` = 64 -> **64 NVMe commands, all `op=1` (write)**,
1.0x amplification, 0.928 GiB/s. The write path is clean when aligned, which is what makes the
misaligned cases attributable rather than ambient.

## Why this needs per-op cross-layer attribution

Three observers, one truth, on a **write** workload:

| observer | Result 1 (4 KiB) | Result 2 (16 KiB) |
|---|---|---|
| `gds_stats` / nvidia-fs | +6 ops, +0 MiB, 0 reads - **blind** | 4096/4096 - reports **healthy** |
| `iostat` / diskstats | device traffic, no cause, polluted by co-tenants | same |
| **GDSight** | bypass named, 100% of commands attributed | hidden POSIX edges + RMW named, 100% attributed |

The device-side **direction** field is what makes this expressible: without it a device command is just
bytes at a sector, and "a write workload issued 14,194 reads" cannot be stated at all. Every device
command, read or write, carries the `corr_id` of the `cuFileWrite` that caused it, so the amplification
is reported per op rather than as a workload aggregate.

**Honest notes.** Read amplification is below 1.0x in bytes because the POSIX fallback path is buffered,
so the page cache absorbs part of the read-modify-write; the *device* read commands are nonetheless real
and are what the tool attributes. The 100% attribution figures are for a single-threaded writer, which
is the sync regime where the time basis is exact; concurrent async writers would fall back to the
address basis exactly as on the read path. Magnitudes are single-drive.

## Reproduce

```
tools/run_write_keystone.sh            # both cases + the aligned control
tools/run_write_sweep.sh               # the size x alignment sweep
```
Requires the direction-aware block probe (`req->cmd_flags & REQ_OP_MASK`); see
`DATACRUMBS-GDS-BUILD-LOG.md` for the build ordering it depends on.
