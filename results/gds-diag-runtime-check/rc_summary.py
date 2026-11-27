import glob
import json

for f in sorted(glob.glob("rc_*.json")):
    try:
        d = json.load(open(f))
    except Exception as e:  # noqa: BLE001
        print(f, "BAD JSON", e)
        continue
    o = d["observation"]
    kc = o["kernel_counts"] or {}
    routes = d["platform_routes"]
    diag = d["sections"]["Diagnosis"][0]
    dev = o["device_read_mib"]
    print("{:<28} exit={} nvfs={:<4} devMiB={:<7} p2pdma={:<5} nvfsmap={:<5} pgcache={:<5} routes={}/{} -> {} {}".format(
        f[3:-5], o["exit_code"], str(o["nvfs_reads"]), str(round(dev, 1) if dev is not None else None),
        str(kc.get("pci_p2pdma_map_segment")), str(kc.get("nvfs_get_p2p_dma_mapping")),
        str(kc.get("filemap_read")), routes["native"], routes["p2pdma"], diag["status"], diag["check"]))
