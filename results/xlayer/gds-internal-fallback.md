# Silent GDS loss: library-specific bypass vs GDS-internal fallback

## Question

The `rag_mixed` bypass comes from kvikio's 16 KiB threshold. Is that a kvikio behavior, or is there
a fallback internal to GDS that any user can hit? The GDS-internal candidates were tested with direct
cuFile (no library).

## Two distinct mechanisms of silent GDS loss

| mechanism | trigger | who decides | enters cuFile? | gds_stats sees it? |
|---|---|---|---|---|
| library threshold bypass | read < 16 KiB | kvikio (`KVIKIO_GDS_THRESHOLD`) | no (goes POSIX) | no (not a cuFile op) |
| cuFile compat fallback | file on non-`data=ordered` / unsupported FS | cuFile (GDS-internal) | yes, but bounces | yes (aggregate `posix` counter) |
| buffer not registered | — | — | yes, still true P2P | (ruled out; not a fallback) |

## Result

Direct cuFile (`workloads/gds_align_probe.c`, 1000×64 KiB):
- `/mnt/nvme1` (ext4 `data=ordered`): cuFileRead 1000, nvfs_io 1000, p2p 1000: true GDS.
- `/` (ext4, no `data=ordered`): cuFileRead 1000 succeeds with nvfs_io 0, p2p 0: silent POSIX bounce.
- Unregistered buffer on the GDS mount: cuFileRead 1000, nvfs_io 1000, p2p 1000: still true P2P
  (cuFile registers internally, so registration is not a fallback trigger).

## Interpretation

- The kvikio 16 KiB bypass is library-specific: a kvikio policy, not a GDS property. As a claim about
  how representative it is, it is narrow (DALI and direct cuFile do not do it).
- There is also a GDS-internal, library-independent silent fallback: cuFile bounces to POSIX based on
  the mount/filesystem (`data=ordered`/supported FS), returning success with zero GDS. This confirms
  the original premise with no library in the loop.
- Tool visibility differs. The library bypass is invisible to gds_stats (the reads never enter
  cuFile). The GDS-internal compat fallback is visible to gds_stats in aggregate (its `posix`
  counter), but not per op or per file, and not with its device cost.
- The useful capability is therefore mechanism-agnostic: a real pipeline can lose GDS through either
  path, and only per-op cross-layer attribution gives a single per-op view of whether an op got GDS,
  via which path, and what it cost at the device. `gds_stats` misses the library-bypassed ops entirely
  and shows the compat ones only in aggregate.

## Limitations

- The non-`data=ordered` mount here is `/dev/sda3` (SATA), so the bounced reads do not generate
  `nvme_setup_cmd` (the block probe is nvme-specific). The device-side cost of the FS-compat bounce on
  a non-nvme disk is not captured; on an nvme non-`data=ordered` mount it would be.
- The FS-compat fallback is documented (the `data=ordered` requirement). What is new is per-op/
  per-file detection in mixed-mount setups, not the existence of the requirement.

## Summary

The bypass trigger is not a GDS-internal discovery; it is kvikio policy. The result is per-op
cross-layer attribution that is mechanism-agnostic, combining library bypass, GDS-internal compat
fallback and device amplification into one view that no existing tool provides. The GDS-internal
fact (mount-based silent compat) is confirmed but largely documented.
