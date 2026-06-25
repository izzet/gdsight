// gds_interfere.c — cross-op interference probe. n_small threads issue small GDS reads
// (latency-sensitive, class B region) while n_large threads issue large GDS reads
// (class A region) concurrently. Synchronous cuFileRead => the traced cuFileRead duration
// IS each op's true latency. Compare small-op latency with n_large=0 (alone) vs n_large>0
// (contended) to measure device-queue head-of-line blocking, attributed per-op cross-layer.
// usage: gds_interfere <file> <n_small> <n_large> <small_sz> <large_sz> <small_cnt> <large_cnt>
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <cufile.h>
#include <cuda_runtime.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CK(x) do{ CUfileError_t _e=(x); if(_e.err!=CU_FILE_SUCCESS){ \
  fprintf(stderr,"cufile err %d at %d\n",_e.err,__LINE__); exit(1);} }while(0)
#define CC(x) do{ cudaError_t _e=(x); if(_e!=cudaSuccess){ \
  fprintf(stderr,"cuda err %s at %d\n",cudaGetErrorString(_e),__LINE__); exit(1);} }while(0)

#define ABASE 0L                  // large reads (class A)
#define BBASE (1L<<30)            // small reads (class B)

static CUfileHandle_t FH;
typedef struct { int tid; size_t sz; long cnt; off_t base; off_t stride; off_t misalign; long nslots; } targ_t;
static void* worker(void* a){
  targ_t* t=(targ_t*)a; void* buf; CC(cudaMalloc(&buf,t->sz)); CK(cuFileBufRegister(buf,t->sz,0));
  unsigned seed=t->tid*2654435761u; long ok=0;
  for(long i=0;i<t->cnt;i++){
    seed = seed*1103515245u + 12345u;                 // cheap PRNG for scattered offsets
    long slot = (seed >> 8) % t->nslots;              // bounded to the file region
    off_t off = t->base + slot*t->stride + t->misalign;
    ssize_t r = cuFileRead(FH, buf, t->sz, off, 0);
    if(r==(ssize_t)t->sz) ok++;
    else fprintf(stderr,"read tid=%d i=%ld off=%ld ret=%zd (want %zu)\n",t->tid,i,(long)off,r,t->sz);
  }
  cuFileBufDeregister(buf); cudaFree(buf); return NULL;
}

int main(int argc,char**argv){
  if(argc<8){ fprintf(stderr,"usage: %s file n_small n_large small_sz large_sz small_cnt large_cnt\n",argv[0]); return 2; }
  const char*path=argv[1];
  int ns=atoi(argv[2]), nl=atoi(argv[3]);
  size_t ssz=strtoull(argv[4],0,10), lsz=strtoull(argv[5],0,10);
  long scnt=strtol(argv[6],0,10), lcnt=strtol(argv[7],0,10);
  CK(cuFileDriverOpen());
  int fd=open(path,O_RDONLY|O_DIRECT); if(fd<0){perror("open");return 1;}
  CUfileDescr_t d; memset(&d,0,sizeof(d)); d.handle.fd=fd; d.type=CU_FILE_HANDLE_TYPE_OPAQUE_FD;
  CK(cuFileHandleRegister(&FH,&d));
  // bound slots to the file regions (file ~2 GiB): A=[0,768MiB) large, B=[1GiB,1.9GiB) small
  long lslots = (768L<<20)/((off_t)lsz*2); if(lslots<1) lslots=1;
  long sslots = (900L<<20)/65536;
  int N=ns+nl; pthread_t th[N]; targ_t ta[N];
  for(int i=0;i<ns;i++) ta[i]=(targ_t){i, ssz, scnt, BBASE, 65536, 3072, sslots};   // small, scattered, unaligned
  for(int i=0;i<nl;i++) ta[ns+i]=(targ_t){ns+i, lsz, lcnt, ABASE, (off_t)lsz*2, 0, lslots}; // large, aligned
  for(int i=0;i<N;i++) pthread_create(&th[i],0,worker,&ta[i]);
  for(int i=0;i<N;i++) pthread_join(th[i],0);
  printf("INTERFERE n_small=%d n_large=%d ssz=%zu lsz=%zu scnt=%ld lcnt=%ld\n",ns,nl,ssz,lsz,scnt,lcnt);
  cuFileHandleDeregister(FH); close(fd); cuFileDriverClose();
  return 0;
}
