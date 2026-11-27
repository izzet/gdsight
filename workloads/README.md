# workloads/

Workloads that read (and for the write case, write) through GPUDirect Storage, driven by the scripts in
[`../tools/`](../tools/README.md). C workloads call cuFile directly; Python workloads use the real
libraries (KvikIO, NIXL, DALI, cuCIM, cuDF, ...) from `/opt/gds-venv` unless noted.

## Controlled cuFile workloads (C)

| file | what it does |
|---|---|
| `gds_oracle.c` | each operation reads a disjoint slot, so the LBA names the operation: ground truth for validation |
| `gds_mixed.c` | two interleaved classes via `cuFileReadAsync`: large aligned and small unaligned |
| `gds_align_probe.c` | exact control of file-offset misalignment, size and buffer alignment |
| `gds_interfere.c`, `gds_interfere_lat.c` | small latency-sensitive reads against concurrent large reads; the `_lat` variant times them with no tracer |
| `gds_replay.c` | replays a recorded read geometry through `cuFileRead` |
| `hdf5_gds.c` | HDF5 reads into GPU memory over the GDS VFD, chunk cache on or off, chunked or contiguous |

## Library workloads (Python)

| file | library | used for |
|---|---|---|
| `rag_mixed.py` | KvikIO | the KvikIO retrieval-mix case (small reads below the GDS threshold) |
| `kvikio_threshold.py`, `kvikio_size_sweep.py` | KvikIO | the GDS-threshold bypass and the size at which it starts |
| `kvikio_bimodal.py`, `kvikio_ragged.py`, `kvikio_npy_read.py`, `kvikio_alignment_amp.py` | KvikIO | mixed sizes, variable records, real `.npy` files, alignment-driven amplification |
| `kvikio_write_threshold.py` | KvikIO | whether the threshold bypass applies to writes |
| `async_reader.py`, `xfile_read.py` | KvikIO | async reads on a CUDA stream; two files in one process |
| `concurrent_reader.py` | cufile-python, PyTorch | many threads with reads in flight at once |
| `nixl_gds_kv.py`, `nixl_gds_read.py`, `nixl_gds_probe.py` | NIXL (`~/nixl-venv`) | the NIXL KV-cache case and a minimal P2P check |
| `dali_npy_read.py` | DALI | GPU numpy reader over the same `.npy` data |
| `cucim_gds_tiles.py`, `cucim_coalesce_test.py`, `read_region_test.py` | cuCIM | whole-slide image tiles and tile coalescing |
| `cudf_read_parquet.py` | cuDF | Parquet ingest through cuFile |
| `fastsafetensors_load.py`, `deepnvme_gds_load.py` | fastsafetensors, DeepSpeed DeepNVMe | model-weight loading |
| `lmcache_gds_kv.py`, `embedding_gather.py`, `espn_retrieval.py`, `mixed_retrieval.py` | cufile-python | KV offload and embedding or retrieval gathers |
| `managed_buffer_test.py` | CUDA driver via ctypes | whether cuFile bounces managed-memory buffers |
