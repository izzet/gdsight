// gds_align_probe.c — controlled (mis)alignment cuFileRead generator for the
// cross-layer read-amplification study (Study A, alignment axis). Issues N cuFileReads
// with exact control of file_offset misalignment, I/O size, and device-pointer offset,
// so the tracer can measure per-op A_byte (device bytes / requested bytes). GDS docs say
// a sub-4K-aligned offset/size/ptr falls to an internal (bounce) path; this quantifies it.
//
// build: see workloads/build_align_probe.sh
// usage: gds_align_probe <file> <iosize> <foff_misalign> <ptr_misalign> <count> <stride>
#define _GNU_SOURCE
#include <fcntl.h>
#include <cufile.h>
#include <cuda_runtime.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CK(x) do{ CUfileError_t _e=(x); if(_e.err!=CU_FILE_SUCCESS){ \
  fprintf(stderr,"cufile err %d at %s:%d\n",_e.err,__FILE__,__LINE__); exit(1);} }while(0)
#define CC(x) do{ cudaError_t _e=(x); if(_e!=cudaSuccess){ \
  fprintf(stderr,"cuda err %s at %s:%d\n",cudaGetErrorString(_e),__FILE__,__LINE__); exit(1);} }while(0)

int main(int argc, char** argv){
  if(argc<7){ fprintf(stderr,"usage: %s file iosize foff_misalign ptr_misalign count stride\n",argv[0]); return 2; }
  const char* path=argv[1];
  size_t iosize=strtoull(argv[2],0,10);
  off_t   foff_mis=strtoll(argv[3],0,10);
  off_t   ptr_mis =strtoll(argv[4],0,10);
  long    count=strtol(argv[5],0,10);
  off_t   stride=strtoll(argv[6],0,10);

  CK(cuFileDriverOpen());
  int fd=open(path, O_RDONLY|O_DIRECT);
  if(fd<0){ perror("open"); return 1; }
  CUfileDescr_t descr; memset(&descr,0,sizeof(descr));
  descr.handle.fd=fd; descr.type=CU_FILE_HANDLE_TYPE_OPAQUE_FD;
  CUfileHandle_t fh; CK(cuFileHandleRegister(&fh,&descr));

  size_t bufsz = iosize + ptr_mis + 65536;     // slack for ptr offset
  void* dptr; CC(cudaMalloc(&dptr,bufsz));
  CK(cuFileBufRegister(dptr,bufsz,0));

  long ok=0; ssize_t bytes=0;
  for(long i=0;i<count;i++){
    off_t foff = (off_t)i*stride + foff_mis;
    ssize_t r = cuFileRead(fh, dptr, iosize, foff, ptr_mis);
    if(r>0){ ok++; bytes+=r; }
    else { fprintf(stderr,"cuFileRead i=%ld foff=%ld ret=%zd\n",i,(long)foff,r); }
  }
  printf("ALIGN_PROBE iosize=%zu foff_mis=%ld ptr_mis=%ld ok=%ld/%ld bytes=%zd\n",
         iosize,(long)foff_mis,(long)ptr_mis,ok,count,bytes);

  cuFileBufDeregister(dptr); cudaFree(dptr);
  cuFileHandleDeregister(fh); close(fd); cuFileDriverClose();
  return 0;
}
