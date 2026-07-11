/* hdf5_gds.c — drive HDF5 reads into GPU memory over the GPUDirect-Storage VFD (nv-legate/vfd-gds),
 * to expose per-op cross-layer GDS behavior under GDSight.
 *
 * The keystone finding this reproduces: HDF5's raw-data CHUNK CACHE silently defeats GDS. A chunked
 * dataset (the default layout for compressed/partial-access scientific & ML data) read with the chunk
 * cache ON routes every chunk through host memory (read chunk -> host cache -> copy to GPU), so cuFile
 * falls back to compat (CPU bounce) — no P2P — even though the app issues normal cuFileRead calls.
 * Disabling the chunk cache (H5Pset_chunk_cache(dapl, 0,0,0)) restores true file->GPU DMA.
 *
 * Reads only (GDS writes are riskier on this stack). The file is CREATED host-side with the default
 * (SEC2) VFD; only the READ uses the GDS VFD. Prints requested bytes + wall time; the harness reads
 * /proc/driver/nvidia-fs/stats around the read to see whether GDS actually engaged (nvfs delta).
 *
 * Build: see chameleon/build_hdf5_gds.sh for HDF5 + vfd-gds; compile line in tools/run_hdf5_gds.sh.
 *
 * Usage:
 *   hdf5_gds create <file> <contig|chunk> <size_MiB> <chunk_KiB>
 *   hdf5_gds read   <file> <on|off>          # chunk cache on (default) vs off (the fix)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <hdf5.h>
#include <cuda_runtime.h>
#include "H5FDgds.h"

#define DSET "data"
#define CK(x)   do{ if((x)<0){ fprintf(stderr,"HDF5 error at %s:%d\n",__FILE__,__LINE__); exit(2);} }while(0)
#define CUCK(x) do{ cudaError_t e=(x); if(e){ fprintf(stderr,"CUDA error %s at %s:%d\n",cudaGetErrorString(e),__FILE__,__LINE__); exit(3);} }while(0)

static double now_s(void){ struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec + t.tv_nsec/1e9; }

static int do_create(const char *file, const char *layout, long size_mib, long chunk_kib){
    hsize_t n = (hsize_t)size_mib * 1024 * 1024;           /* bytes, as a 1-D uchar array */
    hid_t fcpl = H5P_DEFAULT;
    hid_t fapl = H5Pcreate(H5P_FILE_ACCESS);               /* default (SEC2) VFD — host-side write */
    hid_t fid  = H5Fcreate(file, H5F_ACC_TRUNC, fcpl, fapl); CK(fid);
    hid_t space = H5Screate_simple(1, &n, NULL); CK(space);
    hid_t dcpl = H5Pcreate(H5P_DATASET_CREATE); CK(dcpl);
    if (strcmp(layout,"chunk")==0){
        hsize_t ck = (hsize_t)chunk_kib*1024; if(ck>n) ck=n;
        CK(H5Pset_chunk(dcpl, 1, &ck));
    } else {
        CK(H5Pset_layout(dcpl, H5D_CONTIGUOUS));
    }
    hid_t dset = H5Dcreate2(fid, DSET, H5T_NATIVE_UCHAR, space, H5P_DEFAULT, dcpl, H5P_DEFAULT); CK(dset);
    /* fill host buffer with a recognizable pattern and write */
    unsigned char *buf = malloc(n); if(!buf){ perror("malloc"); exit(1);}
    for (hsize_t i=0;i<n;i++) buf[i] = (unsigned char)(i & 0xff);
    CK(H5Dwrite(dset, H5T_NATIVE_UCHAR, H5S_ALL, H5S_ALL, H5P_DEFAULT, buf));
    free(buf);
    H5Dclose(dset); H5Pclose(dcpl); H5Sclose(space); H5Pclose(fapl); H5Fclose(fid);
    printf("created %s layout=%s size=%ldMiB chunk=%ldKiB\n", file, layout, size_mib, chunk_kib);
    return 0;
}

static int do_read(const char *file, int cache_on, long rdcc_bytes){
    /* open with the GDS VFD */
    hid_t fapl = H5Pcreate(H5P_FILE_ACCESS); CK(fapl);
    /* H5Pset_fapl_gds(fapl, alignment, block_size, cbuf_size) — 4K alignment, 16MiB copy buffer */
    CK(H5Pset_fapl_gds(fapl, 4096, 4096, (size_t)16*1024*1024));
    hid_t fid = H5Fopen(file, H5F_ACC_RDONLY, fapl); CK(fid);

    /* dataset access plist: set the raw-data chunk cache. rdcc_bytes>=0 sweeps the cache SIZE at a
     * fixed chunk (the causal test: the P2P transition should move with the cache size, proving the
     * chunk cache is the cause); otherwise on=HDF5 default cache, off=disabled. */
    hid_t dapl = H5Pcreate(H5P_DATASET_ACCESS); CK(dapl);
    if (rdcc_bytes >= 0)  CK(H5Pset_chunk_cache(dapl, 12421, (size_t)rdcc_bytes, 0.75));
    else if (!cache_on)   CK(H5Pset_chunk_cache(dapl, 0, 0, 0.0));   /* disable -> direct file->GPU */
    hid_t dset = H5Dopen2(fid, DSET, dapl); CK(dset);

    hid_t space = H5Dget_space(dset);
    hsize_t n; H5Sget_simple_extent_dims(space, &n, NULL);

    /* GPU destination buffer */
    void *dbuf=NULL; CUCK(cudaMalloc(&dbuf, n)); CUCK(cudaMemset(dbuf,0,n));

    double t0 = now_s();
    CK(H5Dread(dset, H5T_NATIVE_UCHAR, H5S_ALL, H5S_ALL, H5P_DEFAULT, dbuf));
    CUCK(cudaDeviceSynchronize());
    double dt = now_s()-t0;

    double mib = (double)n/(1024*1024);
    if (rdcc_bytes >= 0)
        printf("read %s rdcc=%ldKiB bytes=%.1fMiB time=%.4fs BW=%.3f GiB/s\n",
               file, rdcc_bytes/1024, mib, dt, mib/1024.0/dt);
    else
        printf("read %s cache=%s bytes=%.1fMiB time=%.4fs BW=%.3f GiB/s\n",
               file, cache_on?"on":"off", mib, dt, mib/1024.0/dt);

    cudaFree(dbuf);
    H5Dclose(dset); H5Sclose(space); H5Pclose(dapl); H5Pclose(fapl); H5Fclose(fid);
    return 0;
}

int main(int argc, char **argv){
    if (argc>=6 && strcmp(argv[1],"create")==0)
        return do_create(argv[2], argv[3], atol(argv[4]), atol(argv[5]));
    if (argc>=4 && strcmp(argv[1],"read")==0)
        return do_read(argv[2], strcmp(argv[3],"on")==0, argc>=5 ? atol(argv[4]) : -1);
    fprintf(stderr,
        "usage:\n"
        "  %s create <file> <contig|chunk> <size_MiB> <chunk_KiB>\n"
        "  %s read   <file> <on|off> [rdcc_nbytes]   # rdcc_nbytes sweeps the cache size at fixed chunk\n",
        argv[0], argv[0]);
    return 1;
}
