// gds_oracle.c — generate cuFile reads over UNIQUE non-overlapping slots so the device
// LBA gives an INDEPENDENT per-op ground truth (slot = file_offset/size) to score corr_id
// and LBA attribution against (C1 validation). Modes:
//   threads <T> : T pthreads each issuing synchronous cuFileRead over its slot partition
//   batch   <B> : cuFileBatchIOSubmit batches of B reads (decoupled/async regime)
// Every op reads a distinct slot exactly once -> nvme LBA -> slot is unambiguous truth.
// usage: gds_oracle <file> <threads|batch> <T-or-B> <size> <nreads>
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

static CUfileHandle_t FH; static size_t SZ; static long NREADS; static int T;

typedef struct { int tid; void* buf; } targ_t;
static void* worker(void* a){
  targ_t* t=(targ_t*)a;
  for(long i=t->tid; i<NREADS; i+=T){
    ssize_t r=cuFileRead(FH, t->buf, SZ, (off_t)i*SZ, 0);
    if(r<=0) fprintf(stderr,"read slot %ld ret=%zd\n",i,r);
  }
  return NULL;
}

int main(int argc,char**argv){
  if(argc<6){ fprintf(stderr,"usage: %s file threads|batch N size nreads\n",argv[0]); return 2; }
  const char*path=argv[1]; const char*mode=argv[2]; int N=atoi(argv[3]);
  SZ=strtoull(argv[4],0,10); NREADS=strtol(argv[5],0,10);
  CK(cuFileDriverOpen());
  int fd=open(path,O_RDONLY|O_DIRECT); if(fd<0){perror("open");return 1;}
  CUfileDescr_t d; memset(&d,0,sizeof(d)); d.handle.fd=fd; d.type=CU_FILE_HANDLE_TYPE_OPAQUE_FD;
  CK(cuFileHandleRegister(&FH,&d));

  if(!strcmp(mode,"threads")){
    T=N; pthread_t th[T]; targ_t ta[T];
    for(int t=0;t<T;t++){ void*b; CC(cudaMalloc(&b,SZ)); CK(cuFileBufRegister(b,SZ,0)); ta[t]=(targ_t){t,b}; }
    for(int t=0;t<T;t++) pthread_create(&th[t],0,worker,&ta[t]);
    for(int t=0;t<T;t++) pthread_join(th[t],0);
    printf("ORACLE threads T=%d nreads=%ld size=%zu\n",T,NREADS,SZ);
  } else if(!strcmp(mode,"async")){
    int IF=N;                                   // in-flight async ops per wave
    long HOT=(argc>=7)?strtol(argv[6],0,10):NREADS;  // distinct slots; <NREADS => overlap
    cudaStream_t st; CC(cudaStreamCreate(&st));
    size_t bufsz=(size_t)IF*SZ; void*dptr; CC(cudaMalloc(&dptr,bufsz)); CK(cuFileBufRegister(dptr,bufsz,0));
    cuFileStreamRegister((CUstream)st,0);       // optional; ignore failure
    size_t *sz=calloc(IF,sizeof(size_t)); off_t *fo=calloc(IF,sizeof(off_t)),*po=calloc(IF,sizeof(off_t));
    ssize_t *br=calloc(IF,sizeof(ssize_t));
    for(long base=0; base<NREADS; base+=IF){
      int n=(NREADS-base<IF)?(int)(NREADS-base):IF;
      for(int j=0;j<n;j++){ sz[j]=SZ; fo[j]=(off_t)((base+j)%HOT)*SZ; po[j]=(off_t)j*SZ;
        CK(cuFileReadAsync(FH,dptr,&sz[j],&fo[j],&po[j],&br[j],(CUstream)st)); }
      CC(cudaStreamSynchronize(st));            // wave barrier; arrays reused only after
    }
    free(sz);free(fo);free(po);free(br);
    printf("ORACLE async inflight=%d nreads=%ld size=%zu hot=%ld\n",IF,NREADS,SZ,HOT);
  } else { // batch
    int B=N; size_t bufsz=(size_t)B*SZ; void*dptr; CC(cudaMalloc(&dptr,bufsz)); CK(cuFileBufRegister(dptr,bufsz,0));
    CUfileIOParams_t* p=calloc(B,sizeof(*p)); CUfileIOEvents_t* ev=calloc(B,sizeof(*ev));
    for(long base=0; base<NREADS; base+=B){
      int n=(NREADS-base<B)?(int)(NREADS-base):B;
      for(int i=0;i<n;i++){
        p[i].mode=CUFILE_BATCH; p[i].fh=FH; p[i].opcode=CUFILE_READ; p[i].cookie=(void*)(long)i;
        p[i].u.batch.devPtr_base=dptr; p[i].u.batch.devPtr_offset=(off_t)i*SZ;
        p[i].u.batch.file_offset=(off_t)(base+i)*SZ; p[i].u.batch.size=SZ;
      }
      CUfileBatchHandle_t bh; CK(cuFileBatchIOSetUp(&bh,n));
      CK(cuFileBatchIOSubmit(bh,n,p,0));
      unsigned done=0;
      while(done<(unsigned)n){ unsigned nr=n; struct timespec to={1,0};
        cuFileBatchIOGetStatus(bh,n-done,&nr,ev,&to); done+=nr; }
      cuFileBatchIODestroy(bh);
    }
    free(p); free(ev);
    printf("ORACLE batch B=%d nreads=%ld size=%zu\n",B,NREADS,SZ);
  }
  cuFileHandleDeregister(FH); close(fd); cuFileDriverClose();
  return 0;
}
