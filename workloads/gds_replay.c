// gds_replay.c — replay a REAL access pattern (offset,size pairs from a CSV) through GDS
// via cuFileRead, so the tracer measures the per-op device read-amplification of an
// in-the-wild workload (e.g. WSI tile geometry from a real Aperio SVS). Isolates the
// read-amp of the real *access pattern* from library/threshold confounds.
// usage: gds_replay <datafile> <pairs.csv> <maxcount>   (csv lines: file_offset,size)
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

int main(int argc,char**argv){
  if(argc<4){ fprintf(stderr,"usage: %s datafile pairs.csv maxcount\n",argv[0]); return 2; }
  const char*data=argv[1]; const char*csv=argv[2]; long maxc=strtol(argv[3],0,10);
  // load pairs
  off_t *offs=malloc(sizeof(off_t)*maxc); size_t *szs=malloc(sizeof(size_t)*maxc);
  FILE*fp=fopen(csv,"r"); if(!fp){perror("csv");return 1;}
  long n=0; long long o,s;
  while(n<maxc && fscanf(fp,"%lld,%lld",&o,&s)==2){ offs[n]=o; szs[n]=(size_t)s; n++; }
  fclose(fp);
  size_t maxsz=0; for(long i=0;i<n;i++) if(szs[i]>maxsz) maxsz=szs[i];

  CK(cuFileDriverOpen());
  int fd=open(data,O_RDONLY|O_DIRECT); if(fd<0){perror("open");return 1;}
  CUfileDescr_t d; memset(&d,0,sizeof(d)); d.handle.fd=fd; d.type=CU_FILE_HANDLE_TYPE_OPAQUE_FD;
  CUfileHandle_t fh; CK(cuFileHandleRegister(&fh,&d));
  size_t bufsz=((maxsz+65535)/65536)*65536; void*dptr; CC(cudaMalloc(&dptr,bufsz));
  CK(cuFileBufRegister(dptr,bufsz,0));

  long ok=0; long long req=0,dev_ret=0;
  for(long i=0;i<n;i++){
    ssize_t r=cuFileRead(fh,dptr,szs[i],offs[i],0);
    if(r>0){ ok++; req+=szs[i]; dev_ret+=r; }
    else fprintf(stderr,"read i=%ld off=%lld sz=%zu ret=%zd\n",i,(long long)offs[i],szs[i],r);
  }
  printf("REPLAY n=%ld ok=%ld requested=%.1fMiB\n",n,ok,req/1048576.0);
  cuFileBufDeregister(dptr); cudaFree(dptr);
  cuFileHandleDeregister(fh); close(fd); cuFileDriverClose();
  return 0;
}
