// gds_mixed.c — heterogeneous ASYNC GDS workload to test the *necessity* of per-op
// cross-layer attribution. Two op-classes, interleaved, issued via cuFileReadAsync:
//   A (large, 4K-aligned)   -> A_byte = 1.0   (clean)
//   B (small, unaligned)    -> A_byte > 1     (silent read-amplification)
// Each op reads a UNIQUE non-overlapping block range, so LBA->class is exact ground
// truth. Class A dominates BYTES (so the aggregate device/app ratio looks benign) while
// class B dominates OPS (so most operations are pathological). The point: only per-op
// attribution can reveal class B, and async makes corr_id collapse so only LBA works.
// usage: gds_mixed <file> <NA> <SA> <NB> <SB> <inflight>
#define _GNU_SOURCE
#include <fcntl.h>
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

#define ABASE   0L                 // class A region: [0, NA*SA)
#define BBASE   (1L<<30)           // class B region: starts at 1 GiB (well clear of A)

int main(int argc,char**argv){
  if(argc<7){ fprintf(stderr,"usage: %s file NA SA NB SB inflight\n",argv[0]); return 2; }
  const char*path=argv[1];
  long NA=atol(argv[2]); size_t SA=strtoull(argv[3],0,10);
  long NB=atol(argv[4]); size_t SB=strtoull(argv[5],0,10);
  int IF=atoi(argv[6]);
  long NT=NA+NB;
  // build interleaved op list (offset,size); unique non-overlapping ranges per class
  off_t *off=malloc(sizeof(off_t)*NT); size_t *sz=malloc(sizeof(size_t)*NT);
  // SCATTERED layout so reads don't merge at the block layer (op == device cmd), as in a
  // real embedding/KV gather: class A every 4 MiB (gap >> 1 MiB read), class B every 64 KiB
  // (gap >> 3 KiB read) with a +3072 misalign so each B read spans two 4K blocks.
  long ia=0, ib=0, k=0;
  while(k<NT){
    long ratio = NA? (NB/ (NA?NA:1)) : NT; if(ratio<1) ratio=1;
    for(long r=0; r<ratio && ib<NB; r++){
      off[k]=BBASE + (off_t)ib*65536 + 3072;     // 64 KiB stride => non-adjacent, no merge
      sz[k]=SB; k++; ib++;
    }
    if(ia<NA){ off[k]=ABASE + (off_t)ia*(4L<<20); sz[k]=SA; k++; ia++; }  // 4 MiB stride
    if(ia>=NA && ib>=NB) break;
  }
  NT=k;
  size_t maxsz = SA>SB?SA:SB;

  CK(cuFileDriverOpen());
  int fd=open(path,O_RDONLY|O_DIRECT); if(fd<0){perror("open");return 1;}
  CUfileDescr_t d; memset(&d,0,sizeof(d)); d.handle.fd=fd; d.type=CU_FILE_HANDLE_TYPE_OPAQUE_FD;
  CUfileHandle_t fh; CK(cuFileHandleRegister(&fh,&d));
  cudaStream_t st; CC(cudaStreamCreate(&st));
  size_t bufsz=(size_t)IF*maxsz; void*dptr; CC(cudaMalloc(&dptr,bufsz)); CK(cuFileBufRegister(dptr,bufsz,0));
  cuFileStreamRegister((CUstream)st,0);
  size_t *psz=calloc(IF,sizeof(size_t)); off_t *pfo=calloc(IF,sizeof(off_t)),*ppo=calloc(IF,sizeof(off_t));
  ssize_t *pbr=calloc(IF,sizeof(ssize_t));
  for(long base=0; base<NT; base+=IF){
    int n=(NT-base<IF)?(int)(NT-base):IF;
    for(int j=0;j<n;j++){ psz[j]=sz[base+j]; pfo[j]=off[base+j]; ppo[j]=(off_t)j*maxsz;
      CK(cuFileReadAsync(fh,dptr,&psz[j],&pfo[j],&ppo[j],&pbr[j],(CUstream)st)); }
    CC(cudaStreamSynchronize(st));
  }
  printf("MIXED NA=%ld SA=%zu NB=%ld SB=%zu inflight=%d total_ops=%ld\n",NA,SA,NB,SB,IF,NT);
  cuFileHandleDeregister(fh); close(fd); cuFileDriverClose();
  return 0;
}
