#!/usr/bin/env python
"""Drive DFAnalyzer (datacrumbs/stack preset) on a GDS-Trace cross-layer trace and surface the
per-op cuFile -> NVMe hierarchy (the amplification). Usage: dfa_drive.py <trace_dir> [tmp_dir]"""
import sys
import pandas as pd
from dask.distributed import LocalCluster
from dftracer.analyzer import init_with_hydra


def main():
    trace_path = sys.argv[1]
    tmp = sys.argv[2] if len(sys.argv) > 2 else "/tmp/dfa_run"
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)

    cluster = LocalCluster(processes=False, protocol="tcp")
    overrides = [
        "analyzer=datacrumbs",
        "analyzer/preset=stack",
        "analyzer.checkpoint=False",
        "cluster=external",
        "cluster.restart_on_connect=False",
        f"cluster.scheduler_address={cluster.scheduler_address}",
        f"hydra.run.dir={tmp}",
        f"hydra.runtime.output_dir={tmp}",
        f"trace_path={trace_path}",
        "view_types=[proc_name,func_name]",
    ]
    dfa = init_with_hydra(hydra_overrides=overrides)
    result = dfa.analyze_trace()
    print("\n==== LAYERS:", result.layers, " VIEWS:", list(result.flat_views.keys()))

    # per-event stack hierarchy (set by DataCrumbsAnalyzer.postread_trace for preset=stack)
    st = getattr(dfa.analyzer, "_stack_traces", None)
    sdf = (st.compute() if hasattr(st, "compute") else st).reset_index(drop=True)

    # DFAnalyzer (compute_self_time, datacrumbs.py) already attached self_time/child_time per event:
    #   self_time  = time NOT spent in nested layers  (this layer's own cost)
    #   child_time = time spent in nested layers       (deeper-layer cost)
    has_self = "self_time" in sdf.columns and "child_time" in sdf.columns

    print("\n==== per-LAYER (cat) summary ====")
    lagg = {"events": ("func_name", "size"), "total_time_s": ("time", "sum")}
    if has_self:
        lagg["self_time_s"] = ("self_time", "sum")
        lagg["child_time_s"] = ("child_time", "sum")
    print(sdf.groupby("cat").agg(**lagg).to_string())

    print("\n==== per-FUNCTION summary ====")
    fagg = {"events": ("func_name", "size"), "time_s": ("time", "sum"),
            "roots": ("depth", lambda d: int((d == 0).sum())), "bytes": ("size", "sum")}
    if has_self:
        fagg["self_time_s"] = ("self_time", "sum")
        fagg["child_time_s"] = ("child_time", "sum")
    print(sdf.groupby("func_name").agg(**fagg).to_string())

    # ---- cross-layer TIMING: where does each cuFileRead's latency go? (DFAnalyzer self/child) ----
    if has_self:
        cf_t = sdf[sdf["func_name"] == "cuFileRead"]
        nv_t = sdf[sdf["func_name"] == "nvfs_io"]
        print("\n==== cross-layer TIMING (DFAnalyzer self_time/child_time) ====")
        if len(cf_t):
            tot = cf_t["time"].sum()
            cf_self = cf_t["self_time"].sum()    # cuFile/userspace overhead (own frame) - robust
            cf_child = cf_t["child_time"].sum()  # time nested below cuFileRead (same-thread) - robust
            print(f"cuFileRead total wall-time   : {tot:.4f} s over {len(cf_t)} ops "
                  f"({tot/len(cf_t)*1e3:.0f} us/op)")
            print(f"  -> cuFile/userspace (self) : {cf_self:.4f} s ({100*cf_self/tot:.1f}%)")
            print(f"  -> below cuFile  (child)   : {cf_child:.4f} s ({100*cf_child/tot:.1f}%)")
            if len(nv_t):
                nv_self = nv_t["self_time"].sum()
                # nvfs_io durations are additive only when ops don't overlap on a thread (synchronous
                # cuFile). cuFile's INTERNAL worker threads pipeline them -> sum is meaningless + the
                # nvfs_io/NVMe land off the caller thread, so per-tid attribution splits.
                if nv_self > tot * 1.5:
                    print(f"  [note] Sum nvfs_io self_time = {nv_self:.1f} s >> cuFileRead wall "
                          f"({tot:.2f} s) => cuFile used INTERNAL worker threads: nvfs_io ops overlap "
                          f"(durations not additive) and attribution splits across threads.")
                else:
                    print(f"  nvfs_io self_time          : {nv_self:.4f} s (nvidia-fs+device, synchronous)")
        else:
            print("no cuFileRead events")
        # COMPAT detection: cuFileRead present but no nvfs_io child => POSIX fallback
        if len(cf_t) and not len(nv_t):
            print(">>> cuFileRead present but ZERO nvfs_io => COMPAT/POSIX fallback (not real GDS)")
        # overlap/concurrency via DFAnalyzer's own wall-time (get_job_time = max tend - min tstart).
        # Use time_start/time_end for both numerator and denominator => unit-independent overlap factor.
        if len(cf_t) and {"time_start", "time_end"}.issubset(cf_t.columns):
            dur = (cf_t["time_end"] - cf_t["time_start"])
            span = cf_t["time_end"].max() - cf_t["time_start"].min()
            if span > 0:
                print(f"\n-- overlap / concurrency (cuFileRead) --")
                print(f"wall span (get_job_time)       : {span/1e6:.4f} s")
                print(f"sum(per-op dur)                : {dur.sum()/1e6:.4f} s")
                print(f"avg in-flight (overlap factor) : {dur.sum()/span:.2f}x  "
                      f"(>1 => worker threads pipelining/concurrency)")

    # ---- cross-layer attribution: NVMe -> the cuFileRead that issued it ----
    nvme = sdf[sdf["func_name"] == "nvme_setup_cmd"]
    cr = sdf[sdf["func_name"] == "cuFileRead"]
    print("\n==== cross-layer attribution: NVMe -> cuFileRead ====")
    # robust: by correlation id (carried through cuFile -> nvidia-fs -> NVMe by the tracer)
    if "corr_id" in sdf.columns and len(cr):
        cr_ids = set(cr["corr_id"].dropna().astype("int64").tolist())
        nvme_corr = nvme["corr_id"].dropna().astype("int64")
        matched = int(nvme_corr.isin(cr_ids).sum())
        print(f"by CORR_ID (robust)        : {matched}/{len(nvme)} NVMe -> a cuFileRead "
              f"({100*matched/max(1,len(nvme)):.1f}%)")
        per_op = nvme_corr[nvme_corr.isin(cr_ids)].value_counts()
        if len(per_op):
            print(f"   NVMe cmds per cuFileRead: mean={per_op.mean():.1f} min={int(per_op.min())} "
                  f"max={int(per_op.max())}")
    # legacy: per-tid time-containment (shows why corr_id was needed for worker-thread/async I/O)
    if "root_id" in sdf.columns:
        id2func = sdf.set_index("event_id")["func_name"].to_dict()
        nvme_rf = nvme["root_id"].map(id2func)
        to_cr = int((nvme_rf == "cuFileRead").sum())
        rest = nvme_rf[nvme_rf != "cuFileRead"].value_counts().to_dict()
        print(f"by TIME-CONTAINMENT (legacy): {to_cr}/{len(nvme)} NVMe -> cuFileRead "
              f"({100*to_cr/max(1,len(nvme)):.1f}%); rest -> {rest}")
    if len(cr):
        amp = len(nvme) / len(cr)
        print(f"\n==== AMPLIFICATION ====\ncuFileRead ops = {len(cr)} | nvme_setup_cmd = {len(nvme)} "
              f"| device-cmd amplification = {amp:.2f}x")
        print(f"cuFileRead bytes = {cr['size'].sum()/2**20:.0f} MiB | "
              f"NVMe bytes = {nvme['size'].sum()/2**20:.0f} MiB")
    print("\nDONE")
    cluster.close()


if __name__ == "__main__":
    main()
