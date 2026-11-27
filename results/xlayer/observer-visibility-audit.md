# What `gds_stats` reports: per-case visibility audit

At verbosity 3, the per-GPU line of `gds_stats` carries `posix=` and `unalign=` counters that report
some cuFile fallbacks directly, including unaligned writes. Because the paper states for several cases
that this observer reports nothing, each case is checked here by measurement.

**Result.** Every read-side case in the paper is confirmed: `gds_stats` reports no fallback. Unaligned
writes are reported by cuFile's `posix=` and `unalign=` counters; that case is documented in
[`unaligned-writes.md`](unaligned-writes.md).

## Setup
All measurements use `gds_stats -p <pid> -l 3` with `cufile_stats: 3` (the default is 0, and gds_stats
prints nothing until it is enabled). The relevant counters are on the per-GPU line, not in the GLOBAL
block; truncating the output before that line hides them.

## Where the fallback happens

| fallback located | can cuFile see it? | example |
|---|---|---|
| above cuFile (library decides not to call it) | No - never enters cuFile | kvikio sub-threshold `pread` |
| inside cuFile (compat / RMW / unaligned) | Yes - `posix=`, `unalign=` | unaligned writes, forced compat |

A statement that cuFile cannot see a fallback is only safe for the first row.

## Results by case

### kvikio sub-threshold bypass (Section V-A): no fallback reported
300,000 reads, 60% below the 16 KiB threshold:

```
gds_stats : Read: n=11680 posix=0 unalign=0 err=0
nvidia-fs : +119,749 reads   (the large ops only)
workload  : 119,749 large (GDS)  /  180,251 small (POSIX above cuFile)
```

cuFile reports `posix=0` and zero errors while 60% of the workload's reads are invisible to it; they
never entered cuFile. The statement "all-GDS healthy, nothing reported" is correct, and is measured
here.

### cuFile-internal compat reads: fallback reported
Forced compat (`CUFILE_FORCE_COMPAT_MODE=true`), 64 KiB reads:

```
gds_stats : Read: n=39714 posix=39714      <- 100% reported as posix
nvidia-fs : +0 reads                       <- confirmed never reached the GDS path
```

When the fallback is inside cuFile, cuFile counts it. A statement that an operator running gds_stats
would see nothing in this situation would be false.

### Unaligned writes: fallback reported (see [`unaligned-writes.md`](unaligned-writes.md))

```
4 KiB unaligned : Write: n=29507 posix=29505 unalign=29505
16 KiB unaligned: Write: n=31246 posix=20831 unalign=31246
aligned         : posix=0 unalign=0
```

### HDF5 chunk cache: no fallback reported
This case was expected to be reported, because the reads do enter cuFile (`cuFileRead` with
`nvfs_io = 0`), which places it structurally in the second row above. Measured on a rebuilt HDF5 1.14.5
+ `nv-legate/vfd-gds`, 4 GiB chunked dataset, 256 KiB chunks, chunk cache ON:

```
GLOBAL  : Read: ok = 15898  err = 0     Total Read Size (MiB): 1952
PER-GPU : Read: bw=0 util(%)=0 n=0 posix=0 unalign=0 err=0 MiB=0
nvidia-fs Reads delta: 0                (never reached the GDS path)
```

`posix=0`: cuFile does not flag this fallback, unlike the unaligned-write and forced-compat cases. The
operator sees 15,898 successful reads, ~2 GB moved, zero errors, and no fallback indication. The
statement "compat OK, nothing reported" is correct.

The cache ON/OFF comparison reproduces on the fresh build (64 MiB, 256 KiB chunks): cache ON `+0` GDS
reads at 0.809 GiB/s, cache OFF `+256` (`+65` MiB) at 0.591, contiguous `+72` (`+64` MiB) at 1.755.

Limitation: the per-GPU line does read `n=0`, so a careful operator at `-l 3` could infer that no
GPU-direct read occurred. What cuFile never states is that the reads went through a host bounce, or
what the device did. The statement is therefore "no fallback is reported", not "no signal exists
anywhere in the output".

### Why cuFile flags some internal fallbacks and not others
`posix=` fires for unaligned writes (29,505) and forced compat (39,714) but not for the HDF5
host-staged path (0), although all three are cuFile-internal. Whatever the reason inside libcufile, the
operational consequence is that the posix counter cannot be relied on to reveal that GDS did not
engage. It fires for some fallbacks and not others, and only a cross-layer view distinguishes them
without knowing in advance which kind is present.

## What holds in every case

`gds_stats` can report that a fallback happened. In no case does it report:

- the device-side consequence. It counts requested bytes at the cuFile boundary, not the commands the
  device executes. Under unaligned writes it reports `Read: n=0, MiB=0` while the device issues 14,194
  read commands (verified read-modify-write).
- which operation paid, at what offset, and with what amplification. Its counters are per-GPU
  aggregates.

The supportable statement across all cases is therefore attribution and device-side quantification,
not detection. Where a pathology is detectable by an existing counter, that should be stated, and the
claim should be the per-op device-level measurement.

## Method notes

- `gds_stats` prints nothing unless `cufile_stats` >= 1 in the effective `cufile.json`; point at a
  modified copy with `CUFILE_ENV_PATH_JSON`. `/etc/cufile.json` is a symlink to
  `/etc/alternatives/cufile.json` and is byte-identical to the CUDA one here.
- The `posix=`/`unalign=` counters are on the per-GPU line (line 48 of `-l 3` output), not in the
  GLOBAL block. Truncating the output hides them.
- `gdsio`'s `-U` is option-order sensitive: `-I`/`-x` reset it, so `-U` placed before them silently
  measures aligned behaviour (0.127 vs 0.021 GiB/s at 4 KiB). Always place `-U` last.
