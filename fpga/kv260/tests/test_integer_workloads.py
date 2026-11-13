#!/usr/bin/env python3
"""Run actual integer kernel bodies against original host references natively.
This checks TYPE/reference agreement, not compiled GPU execution or FPGA timing.
"""
from pathlib import Path
import subprocess,tempfile,sys
ROOT=Path(__file__).resolve().parents[3]
ENV=r'''
#pragma once
#include <cstdint>
#include <mutex>
#include <condition_variable>
struct Dim { unsigned x=0,y=0,z=0; };
inline thread_local Dim blockIdx,threadIdx;
inline Dim blockDim,gridDim;
#define __UNIFORM__
struct Barrier { std::mutex m; std::condition_variable cv; unsigned n=0,phase=0;
 void wait(){std::unique_lock<std::mutex> l(m);auto p=phase;if(++n==blockDim.x*blockDim.y){n=0;++phase;cv.notify_all();}else cv.wait(l,[&]{return p!=phase;});}};
inline Barrier barrier;
alignas(64) inline int local_data[512];
inline void* __local_mem(unsigned){return local_data;}
inline void __syncthreads(){barrier.wait();}
'''

def main():
 with tempfile.TemporaryDirectory() as td:
  w=Path(td)
  for n in ['VX_config','VX_types']:
   subprocess.run([sys.executable,str(ROOT/'hw/scripts/gen_config.py'),'-i',str(ROOT/'hw/rtl'/f'{n}.vh'),'-o',str(w/f'{n}.h')],check=True)
  (w/'env.h').write_text(ENV)
  for name in ['vx_spawn.h','vx_print.h','vx_intrinsics.h','vx_dfv_collision_report.h','vx_dfv_host.h']:(w/name).write_text('#include "env.h"\n')
  for app in ['vecadd','relu','sgemm','sgemm2','stencil3d']:
   path=ROOT/'tests/regression'/app
   body=(path/'kernel.cpp').read_text().split('int main()')[0]
   if app in ['vecadd','relu']:
    task='''arg.src0_addr=(uintptr_t)a.data();arg.dst_addr=(uintptr_t)out.data();'''
    if app=='vecadd':task+='arg.src1_addr=(uintptr_t)b.data();'
    task+='for(unsigned i=0;i<N;i++){blockIdx.x=i;kernel_body(&arg);ref[i]='+('a[i]+b[i]' if app=='vecadd' else '(a[i]<0?0:a[i])')+';}'
   elif app=='stencil3d':
    task='''arg.size=4;arg.A_addr=(uintptr_t)a.data();arg.B_addr=(uintptr_t)out.data();blockDim={1,1,1};
    for(unsigned z=0;z<4;z++)for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++){blockIdx={x,y,z};threadIdx={0,0,0};kernel_body(&arg);}
    stencil_cpu(ref.data(),a.data(),4,4,4);'''
   else:
    task='''arg.size=4;arg.A_addr=(uintptr_t)a.data();arg.B_addr=(uintptr_t)b.data();arg.C_addr=(uintptr_t)out.data();'''
    if app=='sgemm':task+='for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++){blockIdx={x,y,0};kernel_body(&arg);}'
    else:task+='''arg.tile_size=2;blockDim={2,2,1};
    for(unsigned y=0;y<2;y++)for(unsigned x=0;x<2;x++){
      std::vector<std::thread> workers;for(unsigned j=0;j<2;j++)for(unsigned i=0;i<2;i++) workers.emplace_back([&,x,y,i,j]{blockIdx={x,y,0};threadIdx={i,j,0};kernel_body(&arg);});
      for(auto& worker:workers)worker.join();}
    '''
    task+='matmul_cpu(ref.data(),a.data(),b.data(),4,4);'
   count=64 if app in ['vecadd','relu','stencil3d'] else 16
   source=f'''#include <cassert>\n#include <thread>\n#include "env.h"\n#define main unused_original_host_main\n#include "{path}/main.cpp"\n#undef main\n'''+body+f'''\nint main(){{for(int trial=0;trial<10;trial++){{constexpr unsigned N={count};std::vector<int>a(N),b(N),out(N),ref(N);for(unsigned i=0;i<N;i++){{a[i]=Comparator<int>::generate();b[i]=Comparator<int>::generate();assert(a[i]>=-8 && a[i]<=8);}}kernel_arg_t arg={{}};{task}for(unsigned i=0;i<N;i++)assert(Comparator<int>::compare(out[i],ref[i],i,0));}}}}'''
   (w/'test.cpp').write_text(source)
   subprocess.run(['g++','-std=c++17','-O2','-fsanitize=undefined','-fno-sanitize-recover=all','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-DTYPE=int','-DXLEN_32','-DEXT_F_DISABLE','-I'+str(w),'-I'+str(path),'-I'+str(ROOT/'runtime/include'),'-I'+str(ROOT/'sim/common'),str(w/'test.cpp'),'-pthread','-o',str(w/'test')],check=True)
   subprocess.run([str(w/'test')],check=True)
   print('PASS:',app,'actual integer kernel body vs original host reference (10 bounded-input trials, UBSan)')
if __name__=='__main__':main()
