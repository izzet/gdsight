# Untraced control for the small-read tail latency

## Question

Every tail number in the paper is derived from the trace itself: `cap_tail_reps.sh` runs the
workload under `datacrumbs_run`, and `interfere_score.py --trace` reads p50/p99 from the captured
`cuFileRead` durations. Sec V-G reports 20 to 30% trace-all overhead on small high-IOPS streams, and
this is a small-read experiment, so the question is whether tracing produced the 22x tail.

## Setup

`workloads/gds_interfere_lat.c` is `gds_interfere.c` with `clock_gettime` around each small
(class-B) read, printing p50/p99 in process. The reads are synchronous `cuFileRead`, so the
in-process duration is the op's true latency, the same quantity the trace records.
`tools/run_tail_untraced.sh` runs both conditions with no tracer in the loop, n=3, at the same
parameters as the paper's claim (4 small threads x 3072 B, 4 large threads x 4 MiB).

## Result

| | p50 alone | p50 contended | p99 alone | p99 contended | p99 ratio | p50 delta |
|---|---:|---:|---:|---:|---:|---:|
| untraced | 127.2 | 139.4 | 209.9 | 5001.4 | 23.8x | +9.6% |
| traced (`tail_reps.csv`) | 136.3 | 148.7 | 214.3 | 4790.7 | 22.4x | +9.0% |

The effect is preserved and is slightly larger untraced (23.8x vs 22.4x), and the p50 shift agrees
(+9.6% vs +9.0%). Tracing does not create the tail. It slightly damps the ratio by putting a small
floor under the uncontended case (alone p99 214.3 traced vs 209.9 untraced), which is the expected
effect of per-op tracing overhead on a latency-sensitive small read.

## Interpretation

The paper's 22.4x is the more conservative of the two numbers. The causal diagnosis (slow ops overlap
3 to 8 large-region NVMe commands, fast ops overlap zero) still requires the trace, since only the
cross-layer view supplies the overlap count. The magnitude does not.

## Reproduce

Raw: `tail_untraced.csv`. Driver: `tools/run_tail_untraced.sh`. Workload: `workloads/gds_interfere_lat.c`.
