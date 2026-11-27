# tools/

Offline attribution, experiment drivers and figure generation. Shell drivers run on a GDS node (see
[`../chameleon/`](../chameleon/README.md)); the Python attribution tools run anywhere on a captured trace.
Scripts resolve paths from the repository root; set `REPO=/path/to/gdsight` to override.

## Attribution (the method)

| file | role |
|---|---|
| `lba_match.py` | address basis: maps each `nvme_setup_cmd` sector through the file's FIEMAP extents to the covering operation |
| `batch_addr_attr.py` | address attribution per `cuFileBatchIOSubmit` entry |
| `posix_addr_attr.py` | address attribution for POSIX `pread` reads that bypass cuFile |
| `write_addr_attr.py` | address attribution for writes |
| `xfile_addr_attr.py` | one reverse physical-to-file index over several files |
| `compose_score.py` | composition: address first, time (`corr_id`) only when its key is valid |
| `classify_commands.py` | accounts for every device command against a file's extent map |
| `xlayer_amp.py` | per-operation command and byte amplification from a trace |
| `dfa_drive.py` | runs DFAnalyzer on a GDSight trace |

## Validation

| file | role |
|---|---|
| `oracle_run.sh`, `oracle_score.py` | scores `corr_id` and LBA attribution against disjoint-slot ground truth (`workloads/gds_oracle.c`) |
| `run_candidate_lifetime.sh` | effect of retiring completed operations from the address candidate set |
| `run_reps.sh` | repetitions with variance for the rate-based results |
| `ablate_datacrumbs.py` | what default DataCrumbs sees versus DataCrumbs with the GDSight plugins |
| `compat_div.sh` | true GDS versus forced compat on the same reads |

## Cases from the paper

| case | drivers | scoring |
|---|---|---|
| KvikIO retrieval mix | `run_ucb_kvikio_fix.sh`, `run_curated_suite.sh` | `mixed_score.py`, `posix_addr_attr.py` |
| HDF5 via the GDS VFD | `run_hdf5_gds.sh`, `trace_hdf5_gds.sh`, `trace_hdf5_gds_p2pdma.sh` | counts in the trace scripts |
| NIXL KV-cache reads | `run_nixl_e2e.sh`, `run_nixl_crosscheck.sh`, `nixl_crosscheck_reps.sh`, `run_nixl_diagnosis.sh`, `run_nixl_kvsweep.sh`, `run_nixl_batchsweep.sh`, `run_nixl_opt.sh` | `class_attr_score.py`, `nixl_devbytes.py`, `batch_addr_attr.py` |
| unaligned writes | `run_write_keystone.sh`, `run_write_sweep.sh` | `write_attr.py`, `write_addr_attr.py`, `rmw_proof.py` |
| small reads under contention | `run_tail_reps.sh`, `run_tail_untraced.sh`, `cap_tail_reps.sh` | `interfere_score.py`, `interfere_attrib.py` |
| command rate and granularity | `xlayer_sweep.sh`, `align_sweep.sh`, `amp_perf.sh`, `probe_effective_granularity.sh`, `rerun_granularity.sh` | `xlayer_amp.py` |

## Overhead, figures and helpers

| file | role |
|---|---|
| `overhead_bench.sh`, `overhead_table.py` | end-to-end tracer overhead (`results/overhead/`) |
| `make_figs.py`, `build_case_figs.py`, `make_arch_fig.py` | figures in `results/figures/` from committed data |
| `ensure_data_file.sh` | creates the shared test file the drivers read |
| `run_gdstrace_demos.sh` | end-to-end demos: trace, then DFAnalyzer |
| `gds_trace_preload.c` | a standalone `LD_PRELOAD` prototype of the per-op GDS-versus-POSIX view, predating the eBPF plugins; not used by the drivers |
