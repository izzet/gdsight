# What `gds_stats` actually reports: an audit of every blindness claim

Prompted by an error found in the write result. That result initially claimed `gds_stats` was blind to
an unaligned-write fallback. It is not: at verbosity 3 the **per-GPU line carries `posix=` and
`unalign=` counters** that report the fallback directly. Since the paper makes a "this observer sees
nothing" claim for several cases, each one is re-checked here **by measurement rather than by
argument**.

**Outcome: every read-side claim in the paper survives.** The one wrong claim was in the new write
result and has been retracted. Details per case below.

Everything below uses `gds_stats -p <pid> -l 3` with `cufile_stats: 3` (default is 0, so gds_stats
prints nothing until enabled), reading the **per-GPU** line, not just the GLOBAL block. The earlier
mistake came from truncating the output before that line.

## The decisive distinction: where the fallback happens

| fallback located | can cuFile see it? | example |
|---|---|---|
| **above** cuFile (library decides not to call it) | **No** - never enters cuFile | kvikio sub-threshold `pread` |
| **inside** cuFile (compat / RMW / unaligned) | **Yes** - `posix=`, `unalign=` | unaligned writes, forced compat |

This is review item R10. A claim of blindness is only safe for the first row.

## Case-by-case measurements

### kvikio sub-threshold bypass (the Section V-A keystone) - CLAIM HOLDS
300,000 reads, 60% below the 16 KiB threshold:

```
gds_stats : Read: n=11680 posix=0 unalign=0 err=0
nvidia-fs : +119,749 reads   (the large ops only)
workload  : 119,749 large (GDS)  /  180,251 small (POSIX above cuFile)
```

cuFile reports **`posix=0` and zero errors** while 60% of the workload's reads are invisible to it: they
never entered cuFile at all. "All-GDS healthy -> nothing" is correct, and now measured rather than
asserted.

### cuFile-internal compat reads - CLAIM WOULD NOT HOLD
Forced compat (`CUFILE_FORCE_COMPAT_MODE=true`), 64 KiB reads:

```
gds_stats : Read: n=39714 posix=39714      <- 100% reported as posix
nvidia-fs : +0 reads                       <- confirmed never reached the GDS path
```

When the fallback is **inside** cuFile, cuFile counts it. Any claim that an operator running gds_stats
would see "nothing" in this situation is false.

### Unaligned writes - CLAIM RETRACTED (see write-keystone.md)

```
4 KiB unaligned : Write: n=29507 posix=29505 unalign=29505
16 KiB unaligned: Write: n=31246 posix=20831 unalign=31246
aligned         : posix=0 unalign=0
```

### HDF5 chunk cache - CLAIM HOLDS (measured, and the interesting case)
Predicted to fail, because the reads **do** enter cuFile (`cuFileRead` with `nvfs_io = 0`), which is
structurally the second row above. Measured on a rebuilt HDF5 1.14.5 + `nv-legate/vfd-gds`, 4 GiB
chunked dataset, 256 KiB chunks, chunk cache ON:

```
GLOBAL  : Read: ok = 15898  err = 0     Total Read Size (MiB): 1952
PER-GPU : Read: bw=0 util(%)=0 n=0 posix=0 unalign=0 err=0 MiB=0
nvidia-fs Reads delta: 0                (never reached the GDS path)
```

**`posix=0`.** cuFile does *not* flag this fallback, unlike the unaligned-write and forced-compat cases.
The operator sees 15,898 successful reads, ~2 GB moved, zero errors, and no fallback indication.
"Compat OK -> nothing" is correct.

The A/B still reproduces on the fresh build (64 MiB, 256 KiB chunks): cache ON `+0` GDS reads at 0.809
GiB/s, cache OFF `+256` (`+65` MiB) at 0.591, contiguous `+72` (`+64` MiB) at 1.755.

*Nuance worth keeping honest:* the per-GPU line does read `n=0`, so a careful operator at `-l 3` could
infer that no GPU-direct read occurred. What cuFile never states is that the reads went through a host
bounce, nor what the device did. The claim is therefore "no fallback is reported", not "no signal
exists anywhere in the output".

### Why cuFile flags some internal fallbacks and not others
`posix=` fires for unaligned writes (29,505) and forced compat (39,714) but **not** for the HDF5
host-staged path (0), even though all three are cuFile-internal. Whatever the reason inside libcufile,
the operational consequence is the useful part: **the posix counter cannot be relied on to reveal that
GDS did not engage.** It fires for some fallbacks and stays silent for others, and only a cross-layer
view distinguishes them without knowing in advance which kind you have.

## What remains true in every case

`gds_stats` can report **that** a fallback happened. In no case does it report:

- the **device-side consequence**: it counts requested bytes at the cuFile boundary, not the commands
  the device executes. Under unaligned writes it reports `Read: n=0, MiB=0` while the device issues
  14,194 read commands (verified read-modify-write).
- **which operation** paid, at what offset and what amplification. Its counters are per-GPU aggregates.

So the defensible line across the whole paper is *attribution and device-side quantification*, not
*detection*. Where a pathology is detectable by an existing counter, say so and claim the per-op
device-level measurement instead.

## Method note (cost real time twice)

- `gds_stats` prints nothing unless `cufile_stats` >= 1 in the effective `cufile.json`; point at a
  modified copy with `CUFILE_ENV_PATH_JSON`. `/etc/cufile.json` is a symlink to
  `/etc/alternatives/cufile.json` and is byte-identical to the CUDA one here.
- The `posix=`/`unalign=` counters live on the **per-GPU** line (line 48 of `-l 3` output), *not* in the
  GLOBAL block. Truncating the output hides exactly the evidence that matters.
- `gdsio`'s `-U` is option-order sensitive: `-I`/`-x` reset it, so `-U` before them silently measures
  aligned behaviour (0.127 vs 0.021 GiB/s at 4 KiB). Always place `-U` last.
