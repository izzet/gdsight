# Paper claims audit, 2026-08-05

Every quantitative claim in `paper/gdsight.tex` traced to a committed artifact and recomputed from raw
data where the artifact carries per-rep values. 189 distinct quantitative mentions extracted
mechanically, then verified source-by-source.

**Verdict: 9 defects, all of them provenance rather than measurement.** No claim was found to be
contradicted by its own data. The recurring failure mode is the paper quoting a *superseded run* for one
quantity while quoting the *current run* for another quantity in the same sentence.

---

## Verified, reproduces exactly

Recomputed from raw per-rep rows, not copied from a summary line:

| claim | paper | recomputed | source |
|---|---|---|---|
| kvikio optimization throughput | 0.152+/-0.012 -> 2.602+/-0.031 GiB/s | 0.1518+/-0.0115 -> 2.6024+/-0.0305 | `reps.csv`, n=5 each |
| kvikio speedup | 17.1+/-1.3x | 17.14x, propagated error 1.31 | `reps.csv` |
| HDF5 compat CPU penalty | 27%, 1.32 vs 1.03 s | 1.318 vs 1.034 s = 27.5% | `hdf5_cpu_reps.csv`, n=5 |
| HDF5 contiguous bandwidth | 2x compat | 1.675 / 0.8576 = 1.95x | `hdf5_cpu_reps.csv` |
| tail p99 | 214 -> 4791 us, 22x | 214.33+/-5.13 -> 4790.67+/-139.4 = 22.35x | `tail_reps.csv`, n=3 |
| tail p50 | +9%, 136 -> 149 us | 136.33 -> 148.67 = +9.05% | `tail_reps.csv` |
| command-rate arm | 2.44 -> 2.41 GiB/s, 4 -> 2 cmds | 2.439+/-0.010 -> 2.405+/-0.061, A_cmd 4 -> 2 | `amp_perf.csv` |
| pid-filter overhead | 8-15% at 37-59 kIOPS | 8.0% at 36.8, 15.0% at 58.8 kIOPS | `overhead_final.txt` |
| oracle table, all 4 rows | 100/100, 100/100, 1.6/100, 1.6/0 | identical | `oracle.csv` |
| validity gate | 63 of 4000 | identical | `compose_validity.txt` |
| write keystone | 46,956 = 14,194 + 32,762, 2.87x | identical, 14,194 x 4 KiB = 55.44 MiB | `write-keystone.md` |
| batch attribution | 2000/2000 vs 32 batch ids | identical | `batch_addr_attr.txt` |
| amplification law | predicted 1.605 = measured 1.605 | identical, zero free parameters | `findings.md` |
| kvikio bypass | 91%, 2.67x | 2000/2200 = 90.9%, 8192/3072 = 2.667 | `ucb_kvikio_fix.txt` |
| address attribution | 2000 attributed, 1 metadata | identical | `posix_addr_attr.txt` |
| HDF5 trace | 262 cuFileRead, nvfs_io=0, 256/272 | identical | `hdf5_gds_trace.csv` |
| NIXL batch sweep | 4.00x, 100% unattributed, all sizes | identical | `nixl_batchsweep.csv` |
| NIXL KV span | 1.6x (10 KiB) to 16x (256 B) | identical | `nixl_kvsweep.csv` |

Six of the seven figure CSVs in `results/figures/data/` reproduce their named source exactly. Every one
carries a `source` column, which is what made this audit tractable.

---

## Defects

### D1. Sec V-D mixes two runs in one sentence (HIGH) - FIXED 2026-08-05
> "cuts device traffic to 1.0x and **120,009 commands to 301**, taking throughput from 0.152+/-0.012 to
> 2.602+/-0.031 GiB/s"

The command counts come from `optimization.md` (2026-07-24), the superseded 2560 B run that also reports
3.2x amplification and 13.7x speedup. The throughput comes from `reps.csv` (2026-07-29), the 2048 B
re-measurement. **The current run's own counts are 120,008 -> 239** (`optimization.csv`, same campaign as
`reps.csv`). Fix: 120,009 -> **120,008**, 301 -> **239**.

### D2. `84%` mis-billed appears in no artifact (MEDIUM) - FIXED 2026-08-05
Sec V-B says "84--96% mis-billed"; Table II says ">=84%". Exhaustive grep over `results/` finds **85%**
(`posix_addr_attr.txt`, 1700/2000), **94%** (`necessity.md`, `worked-examples.md`), **95%**
(`worked-examples.md`), **96%** (`ucb_kvikio_fix.txt`). The floor is **85%**, not 84.

### D3. Sec V-F quotes the old isolated p99 (MEDIUM) - FIXED 2026-08-05
> "Segregating the classes recovers the tail to its isolated **216 us**"

216 is from `interference.md` (2026-06-25). The same sentence's 22x is computed against
`tail_reps.csv`'s **214.3 us**. Note `interference.md` is superseded in a way that matters: it reports
p50 *falling* under contention (141 -> 136), while the re-measurement reports it *rising* (136 -> 149),
and the paper already uses the newer direction. Fix: 216 -> **214**.

### D4. `p99 4080 -> 4058 us` was never re-measured (MEDIUM) [was K2] - RE-MEASURED 2026-08-05
Sec V-G's tail-neutrality evidence comes entirely from `interference.md` (2026-06-25). It sits beside
2.44 -> 2.41 GiB/s, which comes from `amp_perf.csv` and a *different workload* whose measured latencies
are 1587/1610 us (4 MiB) and 5614/5588 us (16 MiB) - nothing near 4080. Either re-measure on the
interference workload with repetitions, or attribute the two numbers to their separate runs in the text.

**Re-measured** (`scratchpad/cap_tail_reps.sh` -> `cap_tail_reps.csv`), n=3, at the SAME 4x4 MiB
contention as Sec V-F's 22x claim, so both tail numbers now share one documented regime:
cap 1280 p99 **4758+/-138 us**, cap 2048 p99 **4946+/-106 us** (p50 149.7 vs 149.0). Difference +3.9%,
t=1.87 on 4 df - not significant. The conclusion holds and is stronger: raising the cap buys no
improvement. Paper reworded from "tail-neutral" to "buys no improvement in either", since the point
estimate moved the wrong way and "neutral" would invite a reader to spot the contradiction.

### D5. `87% unattributed` is not reproducible as stated (MEDIUM) [was K1] - RE-MEASURED 2026-08-05
Sec V-D and Table II say corr_id leaves ~87% unattributed. `nixl-e2e-complete.md` presents this as a
**3-run mean (12%+/-2 correct, 87%+/-2 unattributed)**, but only one crosscheck artifact is committed
(`nixl_crosscheck.txt`) and it reports **9% correct, 90% unattributed, 1% mis-billed on class B**. The
other two runs are not in the repo.

**The 87/100 conflict itself is NOT a defect.** They are different workloads: 87% is the keystone A+B mix
(the ~12% attributed are the large class-A reads), 100% is the KV-only sweep where no class-A reads
exist. The paper's parenthetical already distinguishes them. The defect is only that 87% has no
reproducible backing. The defensible artifact-backed number is **90% on class B**.

**Re-measured** (`scratchpad/nixl_crosscheck_reps.sh` -> `nixl_crosscheck_reps.csv` plus three per-rep
files), n=3, **at SB=2048** - the size Sec V-D declares, which also closes D10 below:
corr_id unattributed **86+/-9%** (92/76/91), correct 12+/-8% (7/22/8). The paper's ~87% was right; the
*claimed precision* was not (the note said +/-2, the real spread is +/-9). Stable across reps:
35 cuFile API calls, 2200 reads, aggregate 1.057x, class A 1.000x, class B **4.000x**.

**Bonus fix.** The old keystone ran at SB=2560 and measured class B at **3.200x** while the paper claims
4.00x. At 2048 B it measures 4.000x, so Sec V-D's headline amplification is now backed by the keystone at
its own declared size instead of borrowed from the diagnosis run.

### D10. Sec V-D declared 2048 B but its keystone numbers came from a 2560 B run - FIXED 2026-08-05
Found during the re-measurement, not in the original sweep. "35 calls for 2208 device reads" and the
"2000/2000 versus 32 batch ids" parenthetical both came from SB=2560 artifacts while the section says
"(2048 B reads)". Both re-derived at 2048 B: **2200 reads / 35 calls**, and address pins **2220 of 2223**
commands to unique batch entries versus **35** batch ids. Note `nvme_setup_cmd` is not stable across reps
(2238/2394/2223), so the paper now quotes the stable 2200 read count rather than a command count.

### D6. WITHDRAWN 2026-08-06 - the figure was right, the artifact was wrong
Originally filed as "`granularity.csv` records 4096/8192 where `effective-granularity.txt` measured
4089/8179". **That diagnosis was backwards.** 4089 and 8179 are display artifacts, not measurements.

Root cause: `nixl_devbytes.py` printed device bytes as MiB to two decimal places
(`f"{devB/2**20:.2f} ..."`), and `probe_effective_granularity.sh` read that string back and multiplied
it up: `perread = devB_MiB * 1048576 / N`, N=200. The true total for the 512 B case is
200 x 4096 = 819,200 B = **0.781250 MiB**, printed as `0.78`, and 0.78 x 1048576 / 200 = 4089.4 -> 4089.
For 6144 B: 1.562500 MiB -> `1.56` -> 8178.9 -> 8179. Both reproduce exactly from the rounding alone,
and the ~0.17% deficit is just 2-dp truncation.

**Fixed at the source, not papered over.** `nixl_devbytes.py` now emits exact bytes as a fifth field
(the existing four are unchanged); `probe_effective_granularity.sh` computes per-read from it; the other
caller `run_nixl_diagnosis.sh` absorbs the new field so its positional read stays clean. Probe re-run
(`scratchpad/rerun_granularity.sh`): **effective-granularity.txt now reports 4096 and 8192**, matching
`granularity.csv` and Fig. 7. No figure regeneration was needed - the plotted values never changed.

**All seven figure CSVs now match their sources.** Lesson: a committed artifact can be less precise than
the figure derived from it, so "artifact and figure disagree" does not imply the figure is wrong.

### D7. Trace-all overhead overclaims in our favour (LOW)
Sec V-G: "Trace-all stays within **2%** at >=256 KiB". `overhead_traceall.txt` measures **2.6%** at
256 KiB (0.5% at 1 MiB, 0.1% at 4 MiB). Should be "within 3%". Separately, "20 to 30% for small
high-IOPS streams" is 21.7% (16K) and 22.9% (4K), but 64K measures **32.0%**.

### D8. Rounding inconsistency between text and figure (LOW) - FIXED 2026-08-05
`amp_perf.csv` gives 2.405 GiB/s. Sec V-G rounds it to **2.41**; Fig. 9 labels it **2.40**.

### D9. Abstract's amplification bound is loose (LOW) - FIXED 2026-08-06
"device amplification up to 4x in both the read and write directions". Measured read amplification
reaches **16x** (256 B KV, Sec V-E); measured write amplification reaches **2.87x**. 4x is true as an
upper bound for the headline cases but is neither direction's maximum. Reworded to "device
amplification of 4x on KV reads and 2.9x on unaligned writes", which keeps the NIXL headline and states
the write figure instead of letting "both directions" imply writes also reach 4x.

---

## Reproducibility posture

Every case has a committed driver script (`tools/run_nixl_*.sh`, `run_hdf5_gds.sh`,
`run_ucb_kvikio_fix.sh`, `run_write_keystone.sh`, `run_reps.sh`, `oracle_run.sh`, `overhead_bench.sh`).
Rate-based claims carry repetitions with variance; structural counts (command counts, attribution
fractions) are deterministic single runs, which is appropriate.

**Gap:** `nixl-e2e-complete.md`'s 3-run corr_id mean has no committed per-run data. That is the only
claimed multi-run result whose reps are missing.
