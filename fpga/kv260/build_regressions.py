#!/usr/bin/env python3
"""Build upstream regression host sources for ARM and kernels for RV32.

Uses the exact combo patcher/defaults in tests/run_dfv_combos.py on private copies.
Never edits the verified regression kernels in the checkout.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys

PORT=Path(__file__).resolve().parent
ROOT=PORT.parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import run_dfv_combos as combos

def check_image(image, elf, objdump, nm):
    data=image.read_bytes()
    if len(data)<=16: raise ValueError('Truncated kernel.vxbin')
    lo,hi=struct.unpack('<QQ',data[:16])
    if lo!=0x80000000 or hi>0x80010000 or hi<=lo or len(data)-16>hi-lo:
        raise ValueError(f'Kernel does not fit the 64 KiB code region: {lo:#x}..{hi:#x}')
    symbols={}
    for line in subprocess.check_output([nm,str(elf)],text=True).splitlines():
        m=re.match(r'([0-9a-fA-F]+)\s+\w\s+(\S+)',line)
        if m: symbols[m[2]]=int(m[1],16)
    if not all(s in symbols for s in ['_end','__tbss_size']):
        raise ValueError('Kernel lacks standard startup/TLS symbols')
    if symbols['_end']+16*symbols['__tbss_size']>0x80010000:
        raise ValueError('Per-hart TLS overlaps the end of the code/TLS allocation')
    asm=subprocess.check_output([objdump,'-d',str(elf)],text=True)
    # This first bitstream has no F/D/C/A/V instructions. Reject a kernel instead
    # of starting unsupported instructions on hardware. Custom Vortex opcodes pass.
    for line in asm.splitlines():
        m=re.match(r'\s*[0-9a-f]+:\s+([0-9a-f]{4,8})\s+(\S+)',line)
        if not m: continue
        opcode=int(m[1],16)&0x7f
        if len(m[1])!=8 or opcode in {0x07,0x27,0x43,0x47,0x4b,0x4f,0x53,0x57,0x2f}:
            raise ValueError('Instruction outside the integer FPGA profile: '+line.strip())
    return {'min_vma':lo,'max_vma':hi,'file_bytes':len(data),
            'sha256':hashlib.sha256(data).hexdigest()}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build',required=True,type=Path,help='Configured RV32 Vortex build directory')
    ap.add_argument('--out',required=True,type=Path,help='New bundle directory')
    ap.add_argument('--apps',nargs='+',choices=['demo','vecadd'],default=['demo'])
    ap.add_argument('--type',type=int,choices=[1,2,3],default=1)
    ap.add_argument('--host-cxx',default='aarch64-linux-gnu-g++')
    a=ap.parse_args()
    build,out=a.build.resolve(),a.out.resolve()
    cfg=json.loads((PORT/'config.json').read_text())
    revision=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    if revision!=cfg['upstream_commit']: ap.error('Unreviewed upstream revision')
    if not (build/'config.mk').is_file(): ap.error('--build needs an existing configured RV32 build')
    if out.exists(): ap.error('--out must be new')
    if not shutil.which(a.host_cxx): ap.error(f'Host cross compiler missing: {a.host_cxx}')
    triple=subprocess.check_output([a.host_cxx,'-dumpmachine'],text=True).strip()
    if not triple.startswith('aarch64'): ap.error('--host-cxx must target aarch64 Linux')
    (out/'hw').mkdir(parents=True)
    # Preserve SDK paths, force matching hardware XLEN, and isolate all outputs.
    (out/'config.mk').write_text(f'include {build}/config.mk\noverride XLEN := 32\n')
    for name in ['VX_config','VX_types']:
        subprocess.run([sys.executable,str(ROOT/'hw/scripts/gen_config.py'),'-i',str(ROOT/'hw/rtl'/f'{name}.vh'),'-o',str(out/'hw'/f'{name}.h')],check=True)
    probe=out/'probe.mk'
    probe.write_text('include config.mk\nprobe:\n\t@echo $(XLEN)\n\t@echo $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-gcc\n\t@echo $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-objdump\n\t@echo $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-nm\n')
    lines=subprocess.check_output(['make','-s','-f',str(probe),'probe'],cwd=out,text=True).splitlines()
    if lines[0]!='32': raise ValueError('SDK is not RV32')
    gcc,objdump,nm=lines[1:4]
    for tool in [gcc,objdump,nm]:
        if not Path(tool).is_file(): raise ValueError('Install the RV32 Vortex SDK; missing '+tool)
    defs=['-D'+s for s in cfg['defines']]
    includes=['-I'+str(out/'hw'),'-I'+str(ROOT/'sim/common'),'-I'+str(ROOT/'runtime/include'),'-I'+str(ROOT/'runtime/common')]
    runtime=out/'runtime'; runtime.mkdir()
    for name,sources,libs in [('vortex',[ROOT/'runtime/stub/vortex.cpp',ROOT/'runtime/stub/utils.cpp'],['-ldl','-pthread']),
                              ('vortex-kv260',[PORT/'runtime/vortex.cpp'],['-pthread'])]:
        subprocess.run([a.host_cxx,'-std=c++17','-O2','-DNDEBUG','-fPIC','-shared',*defs,*includes,*map(str,sources),*libs,'-o',str(runtime/f'lib{name}.so')],check=True)
    kn=out/'kernel';kn.mkdir()
    cflags=['-march=rv32imaf','-mabi=ilp32f','-O3','-mcmodel=medany','-fno-exceptions','-fdata-sections','-ffunction-sections',
            '-I'+str(ROOT/'kernel/include'),'-I'+str(out/'hw'),*defs]
    subprocess.run(['make','-f',str(ROOT/'kernel/Makefile'),f'ROOT_DIR={out}',f'CFLAGS={shlex.join(cflags)}','libvortex.a'],cwd=kn,check=True)
    result={'upstream_commit':revision,'profile':cfg,'type':a.type,'cases':[]}
    source_hashes={}
    choices={1:combos.COMBOS_TYPE1,2:combos.COMBOS_TYPE2,3:combos.COMBOS_TYPE3}[a.type]
    for app in a.apps:
        original=ROOT/'tests/regression'/app
        for p in original.iterdir():
            if p.is_file(): source_hashes[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
        for combo in choices:
            tag=combos.combo_tag(combo)
            case=out/'cases'/app/tag; source=case/'source'; source.mkdir(parents=True)
            for p in original.iterdir():
                if p.is_file(): shutil.copyfile(p,source/p.name)
            saved=combos.kernel_path
            try:
                combos.kernel_path=lambda _:str(source/'kernel.cpp')
                combos.patch_kernel(app,combo)
            finally: combos.kernel_path=saved
            # The integer-only image uses vecadd's existing TYPE customization.
            # This is explicitly a different data-type variant of the same test.
            flags=defs+(['-DTYPE=int'] if app=='vecadd' else [])
            mk=case/'Makefile'
            mk.write_text(f'include {out}/config.mk\nPROJECT := {app}\nSRC_DIR := {source}\nSRCS := $(SRC_DIR)/main.cpp\nVX_SRCS := $(SRC_DIR)/kernel.cpp\nCONFIGS := {shlex.join(flags)}\ninclude {ROOT}/tests/regression/common.mk\n')
            subprocess.run(['make',f'ROOT_DIR={out}',f'CXX={a.host_cxx}',app,'kernel.vxbin'],cwd=case,check=True)
            info=check_image(case/'kernel.vxbin',case/'kernel.elf',objdump,nm)
            result['cases'].append({'app':app,'combo':tag,'args':shlex.split(combos.TEST_APPS[app])+['-d'],
                                    'dtype':'int','combo_configuration':combo,'directory':str(case.relative_to(out)),**info})
    result['regression_source_sha256']=source_hashes
    (out/'bundle.json').write_text(json.dumps(result,indent=2)+'\n')
    for name in ['run_board.py','README.md']:
        shutil.copyfile(PORT/name,out/name)
    (out/'tests').mkdir()
    for name in ['dfv_csr.py','run_dfv_combos.py']:
        shutil.copyfile(ROOT/'tests'/name,out/'tests'/name)
    print(f'Built {len(result["cases"])} existing regression variants; transfer {out} to the KV260')

if __name__=='__main__': main()
