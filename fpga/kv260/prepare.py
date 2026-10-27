#!/usr/bin/env python3
"""Prepare a pinned, self-contained native-Windows Vivado source directory."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PORT = Path(__file__).resolve().parent
DIRS = ['hw/rtl', 'hw/rtl/libs', 'hw/rtl/interfaces', 'hw/rtl/core', 'hw/rtl/mem', 'hw/rtl/cache']
EXCLUDE = {'VX_schedule_dfv.sv', 'VX_schedule_dfv_v2.sv', 'VX_dfv_primitives.sv', 'VX_mem_unit_top.sv'}

def prepare(out, verilator, simulation=False):
    cfg=json.loads((PORT/'config.json').read_text())
    revision=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    if revision!=cfg['upstream_commit']:
        raise ValueError(f"Expected reviewed upstream {cfg['upstream_commit']}; got {revision}")
    tracked=subprocess.check_output(['git','-C',str(ROOT),'ls-files'],text=True).splitlines()
    selected=sorted(p for p in tracked if str(Path(p).parent) in DIRS
                    and Path(p).suffix in {'.sv','.v','.vh','.svh'} and Path(p).name not in EXCLUDE)
    changed=subprocess.check_output(['git','-C',str(ROOT),'diff','HEAD','--name-only','--',*selected],text=True).strip()
    if changed: raise ValueError('Upstream RTL is modified; review before preparing:\n'+changed)
    if out.exists(): raise ValueError(f'Use a new output directory: {out}')
    (out/'rtl').mkdir(parents=True)
    defines=['SYNTHESIS','NDEBUG',*cfg['defines']]
    if not simulation: defines.insert(0,'VIVADO')
    flags=['-D'+d for d in defines]+['-I'+str(ROOT/d) for d in DIRS]
    records=[]
    for rel in selected:
        src=ROOT/rel
        if src.suffix not in {'.sv','.v'}: continue
        content=subprocess.check_output([verilator,'-E','-P',*flags,str(src)],text=True)
        if re.search(r'^\s*`(?:include|ifdef|ifndef|define|elsif|else|endif)\b',content,re.M):
            raise ValueError(f'Unresolved preprocessing directives in {rel}')
        dest=out/'rtl'/src.name
        dest.write_text(f'// {rel} at {revision}; Apache-2.0; see LICENSE.\n'+content)
        records.append({'source':rel,'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
                        'prepared':str(dest.relative_to(out)), 'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
    def order(p):
        s=p.read_text()
        return (0 if p.name=='VX_gpu_pkg.sv' else 1 if re.search(r'^\s*package\b',s,re.M)
                else 2 if re.search(r'^\s*interface\b',s,re.M) else 3,p.name)
    sources=sorted((out/'rtl').glob('*'),key=order)
    (out/'sources.f').write_text(''.join(str(p.relative_to(out))+'\n' for p in sources))
    for name in ['create_project.tcl','build_bitstream.tcl','pre_opt_hook.tcl',
                 'xilinx_async_bram_patch_fast.tcl','bram_patch_queries.tcl','vortex.bif','shell.json','kv260-vortex.dts','README.md','config.json']:
        shutil.copyfile(PORT/name,out/name)
    for name in ['kv260_native_bram.sv','kv260_vortex_bram.sv','kv260_vortex_bd.v']:
        shutil.copyfile(PORT/'rtl'/name,out/name)
    shutil.copyfile(ROOT/'LICENSE',out/'LICENSE')
    shutil.copyfile(ROOT/'hw/scripts/xilinx_async_bram_patch.tcl',out/'xilinx_async_bram_patch.tcl')
    cfg.update(preprocessor=subprocess.check_output([verilator,'--version'],text=True).strip(),
               simulation=simulation, sources=records)
    (out/'manifest.json').write_text(json.dumps(cfg,indent=2)+'\n')
    print(f'Prepared {len(sources)} source units at {revision} in {out}')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',required=True,type=Path)
    ap.add_argument('--verilator',default='verilator')
    ap.add_argument('--simulation',action='store_true')
    a=ap.parse_args()
    prepare(a.out.resolve(),a.verilator,a.simulation)

if __name__=='__main__': main()
