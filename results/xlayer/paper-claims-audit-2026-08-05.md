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

### D1. Sec V-D mixes two runs in one sentence (HIGH)
> "cuts device traffic to 1.0x and **120,009 commands to 301**, taking throughput from 0.152+/-0.012 to
> 2.602+/-0.031 GiB/s"

The command counts come from `optimization.md` (2026-07-24), the superseded 2560 B run that also reports
3.2x amplification and 13.7x speedup. The throughput comes from `reps.csv` (2026-07-29), the 2048 B
re-measurement. **The current run's own counts are 120,008 -> 239** (`optimization.csv`, same campaign as
`reps.csv`). Fix: 120,009 -> **120,008**, 301 -> **239**.

### D2. `84%` mis-billed appears in no artifact (MEDIUM)
Sec V-B says "84--96% mis-billed"; Table II says ">=84%". Exhaustive grep over `results/` finds **85%**
(`posix_addr_attr.txt`, 1700/2000), **94%** (`necessity.md`, `worked-examples.md`), **95%**
(`worked-examples.md`), **96%** (`ucb_kvikio_fix.txt`). The floor is **85%**, not 84.

### D3. Sec V-F quotes the old isolated p99 (MEDIUM)
> "Segregating the classes recovers the tail to its isolated **216 us**"

216 is from `interference.md` (2026-06-25). The same sentence's 22x is computed against
`tail_reps.csv`'s **214.3 us**. Note `interference.md` is superseded in a way that matters: it reports
p50 *falling* under contention (141 -> 136), while the re-measurement reports it *rising* (136 -> 149),
and the paper already uses the newer direction. Fix: 216 -> **214**.

### D4. `p99 4080 -> 4058 us` was never re-measured (MEDIUM) [was K2]
Sec V-G's tail-neutrality evidence comes entirely from `interference.md` (2026-06-25). It sits beside
2.44 -> 2.41 GiB/s, which comes from `amp_perf.csv` and a *different workload* whose measured latencies
are 1587/1610 us (4 MiB) and 5614/5588 us (16 MiB) - nothing near 4080. Either re-measure on the
interference workload with repetitions, or attribute the two numbers to their separate runs in the text.

### D5. `87% unattributed` is not reproducible as stated (MEDIUM) [was K1]
Sec V-D and Table II say corr_id leaves ~87% unattributed. `nixl-e2e-complete.md` presents this as a
**3-run mean (12%+/-2 correct, 87%+/-2 unattributed)**, but only one crosscheck artifact is committed
(`nixl_crosscheck.txt`) and it reports **9% correct, 90% unattributed, 1% mis-billed on class B**. The
other two runs are not in the repo.

**The 87/100 conflict itself is NOT a defect.** They are different workloads: 87% is the keystone A+B mix
(the ~12% attributed are the large class-A reads), 100% is the KV-only sweep where no class-A reads
exist. The paper's parenthetical already distinguishes them. The defect is only that 87% has no
reproducible backing. The defensible artifact-backed number is **90% on class B**.

### D6. `granularity.csv` idealizes its source (MEDIUM)
`effective-granularity.txt` measures **4089 B** device bytes per read at 512/2048/2560/4096 B requested,
and **8179 B** at 6144 B. `granularity.csv` records **4096** and **8192** - the theoretical grid, not the
measurement. Fig. 7's caption says "Points are **measured** per-op device bytes". The gap is 0.17%, so no
conclusion changes, but the figure is not reproducing what the artifact recorded. This is the only figure
CSV of the seven that does not match its source.

### D7. Trace-all overhead overclaims in our favour (LOW)
Sec V-G: "Trace-all stays within **2%** at >=256 KiB". `overhead_traceall.txt` measures **2.6%** at
256 KiB (0.5% at 1 MiB, 0.1% at 4 MiB). Should be "within 3%". Separately, "20 to 30% for small
high-IOPS streams" is 21.7% (16K) and 22.9% (4K), but 64K measures **32.0%**.

### D8. Rounding inconsistency between text and figure (LOW)
`amp_perf.csv` gives 2.405 GiB/s. Sec V-G rounds it to **2.41**; Fig. 9 labels it **2.40**.

### D9. Abstract's amplification bound is loose (LOW)
"device amplification up to 4x in both the read and write directions". Measured read amplification
reaches **16x** (256 B KV, Sec V-E); measured write amplification reaches **2.87x**. 4x is true as an
upper bound for the headline cases but is neither direction's maximum.

---

## Reproducibility posture

Every case has a committed driver script (`tools/run_nixl_*.sh`, `run_hdf5_gds.sh`,
`run_ucb_kvikio_fix.sh`, `run_write_keystone.sh`, `run_reps.sh`, `oracle_run.sh`, `overhead_bench.sh`).
Rate-based claims carry repetitions with variance; structural counts (command counts, attribution
fractions) are deterministic single runs, which is appropriate.

**Gap:** `nixl-e2e-complete.md`'s 3-run corr_id mean has no committed per-run data. That is the only
claimed multi-run result whose reps are missing.
