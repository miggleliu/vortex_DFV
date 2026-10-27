#!/usr/bin/env python3
"""Exercise actual compiled callbacks against a file-backed MMIO device.

Validates host-side memory/API behavior; this does not emulate GPU execution.
"""
import ctypes as c
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
PORT=ROOT/'fpga/kv260'
P=c.c_void_p; U=c.c_uint32; Q=c.c_uint64
SPECS=[('dev_open',[c.POINTER(P)]),('dev_close',[P]),('dev_caps',[P,U,c.POINTER(Q)]),
       ('mem_alloc',[P,Q,c.c_int,c.POINTER(P)]),('mem_reserve',[P,Q,Q,c.c_int,c.POINTER(P)]),
       ('mem_free',[P]),('mem_access',[P,Q,Q,c.c_int]),('mem_address',[P,c.POINTER(Q)]),
       ('mem_info',[P,c.POINTER(Q),c.POINTER(Q)]),('copy_to_dev',[P,P,Q,Q]),
       ('copy_from_dev',[P,P,Q,Q]),('start',[P,P,P]),('ready_wait',[P,Q]),
       ('dcr_read',[P,U,c.POINTER(U)]),('dcr_write',[P,U,U]),('mpm_query',[P,U,U,c.POINTER(Q)])]
class Callbacks(c.Structure):
    _fields_=[(n,c.CFUNCTYPE(c.c_int,*args)) for n,args in SPECS]

def main():
    with tempfile.TemporaryDirectory(prefix='kv260-driver-') as tmp:
        tmp=Path(tmp); hw=tmp/'hw';hw.mkdir()
        for n in ['VX_config','VX_types']:
            subprocess.run(['python3',str(ROOT/'hw/scripts/gen_config.py'),'-i',str(ROOT/'hw/rtl'/f'{n}.vh'),'-o',str(hw/f'{n}.h')],check=True)
        cfg=json.loads((PORT/'config.json').read_text())
        lib=tmp/'libvortex-kv260.so'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-O2','-DNDEBUG','-shared','-fPIC',
                        *['-D'+d for d in cfg['defines']],'-I'+str(hw),
                        *['-I'+str(ROOT/d) for d in ['runtime/include','runtime/common','sim/common']],
                        str(PORT/'runtime/vortex.cpp'),'-o',str(lib)],check=True)
        device_file=tmp/'mmio.bin'
        image=bytearray(0x80000)
        for off,v in [(0,0x56584446),(4,0x20000),(0x10,0x01040420),(0x14,0x40000),
                      (0x48,0x1a2cde44),(0x4c,0x404),(0x50,4096)]: struct.pack_into('<I',image,off,v)
        device_file.write_bytes(image)
        os.environ['VORTEX_KV260_MEM']=str(device_file)
        os.environ['VORTEX_KV260_TIMEOUT_MS']='1'
        dll=c.CDLL(str(lib)); cb=Callbacks()
        assert dll.vx_dev_init(c.byref(cb))==0
        dev=P();assert cb.dev_open(c.byref(dev))==0
        for cap,expected in [(1,4),(2,4),(3,1),(4,64),(5,0x40000)]:
            v=Q();assert cb.dev_caps(dev,cap,c.byref(v))==0 and v.value==expected
        first=P();second=P();kernel=P();args=P()
        assert cb.mem_alloc(dev,5,3,c.byref(first))==0
        assert cb.mem_alloc(dev,128,3,c.byref(second))==0
        address=Q();assert cb.mem_address(first,c.byref(address))==0 and address.value==0x10000
        assert cb.mem_reserve(dev,0x10001,32,3,c.byref(args))!=0
        assert cb.mem_reserve(dev,0x80000000,0x100,1,c.byref(kernel))==0
        assert cb.mem_reserve(dev,0x8000fff0,32,1,c.byref(args))!=0
        data=c.create_string_buffer(b'ABCDE');out=c.create_string_buffer(5)
        assert cb.copy_to_dev(second,data,1,5)==0
        assert cb.copy_from_dev(out,second,1,5)==0 and out.raw==b'ABCDE'
        assert cb.copy_to_dev(first,data,4,5)!=0
        assert cb.copy_from_dev(out,second,127,5)!=0
        assert cb.mem_free(first)==0
        assert cb.mem_alloc(dev,8,3,c.byref(args))==0
        assert cb.mem_address(args,c.byref(address))==0 and address.value==0x10000
        assert cb.dcr_write(dev,1,0x80000000)==0
        v=U();assert cb.dcr_read(dev,1,c.byref(v))==0 and v.value==0x80000000
        assert cb.start(dev,kernel,args)==0
        assert cb.copy_from_dev(out,second,0,5)!=0
        assert cb.ready_wait(dev,100)!=0
        with device_file.open('rb') as f:
            f.seek(8);assert struct.unpack('<I',f.read(4))[0]==2
        assert cb.copy_from_dev(out,second,1,5)==0
        assert cb.mem_free(second)==cb.mem_free(kernel)==cb.mem_free(args)==0
        assert cb.dev_close(dev)==0
        image[4:8]=struct.pack('<I',0x10000);device_file.write_bytes(image)
        assert cb.dev_open(c.byref(dev))!=0
        print('PASS: actual runtime callbacks: configuration, allocation/overlap, unaligned byte transfers, bounds, reuse, DCRs, busy guards, timeout reset, old-image rejection')

if __name__=='__main__': main()
