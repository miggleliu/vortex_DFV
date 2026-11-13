#!/usr/bin/env python3
"""Runtime settings, actual kernel helper, and source reuse contracts."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import dfv_host
import run_dfv_combos as runner
from test_dfv_csr import report

ROOT=Path(__file__).resolve().parents[1]

class SettingsTests(unittest.TestCase):
    def test_every_combo_and_override(self):
        for choices in (runner.COMBOS_TYPE1,runner.COMBOS_TYPE2,runner.COMBOS_TYPE3):
            for combo in choices:
                values=dfv_host.combo_values(combo,runner)
                self.assertEqual(values[0x7c0],int(any(combo['stalls'])))
                self.assertEqual(len(dfv_host.environment(values).split(',')),15)
        self.assertEqual(dfv_host.overrides(['release_delay=0','0x7c3=0xe000']),
                         {0x7c9:0,0x7c3:0xe000})
        for pairs in (['0x7cf=1'],['enable=2'],['set_threshold=-1'],
                      ['release_delay=9'],['release_delay=0x9000'],['random_seed=0x100000000']):
            with self.subTest(pairs=pairs),self.assertRaises(ValueError):
                dfv_host.overrides(pairs)

    def test_all_twenty_original_kernels_use_the_shared_helper(self):
        self.assertEqual(len(runner.TEST_APPS),13)
        for app in runner.TEST_APPS:
            source=(ROOT/'tests/regression'/app/'kernel.cpp').read_text()
            self.assertIn('vx_dfv_apply_host_config()',source)
            self.assertNotIn('csr_write(VX_CSR_DFV_SET_THRESHOLD,',source)

    def test_board_case_filters_and_runtime_overrides_need_no_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            cases=[]
            for app in runner.TEST_APPS:
                for kind,choices in ((1,runner.COMBOS_TYPE1),(2,runner.COMBOS_TYPE2),(3,runner.COMBOS_TYPE3)):
                    for combo in choices:
                        cases.append({'app':app,'type':kind,'combo':runner.combo_tag(combo),
                                      'host_csrs':{hex(k):v for k,v in dfv_host.combo_values(combo,runner).items()}})
            self.assertEqual(len(cases),130)
            (work/'bundle.json').write_text(json.dumps({'format_version':2,'cases':cases}))
            env={**os.environ,'PYTHONPATH':str(ROOT/'tests')}
            base=[sys.executable,str(ROOT/'fpga/kv260/run_board.py'),'--bundle',str(work),'--list']
            selected=subprocess.run([*base,'--apps','demo','--type','1','--combo','none','--csr','release_delay=0'],
                                    env=env,check=True,capture_output=True,text=True)
            self.assertEqual(len(selected.stdout.splitlines()),1)
            self.assertTrue(selected.stdout.startswith('demo/type1/none'))
            self.assertIn("'0x7c9': 0",selected.stdout)
            all_cases=subprocess.run(base,env=env,check=True,capture_output=True,text=True)
            self.assertEqual(len(all_cases.stdout.splitlines()),130)

    def test_dynamic_runner_uses_host_values_without_editing_sources(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'reg/demo').mkdir(parents=True);(root/'build').mkdir()
            kernel=root/'reg/demo/kernel.cpp'
            original='if (vx_dfv_apply_host_config()) return -1;\n'
            kernel.write_text(original)
            seen=[]
            def launch(command,**kwargs):
                self.assertEqual(kernel.read_text(),original)
                seen.append(kwargs['env']['VORTEX_DFV_CSRS'])
                kwargs['stdout'].write(''.join(report())+'PERF: instrs=20, cycles=100, IPC=0.2\n')
                return subprocess.CompletedProcess(command,0)
            with patch.multiple(runner,REG_DIR=str(root/'reg'),BUILD_DIR=str(root/'build'),
                                RUN_LOG=str(root/'build/run.log'),LOG_BASE=str(root/'logs')),\
                 patch.object(runner.subprocess,'run',side_effect=launch),\
                 patch.object(sys,'argv',['runner','--apps','demo','--type','1','--csr','release_delay=0']),\
                 redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(),0)
            self.assertEqual(len(seen),3)
            self.assertTrue(seen[0].startswith('0x7c0=0x0,'))
            self.assertTrue(seen[1].startswith('0x7c0=0x1,'))
            self.assertTrue(all('0x7c9=0x0,' in item for item in seen))

class KernelHelperTests(unittest.TestCase):
    def test_actual_shared_helper_writes_csrs_and_returns_readback(self):
        source=r'''
#include <cstdint>
#include <cstdarg>
#include <cstdio>
#include <cassert>
#include <sys/mman.h>
#define __VX_INTRINSICS_H__
static uint32_t regs[4096];
static unsigned writes=0, first_addr=0, first_value=1, last_addr=1;
static void write_csr(unsigned a,uint32_t v){if(!writes){first_addr=a;first_value=v;}++writes;last_addr=a;regs[a]=v;}
#define csr_write(a,v) write_csr(a,v)
#define csr_read(a) regs[a]
static unsigned vx_core_id(){return 0;}
static void vx_fence(){}
#include <vx_dfv_host.h>
extern "C" int vx_printf(const char* f,...){va_list a;va_start(a,f);int n=vprintf(f,a);va_end(a);return n;}
int main(){
 void* p=mmap((void*)(uintptr_t)VX_DFV_CONFIG_ADDR,4096,PROT_READ|PROT_WRITE,
             MAP_FIXED_NOREPLACE|MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(p!=MAP_FAILED);
 auto* config=(vx_dfv_config_t*)p;
 config->magic=VX_DFV_CONFIG_MAGIC;config->version=1;config->sequence=7;
 config->values[0]=1;config->values[2]=0xabcdef00;config->values[3]=0xe000;
 config->values[9]=0x1234;config->values[12]=15;
 assert(vx_dfv_apply_host_config()==0);
 assert(writes==16 && first_addr==0x7c0 && first_value==0 && last_addr==0x7c0);
 assert(config->applied==7);
 for(unsigned i=0;i<15;i++)assert(regs[0x7c0+i]==config->values[i] && config->readback[i]==config->values[i]);
 config->sequence=8;config->values[9]=0;assert(vx_dfv_apply_host_config()==0 && config->readback[9]==0);
 config->values[9]=9;auto before=writes;assert(vx_dfv_apply_host_config()!=0 && writes==before);
 config->magic=0;assert(vx_dfv_apply_host_config()!=0);
 munmap(p,4096);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            subprocess.run([sys.executable,str(ROOT/'hw/scripts/gen_config.py'),'-i',
                            str(ROOT/'hw/rtl/VX_config.vh'),'-o',str(work/'VX_config.h')],check=True)
            (work/'helper.cpp').write_text(source)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-DXLEN_32',
                            '-I'+str(work),'-I'+str(ROOT/'sim/common'),'-I'+str(ROOT/'kernel/include'),
                            str(work/'helper.cpp'),'-o',str(work/'helper')],check=True)
            subprocess.run([str(work/'helper')],check=True,capture_output=True)

if __name__=='__main__':unittest.main()
