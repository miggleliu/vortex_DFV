#!/usr/bin/env python3
"""Run infrastructure checks; the board regressions remain tests/regression/*.

Needs Verilator 5, native GCC >= 11, make, Python 3. No Vortex SDK required.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

PORT=Path(__file__).resolve().parents[1]
ROOT=PORT.parents[1]

def smoke_words(a):
    # Infrastructure-only AXI/memory/start/reset check, not a regression substitute.
    return [0x800012b7,(a<<20)|0x313,0x01700393,0x00730433,0x0082a023,
            0x0002a483,0x00148493,0x0092a223,0x12345537,0x67850513,
            0x00a2a423,0x0330000f,0x0000000b]

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--work',type=Path,required=True,help='New output directory')
    ap.add_argument('--verilator',default='verilator')
    a=ap.parse_args()
    work=a.work.resolve()
    subprocess.run([sys.executable,str(PORT/'prepare.py'),'--out',str(work),
                    '--simulation','--verilator',a.verilator],check=True)
    def run(command,name,cwd=work):
        with (work/name).open('w') as log:
            subprocess.run(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,check=True)
    run([sys.executable,str(PORT/'tests/test_driver.py')],'driver-test.log',ROOT)
    run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_dfv_csr.py','-v'],
        'upstream-csr-tests.log',ROOT)
    files=['-f','sources.f','kv260_native_bram.sv','kv260_vortex_bram.sv','kv260_vortex_bd.v']
    run([a.verilator,'--lint-only','--top-module','kv260_vortex_bd',*files],'rtl-lint.log')
    for n in [19,20]:
        (work/f'kernel_{n}.hex').write_text(''.join(f'{v:08x}\n' for v in smoke_words(n)))
    for top,bench,folder in [('tb_backend','tb_backend.sv','obj_backend'),
                              ('tb_kv260_vortex_bram','tb_board.sv','obj_board')]:
        run([a.verilator,'--binary','--timing','--assert','--timescale-override','1ns/1ps',
             '--top-module',top,'-j','4','-MAKEFLAGS','OBJCACHE= VK_PCH_I_FAST= VK_PCH_I_SLOW=',
             *files,str(PORT/'tests'/bench),'--Mdir',folder],folder+'-build.log')
        run([str(work/folder/f'V{top}')],folder+'-test.log')
    print('PASS: native runtime API tests, 12 upstream CSR/parser tests, RTL lint, backend and complete GPU-wrapper simulations')
    print('These checks do not claim SDK kernel compilation, Vivado fit/timing or physical board execution.')

if __name__=='__main__': main()
