/* gds_trace_preload.c — per-op cross-layer GDS tracer (standalone prototype).
 *
 * The silent pathology: a vendor reader (kvikio/DALI) services some reads via cuFile (GDS DMA) and
 * others via libc POSIX (pread/read) — bypassing GDS — and gds_stats (cuFile-only) can't see the
 * POSIX ones. This LD_PRELOAD interposer hooks BOTH layers and logs every read on the dataset, so
 * each operation is attributed GDS vs POSIX-bypass with file/offset/size = the per-op cross-layer
 * view GDSight proposes (to be folded into DFTracer's brahma cuFile module; see external/dftracer).
 *
 *   cc -shared -fPIC -O2 -o libgdstrace.so gds_trace_preload.c -ldl -lpthread
 *   GDS_TRACE_OUT=trace.tsv GDS_TRACE_FILTER=gdstrace-smoke LD_PRELOAD=./libgdstrace.so <app>
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/types.h>

static FILE *out;
static const char *filt;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static unsigned long n_gds, n_posix;

__attribute__((constructor)) static void gt_init(void) {
    const char *o = getenv("GDS_TRACE_OUT");
    out = o ? fopen(o, "w") : stderr;
    filt = getenv("GDS_TRACE_FILTER"); if (!filt) filt = "gdstrace-smoke";
    if (out) { fprintf(out, "# layer\tapi\tfile\toffset\tsize\tret\n"); fflush(out); }
}
__attribute__((destructor)) static void gt_fini(void) {
    if (out) { fprintf(out, "# SUMMARY gds_reads=%lu posix_reads=%lu\n", n_gds, n_posix); fflush(out); }
}
static void fdpath(int fd, char *b, size_t n) {
    char p[64]; snprintf(p, sizeof p, "/proc/self/fd/%d", fd);
    ssize_t r = readlink(p, b, n - 1); b[r > 0 ? r : 0] = 0;
}
static int match(const char *p) { return filt[0] == 0 || (p && p[0] && strstr(p, filt)); }
static void logline(const char *layer, const char *api, const char *file, long long off, size_t sz, ssize_t ret) {
    pthread_mutex_lock(&mu);
    fprintf(out, "%s\t%s\t%s\t%lld\t%zu\t%zd\n", layer, api, file, off, sz, ret);
    fflush(out);
    pthread_mutex_unlock(&mu);
}

/* ---- GDS path: cuFile ---- */
typedef ssize_t (*cuFileRead_t)(void *, void *, size_t, off_t, off_t);
ssize_t cuFileRead(void *fh, void *dev, size_t size, off_t foff, off_t doff) {
    static cuFileRead_t real; if (!real) real = (cuFileRead_t)dlsym(RTLD_NEXT, "cuFileRead");
    ssize_t r = real(fh, dev, size, foff, doff);
    __sync_fetch_and_add(&n_gds, 1);
    logline("GDS", "cuFileRead", "<gds-handle>", (long long)foff, size, r);
    return r;
}

/* ---- POSIX path (the silent bypass) ---- */
typedef ssize_t (*pread64_t)(int, void *, size_t, off_t);
ssize_t pread64(int fd, void *buf, size_t cnt, off_t off) {
    static pread64_t real; if (!real) real = (pread64_t)dlsym(RTLD_NEXT, "pread64");
    ssize_t r = real(fd, buf, cnt, off);
    char p[512]; fdpath(fd, p, sizeof p);
    if (match(p)) { __sync_fetch_and_add(&n_posix, 1); logline("POSIX", "pread64", p, (long long)off, cnt, r); }
    return r;
}
typedef ssize_t (*pread_t)(int, void *, size_t, off_t);
ssize_t pread(int fd, void *buf, size_t cnt, off_t off) {
    static pread_t real; if (!real) real = (pread_t)dlsym(RTLD_NEXT, "pread");
    ssize_t r = real(fd, buf, cnt, off);
    char p[512]; fdpath(fd, p, sizeof p);
    if (match(p)) { __sync_fetch_and_add(&n_posix, 1); logline("POSIX", "pread", p, (long long)off, cnt, r); }
    return r;
}
typedef ssize_t (*read_t)(int, void *, size_t);
ssize_t read(int fd, void *buf, size_t cnt) {
    static read_t real; if (!real) real = (read_t)dlsym(RTLD_NEXT, "read");
    ssize_t r = real(fd, buf, cnt);
    char p[512]; fdpath(fd, p, sizeof p);
    if (match(p)) { __sync_fetch_and_add(&n_posix, 1); logline("POSIX", "read", p, -1, cnt, r); }
    return r;
}
