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
import tempfile

PORT=Path(__file__).resolve().parent
ROOT=PORT.parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import run_dfv_combos as combos

# Contract of the already-built FPGA image. Git history is provenance, not ABI.
PROFILE_DEFINES = ['XLEN_32','NUM_CLUSTERS=1','NUM_CORES=1','NUM_WARPS=4',
    'NUM_THREADS=4','EXT_F_DISABLE','ICACHE_SIZE=4096','DCACHE_SIZE=4096',
    'DCACHE_NUM_BANKS=4','PLATFORM_MEMORY_NUM_BANKS=1']
BOARD_ABI = {
    'XLEN':32,'NUM_CLUSTERS':1,'NUM_CORES':1,'NUM_WARPS':4,'NUM_THREADS':4,
    'ICACHE_SIZE':4096,'DCACHE_SIZE':4096,'DCACHE_NUM_BANKS':4,
    'PLATFORM_MEMORY_NUM_BANKS':1,'STACK_BASE_ADDR':0xffff0000,
    'STACK_LOG2_SIZE':13,'STARTUP_ADDR':0x80000000,'USER_BASE_ADDR':0x10000,
    'IO_BASE_ADDR':0x40,'IO_COUT_ADDR':0x40,'IO_COUT_SIZE':64,'IO_MPM_ADDR':0x80,
    'LMEM_BASE_ADDR':0xffff0000,'LMEM_LOG_SIZE':14,
    'VX_DCR_BASE_STARTUP_ADDR0':1,'VX_DCR_BASE_STARTUP_ADDR1':2,
    'VX_DCR_BASE_STARTUP_ARG0':3,'VX_DCR_BASE_STARTUP_ARG1':4,'VX_DCR_BASE_MPM_CLASS':5,
    'VX_CSR_MSCRATCH':0x340,'VX_CSR_MPM_BASE':0xb00,'VX_CSR_MPM_BASE_H':0xb80,
    'VX_CSR_NUM_THREADS':0xfc0,'VX_CSR_NUM_WARPS':0xfc1,'VX_CSR_NUM_CORES':0xfc2,
    'VX_CSR_DFV_COLL_CTRL':0x7cf,'VX_CSR_DFV_COLL_SELECT':0x7d0,
    'VX_CSR_DFV_THR_CTRL':0x7d1,'VX_CSR_DFV_THR_SELECT':0x7d2,
    'VX_DFV_COLLISION_ID':0x434f4c33,'VX_DFV_THROTTLE_ID':0x54485231,
    'VX_DFV_COLLISION_WIDTH':32,
}
for _name,_address in [('INFO',0xcc8),('NATURAL',0xcc9),('DFV',0xcca),
    ('STATUS',0xccb),('CYCLE_LO',0xccc),('CYCLE_HI',0xccd),('COUNT',0xcce),
    ('DESC',0xccf),('OWNER',0xcd0)]:
    BOARD_ABI['VX_CSR_DFV_COLL_'+_name]=_address
for _name,_address in [('INFO',0xcd8),('EVENTS',0xcd9),('STATUS',0xcda),
    ('CYCLE_LO',0xcdb),('CYCLE_HI',0xcdc),('COUNT',0xcdd),('DESC',0xcde),
    ('OWNER',0xcdf),('THRESHOLD',0xce0)]:
    BOARD_ABI['VX_CSR_DFV_THR_'+_name]=_address
APP_ABI = {
    'VX_CSR_DFV_CTRL':0x7c0,'VX_CSR_DFV_ICACHE_FILL_REQ_STALL':0x7c1,
    'VX_CSR_DFV_RANDOM_SEED':0x7c2,'VX_CSR_DFV_SET_THRESHOLD':0x7c3,
    'VX_CSR_DFV_DCACHE_FILL_REQ_STALL':0x7c4,'VX_CSR_DFV_WRITEBACK_STALL':0x7c5,
    'VX_CSR_DFV_DCACHE_FILL_RSP_STALL':0x7c6,'VX_CSR_DFV_RELEASE_THRESHOLD':0x7c7,
    'VX_CSR_DFV_RELEASE_SEED':0x7c8,'VX_CSR_DFV_RELEASE_DELAY':0x7c9,
    'VX_CSR_DFV_RELEASE_FOREVER':0x7ca,'VX_CSR_DFV_THROTTLE_THRESHOLD':0x7cb,
    'VX_CSR_DFV_FILL_BANK_MASK':0x7cc,'VX_CSR_DFV_DCACHE_CORE_REQ_STALL':0x7cd,
    'VX_CSR_DFV_BANK_THROTTLE_THRESHOLD':0x7ce,
}

def check_compatibility(root, cfg, apps, native_cxx='g++'):
    """Evaluate current working-tree headers with the actual FPGA build defines.

    This checks address/CSR/configuration compatibility, not equivalence of new
    RTL to the old bitstream. New RTL behavior still requires a hardware rebuild.
    """
    if sorted(cfg['defines']) != sorted(PROFILE_DEFINES):
        raise ValueError('FPGA profile defines differ from the implemented image')
    if not shutil.which(native_cxx):
        raise ValueError(f'Native compiler missing: {native_cxx}; use --native-cxx /usr/bin/g++-11')
    report={'checked_apps':{},'scope':'memory map, CSR addresses/IDs and FPGA profile; not RTL behavioral equivalence'}
    with tempfile.TemporaryDirectory(prefix='kv260-abi-') as td:
        work=Path(td)
        for name in ['VX_config','VX_types']:
            subprocess.run([sys.executable,str(root/'hw/scripts/gen_config.py'),'-i',
                str(root/'hw/rtl'/f'{name}.vh'),'-o',str(work/f'{name}.h')],check=True,capture_output=True,text=True)
        for app in apps:
            expected={**BOARD_ABI,**APP_ABI}
            source=['#include <cstdint>','#include <cstdio>','#include <VX_config.h>',
                    '#include <VX_types.h>','#include "common.h"',
                    '#if defined(EXT_F_ENABLE) || defined(EXT_D_ENABLE) || defined(L2_ENABLE) || defined(L3_ENABLE) || defined(VM_ENABLE)',
                    '#error Current software enables a hardware feature absent from this FPGA profile',
                    '#endif','int main() {']
            source += [f'  std::printf("{name}=%llx\\n", (unsigned long long)({name}));' for name in expected]
            source += ['}']
            cpp=work/'probe.cpp';cpp.write_text('\n'.join(source)+'\n')
            exe=work/'probe'
            command=[native_cxx,'-std=c++17',*['-D'+d for d in cfg['defines']],'-DTYPE=int',
                     '-I'+str(work),'-I'+str(root/'tests/regression'/app),str(cpp),'-o',str(exe)]
            compiled=subprocess.run(command,capture_output=True,text=True)
            if compiled.returncode:
                raise ValueError(f'{app}: cannot compile FPGA interface probe:\n{compiled.stderr}')
            values=dict(line.split('=',1) for line in subprocess.check_output([str(exe)],text=True).splitlines())
            actual={name:int(value,16) for name,value in values.items()}
            mismatch=[f'{name}: expected {value:#x}, got {actual[name]:#x}'
                      for name,value in expected.items() if actual.get(name)!=value]
            if mismatch:
                raise ValueError(app+': FPGA interface mismatch:\n'+'\n'.join(mismatch))
            report['checked_apps'][app]=actual
    return report

def source_provenance(root, cfg):
    revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain','--',
        'hw','kernel','runtime','sim/common','tests'],text=True).splitlines()
    delta=subprocess.run(['git','-C',str(root),'diff','--name-only',cfg['upstream_commit'],
                         '--','hw/rtl'],capture_output=True,text=True)
    return {'software_commit':revision,'hardware_commit':cfg['upstream_commit'],
            'working_tree_changes':dirty,
            'rtl_changed_from_image':delta.stdout.splitlines() if not delta.returncode else None}

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
    ap.add_argument('--build',type=Path,help='Configured RV32 Vortex build directory')
    ap.add_argument('--out',type=Path,help='New bundle directory')
    ap.add_argument('--apps',nargs='+',choices=['demo','vecadd'],default=['demo'])
    ap.add_argument('--type',type=int,choices=[1,2,3],default=1)
    ap.add_argument('--host-cxx',default='aarch64-linux-gnu-g++')
    ap.add_argument('--native-cxx',default='g++',help='Native compiler for interface preflight')
    ap.add_argument('--check-only',action='store_true',help='Check interface compatibility without the RV32 SDK or building outputs')
    a=ap.parse_args()
    cfg=json.loads((PORT/'config.json').read_text())
    provenance=source_provenance(ROOT,cfg)
    revision=provenance['software_commit']
    try:
        compatibility=check_compatibility(ROOT,cfg,a.apps,a.native_cxx)
    except (ValueError,subprocess.CalledProcessError) as exc:
        ap.error(str(exc))
    print(f'Hardware image commit: {cfg["upstream_commit"]}',flush=True)
    print(f'Software checkout:     {revision}',flush=True)
    print('PASS: FPGA interface/profile checks for '+', '.join(a.apps),flush=True)
    if provenance['rtl_changed_from_image']:
        print('RTL changes are recorded in provenance; the loaded image retains its built RTL behavior.',flush=True)
    if a.check_only: return 0
    if not a.build or not a.out: ap.error('--build and --out are required for compilation')
    build,out=a.build.resolve(),a.out.resolve()
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
    result={'upstream_commit':revision,**provenance,'compatibility':compatibility,
            'profile':cfg,'type':a.type,'cases':[]}
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
