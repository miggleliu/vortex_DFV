#!/usr/bin/env python3
"""Actual common runtime + KV260 driver over file-backed MMIO.

The test supplies completion/readback bytes, not simulated GPU execution.
"""
import ctypes as c
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
P=c.c_void_p; U=c.c_uint32; Q=c.c_uint64

def child(work):
    image=work/'mmio.bin'
    data=bytearray(0x100000)
    for offset,value in [(0,0x56584446),(4,0x50000),(0x10,0x01040420),
                         (0x14,0x60000),(0x48,0x08463752),(0x4c,0x404),(0x50,4096),(0x54,0)]:
        struct.pack_into('<I',data,offset,value)
    image.write_bytes(data)
    os.environ['VORTEX_KV260_MEM']=str(image)
    dll=c.CDLL(str(work/'libvortex.so'))
    specs={'vx_dev_open':[c.POINTER(P)],'vx_dev_close':[P],
           'vx_dfv_csr_write':[P,U,U],'vx_dfv_csr_read':[P,U,c.POINTER(U)],
           'vx_mem_reserve':[P,Q,Q,c.c_int,c.POINTER(P)],
           'vx_mem_alloc':[P,Q,c.c_int,c.POINTER(P)],'vx_mem_free':[P],
           'vx_mem_address':[P,c.POINTER(Q)],'vx_start':[P,P,P],'vx_ready_wait':[P,Q]}
    for name,args in specs.items():getattr(dll,name).argtypes=args;getattr(dll,name).restype=c.c_int
    dev=P()
    if os.environ.get('EXPECT_BAD_ENV'):
        assert dll.vx_dev_open(c.byref(dev))!=0
        return
    assert dll.vx_dev_open(c.byref(dev))==0
    assert dll.vx_dfv_csr_write(dev,0x7cf,1)!=0
    assert dll.vx_dfv_csr_write(dev,0x7c0,2)!=0
    assert dll.vx_dfv_csr_write(dev,0x7c9,9)!=0
    assert dll.vx_dfv_csr_write(dev,0x7c0,1)==0
    kernel=P();args=P();address=Q();value=U()
    assert dll.vx_mem_reserve(dev,0x80000000,64,1,c.byref(kernel))==0
    assert dll.vx_mem_alloc(dev,64,3,c.byref(args))==0
    assert dll.vx_mem_address(args,c.byref(address))==0 and address.value>=0x10000+192
    assert dll.vx_dfv_csr_read(dev,0x7c9,c.byref(value))!=0
    for delay in (0x1000,0):
        assert dll.vx_dfv_csr_write(dev,0x7c9,delay)==0
        with image.open('r+b') as stream:stream.seek(0xc);stream.write(struct.pack('<I',0))
        assert dll.vx_start(dev,kernel,args)==0
        assert dll.vx_dfv_csr_write(dev,0x7c9,0)!=0
        assert dll.vx_dfv_csr_read(dev,0x7c9,c.byref(value))!=0
        with image.open('r+b') as stream:
            stream.seek(0x60000);words=list(struct.unpack('<48I',stream.read(192)))
            assert words[0]==0x44465631 and words[3]==0
            assert words[4]==1 and words[13]==delay
            words[19:34]=words[4:19];words[3]=words[2]
            stream.seek(0x60000);stream.write(struct.pack('<48I',*words))
            stream.seek(0xc);stream.write(struct.pack('<I',4))
        assert dll.vx_ready_wait(dev,100)==0
        assert dll.vx_dfv_csr_read(dev,0x7c9,c.byref(value))==0 and value.value==delay
    # A completed old/uninstrumented kernel cannot falsely acknowledge settings.
    assert dll.vx_start(dev,kernel,args)==0
    assert dll.vx_ready_wait(dev,100)!=0
    assert dll.vx_mem_free(kernel)==dll.vx_mem_free(args)==0
    assert dll.vx_dev_close(dev)==0

def main():
    with tempfile.TemporaryDirectory(prefix='dfv-host-api-') as directory:
        work=Path(directory);(work/'hw').mkdir()
        for name in ['VX_config','VX_types']:
            subprocess.run([sys.executable,str(ROOT/'hw/scripts/gen_config.py'),'-i',
                            str(ROOT/'hw/rtl'/f'{name}.vh'),'-o',str(work/'hw'/f'{name}.h')],check=True)
        cfg=json.loads((ROOT/'fpga/kv260/config.json').read_text())
        flags=['-std=c++17','-Wall','-Wextra','-Werror','-O2','-DNDEBUG','-fPIC','-shared',
               *['-D'+d for d in cfg['defines']],'-I'+str(work/'hw'),
               *['-I'+str(ROOT/d) for d in ['runtime/include','runtime/common','sim/common']]]
        for name,sources in [('vortex',['runtime/stub/vortex.cpp','runtime/stub/utils.cpp']),
                             ('vortex-kv260',['fpga/kv260/runtime/vortex.cpp'])]:
            subprocess.run(['g++',*flags,*[str(ROOT/p) for p in sources],'-ldl','-pthread',
                            '-o',str(work/f'lib{name}.so')],check=True)
        env={**os.environ,'LD_LIBRARY_PATH':str(work),'VORTEX_DRIVER':'kv260',
             'VORTEX_DFV_CSRS':'0x7c0=0,0x7c9=0'}
        subprocess.run([sys.executable,__file__,'--child',str(work)],env=env,check=True,capture_output=True)
        for invalid in ('0x7c0=2','0x7c9=9','0x7cf=0','0x7c0=0,0x7c0=1','0x7c0=1,',''):
            subprocess.run([sys.executable,__file__,'--child',str(work)],
                           env={**env,'VORTEX_DFV_CSRS':invalid,'EXPECT_BAD_ENV':'1'},
                           check=True,capture_output=True)
        print('PASS: actual shared runtime/API + backend: reserve mailbox, runtime updates, busy guards, readback, repeated launches, missing acknowledgements and invalid inputs')

if __name__=='__main__':
    if '--child' in sys.argv:child(Path(sys.argv[-1]))
    else:main()
