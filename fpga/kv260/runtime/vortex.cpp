// SPDX-License-Identifier: Apache-2.0
// Implements upstream callbacks_t. Regression host and kernel sources are reused.
#include <common.h>
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <map>
#include <string>
#include <sys/file.h>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>

class vx_device {
  static constexpr uint64_t code_base=0x80000000, code_end=0x80010000;
  static constexpr uint64_t heap_base=0x10000, heap_end=0x20000;
  static constexpr size_t map_size=0x80000, ram_window=0x40000;
  int fd_=-1, lock_=-1;
  volatile uint32_t* regs_=nullptr;
  std::map<uint64_t,uint64_t> blocks_;
  DeviceConfig dcrs_;
  std::array<std::string,64> console_;
  bool running_=false;
  uint32_t rd(size_t off) const { return regs_[off/4]; }
  void wr(size_t off,uint32_t value) { regs_[off/4]=value; __sync_synchronize(); }
  static bool range(uint64_t a,uint64_t n,uint64_t lo,uint64_t hi) {
    return a>=lo && a<=hi && n<=hi-a;
  }
  static int ram_offset(uint64_t a,uint64_t n,size_t* offset) {
    if(range(a,n,code_base,code_end)) *offset=size_t(a-code_base);
    else if(range(a,n,heap_base,heap_end)) *offset=size_t(a);
    else return -1;
    return 0;
  }
  void drain() {
    // A read at 0x40 atomically removes one queued character.
    for(unsigned i=0;i<4096;i++) {
      uint32_t item=rd(0x40);
      if(!(item&0x80000000u)) break;
      auto& line=console_[(item>>8)&63];
      char ch=char(item&255);
      line+=ch;
      if(ch=='\n') { std::fwrite(line.data(),1,line.size(),stdout); line.clear(); }
    }
    std::fflush(stdout);
  }
public:
  int init() {
    lock_=::open("/tmp/vortex-kv260.lock",O_RDWR|O_CREAT,0600);
    if(lock_<0 || flock(lock_,LOCK_EX|LOCK_NB)) {
      std::fprintf(stderr,"KV260 device already in use or lock unavailable\n"); return -1;
    }
    const char* override_path=std::getenv("VORTEX_KV260_MEM");
    fd_=::open(override_path ? override_path : "/dev/mem",O_RDWR|O_SYNC);
    if(fd_<0) { std::perror("KV260 /dev/mem"); return -1; }
    // Override is only for the file-backed driver test, never a board address.
    off_t base=override_path ? 0 : off_t(0xa0000000);
    void* mapped=mmap(nullptr,map_size,PROT_READ|PROT_WRITE,MAP_SHARED,fd_,base);
    if(mapped==MAP_FAILED) { std::perror("KV260 mmap"); return -1; }
    regs_=static_cast<volatile uint32_t*>(mapped);
    if(rd(0)!=0x56584446 || rd(4)!=0x20000 || rd(0x10)!=0x01040420 ||
       rd(0x14)!=0x40000 || rd(0x48)!=0x1a2cde44 || rd(0x4c)!=0x404 || rd(0x50)!=4096) {
      std::fprintf(stderr,"KV260 image/config mismatch; load this package's new bitstream\n"); return -1;
    }
    wr(8,2);
    for(size_t off=ram_window;off<map_size;off+=4) wr(off,0);
    return 0;
  }
  ~vx_device() {
    if(regs_) {
      if(running_) wr(8,2);
      munmap(const_cast<uint32_t*>(regs_),map_size);
    }
    if(fd_>=0) ::close(fd_);
    if(lock_>=0) ::close(lock_);
  }
  int get_caps(uint32_t id,uint64_t* value) {
    switch(id) {
      case VX_CAPS_VERSION: *value=IMPLEMENTATION_ID; break;
      case VX_CAPS_NUM_THREADS: *value=4; break;
      case VX_CAPS_NUM_WARPS: *value=4; break;
      case VX_CAPS_NUM_CORES: *value=1; break;
      case VX_CAPS_CACHE_LINE_SIZE: *value=64; break;
      case VX_CAPS_GLOBAL_MEM_SIZE: *value=0x40000; break;
      case VX_CAPS_LOCAL_MEM_SIZE: *value=1u<<LMEM_LOG_SIZE; break;
      case VX_CAPS_ISA_FLAGS: *value=(uint64_t(MISA_EXT)<<32)|(1ull<<30)|MISA_STD; break;
      case VX_CAPS_NUM_MEM_BANKS: *value=1; break;
      case VX_CAPS_MEM_BANK_SIZE: *value=0x40000; break;
      default: return -1;
    }
    return 0;
  }
  int mem_alloc(uint64_t n,int flags,uint64_t* out) {
    if(!n || n>heap_end-heap_base) return -1;
    n=aligned_size(n,64);
    uint64_t a=heap_base;
    for(const auto& b:blocks_) {
      if(b.first<heap_base || b.first>=heap_end) continue;
      if(n<=b.first-a) break;
      a=b.first+b.second;
    }
    int rc=mem_reserve(a,n,flags);
    if(!rc) *out=a;
    return rc;
  }
  int mem_reserve(uint64_t a,uint64_t n,int) {
    size_t off;
    if(!n || ram_offset(a,n,&off)) return -1;
    for(const auto& b:blocks_)
      if(a<b.first+b.second && b.first<a+n) return -1;
    blocks_[a]=n; return 0;
  }
  int mem_free(uint64_t a) { return blocks_.erase(a)==1 ? 0 : -1; }
  int mem_access(uint64_t a,uint64_t n,int) { size_t off; return ram_offset(a,n,&off); }
  int mem_info(uint64_t* available,uint64_t* used) {
    *used=0;
    for(const auto& b:blocks_) if(b.first>=heap_base && b.first<heap_end) *used+=b.second;
    *available=heap_end-heap_base-*used; return 0;
  }
  int upload(uint64_t a,const void* src,uint64_t n) {
    size_t off;
    if(running_ || ram_offset(a,n,&off)) return -1;
    auto bytes=static_cast<const uint8_t*>(src);
    for(uint64_t i=0;i<n;) {
      size_t p=off+size_t(i), word=p&~size_t(3);
      uint32_t v=rd(ram_window+word);
      do { unsigned shift=unsigned(p&3)*8; v=(v&~(255u<<shift))|(uint32_t(bytes[i])<<shift); ++i; ++p; }
      while(i<n && (p&3));
      wr(ram_window+word,v);
    }
    return 0;
  }
  int download(void* dst,uint64_t a,uint64_t n) {
    size_t off;
    if(running_ || ram_offset(a,n,&off)) return -1;
    auto bytes=static_cast<uint8_t*>(dst);
    for(uint64_t i=0;i<n;i++) bytes[i]=uint8_t(rd(ram_window+((off+i)&~size_t(3)))>>(((off+i)&3)*8));
    return 0;
  }
  int dcr_write(uint32_t a,uint32_t v) {
    if(running_ || a<1 || a>5) return -1;
    wr(0x20,a); wr(0x24,v); dcrs_.write(a,v); return 0;
  }
  int dcr_read(uint32_t a,uint32_t* v) { return dcrs_.read(a,v); }
  int start(uint64_t kernel,uint64_t args) {
    if(running_ || kernel!=code_base || !range(args,1,heap_base,heap_end)) return -1;
    CHECK_ERR(dcr_write(1,uint32_t(kernel)),{return err;});
    CHECK_ERR(dcr_write(2,0),{return err;});
    CHECK_ERR(dcr_write(3,uint32_t(args)),{return err;});
    CHECK_ERR(dcr_write(4,0),{return err;});
    wr(8,1); running_=true; return 0;
  }
  int ready_wait(uint64_t timeout_ms) {
    if(!running_) return 0;
    // Bound a hung experiment even when upstream supplies its 24-hour default.
    uint64_t cap=60000;
    if(auto s=std::getenv("VORTEX_KV260_TIMEOUT_MS")) {
      char* end=nullptr; auto n=std::strtoull(s,&end,10);
      if(!n || *end) return -1;
      cap=n;
    }
    const auto begin=std::chrono::steady_clock::now();
    for(;;) {
      drain();
      uint32_t status=rd(0xc);
      if(status&8) { std::fprintf(stderr,"GPU address fault, code=%u\n",rd(0x38)); running_=false; return -1; }
      if(status&4) {
        running_=false;
        // Backend may still be serializing the final accepted console line.
        for(unsigned i=0;i<128;i++) drain();
        int32_t exit_code=int32_t(rd(0x1008));
        if(exit_code) std::fprintf(stderr,"GPU kernel exit code=%d\n",exit_code);
        return exit_code ? -1 : 0;
      }
      auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-begin).count();
      if(uint64_t(elapsed)>=std::min(timeout_ms,cap)) {
        std::fprintf(stderr,"KV260 GPU timed out; resetting GPU\n"); wr(8,2); running_=false; return -1;
      }
      std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
  }
  int mpm_query(uint32_t a,uint32_t core,uint64_t* v) {
    if(running_ || core!=0 || a<VX_CSR_MPM_BASE || a>=VX_CSR_MPM_BASE+32) return -1;
    size_t off=0x1000+8*(a-VX_CSR_MPM_BASE);
    *v=uint64_t(rd(off))|(uint64_t(rd(off+4))<<32); return 0;
  }
};
#include <callbacks.inc>
