#!/usr/bin/env python3
"""Contracts for all-monitor collision/throttle reports and combo execution."""
from contextlib import redirect_stdout
import csv
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import dfv_csr as csr
import run_dfv_combos as runner

COLL_DESCS = [0, 1, 0x01000000, 0x02000000, 0x03000000, 0x04000000]
THR_DESCS = [65535, 0x01000000, 0x01000001]


def report(cores=1, zero=False):
    lines=[]
    for core in range(cores):
        for phase in ('BEGIN','END'):
            for family,descs in [('collision',COLL_DESCS),('throttle',THR_DESCS)]:
                lo = (100 if family=='collision' else 101) + (0 if phase=='BEGIN' else (200 if family=='collision' else 300))
                lines.append(f'#0: DFV_CSR_FRAME v=2 core={core} cores={cores} family={family} phase={phase} info=0x{csr.IDS[family]:08x} count={len(descs)} valid=1 cycle_lo={lo} cycle_hi=0\n')
                for index,desc in enumerate(descs):
                    a,b=(5000+index,6000+index) if family=='collision' else (10+index,0)
                    if phase=='END' and not zero:
                        a+=5;b+=11 if family=='collision' else 0
                    threshold=0 if family=='collision' else 50
                    lines.append(f'#0: DFV_CSR_SAMPLE v=2 core={core} family={family} phase={phase} index={index} desc=0x{desc:08x} owner=0 flags=9 count0={a} count1={b} threshold={threshold}\n')
    return lines


class ParserTests(unittest.TestCase):
    def test_every_domain_bank_and_throttle_kind(self):
        windows=csr.parse_observation_windows(report())
        self.assertEqual(len(windows),9)
        self.assertEqual({w.domain for w in windows},{'dcache','icache','l1arb','l2','l3','global','bank'})
        self.assertEqual([w.bank for w in windows if w.domain=='dcache'],[0,1])
        self.assertTrue(all((w.natural,w.dfv,w.total,w.cycles)==(5,11,16,200) for w in windows if w.family=='collision'))
        self.assertTrue(all(w.count0==5 and w.count1==0 and w.cycles==300 for w in windows if w.family=='throttle'))

    def test_genuine_zero_and_counts_beyond_old_12_bit_limit(self):
        self.assertTrue(all(w.total==0 for w in csr.parse_observation_windows(report(zero=True))))
        self.assertEqual(csr.parse_observation_windows(report())[0].total,16)

    def test_calibration_analyzer_uses_csr_data(self):
        import analyze_bank_throttle as analyzer
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'run.log';path.write_text(''.join(report()))
            with redirect_stdout(io.StringIO()): windows=analyzer.summarize(path)
            self.assertEqual(len(windows),3)
            self.assertEqual({w.count0 for w in windows},{5})

    def test_shared_reader_cores_stay_separate(self):
        windows=csr.parse_observation_windows(report(cores=2))
        self.assertEqual(len(windows),18)
        self.assertEqual({w.core for w in windows},{0,1})
        self.assertEqual(len({w.prefix for w in windows}),18)

    def test_legacy_logs_never_supply_metrics(self):
        with self.assertRaises(csr.CollisionReportError):
            csr.parse_observation_windows(['DFV_COLLISION_DFV edge_count=999\n','DFV_THROTTLE_REACHED threshold=50\n'])
        lines=report()+['DFV_COLLISION_DFV edge_count=999999\n']
        self.assertEqual(csr.parse_observation_windows(lines)[0].total,16)

    def test_invalid_partial_or_saturated(self):
        mutations=[
            lambda x:x[:-1],lambda x:x+[x[1]],
            lambda x:[v for v in x if not ('family=throttle' in v)],
            lambda x:[v.replace('info=0x434f4c33','info=0x434f4c32') for v in x],
            lambda x:[v.replace('valid=1','valid=0') for v in x],
            lambda x:[v.replace('flags=9','flags=11') for v in x],
            lambda x:[v.replace('flags=9','flags=1') for v in x],
            lambda x:[v.replace('count0=5000','count0=4294967295') for v in x],
            lambda x:[v.replace('cycle_hi=0','cycle_hi=4096') for v in x],
            lambda x:[v.replace('index=5','index=7') for v in x],
            lambda x:[v.replace('cores=1','cores=2') for v in x],
            lambda x:[v.replace('cycle_lo=300','cycle_lo=100') for v in x],
            lambda x:[v.replace('count0=5005','count0=1') for v in x],
            lambda x:[v.replace('desc=0x04000000','desc=0x03000000') for v in x],
            lambda x:[v.replace('threshold=50','threshold=51') if 'phase=END' in v else v for v in x],
            lambda x:[v.replace('owner=0','owner=2') if 'phase=END' in v else v for v in x],
            lambda x:x+['DFV_CSR_ERROR invalid\n'],
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate),self.assertRaises(csr.CollisionReportError):
                csr.parse_observation_windows(mutate(report()))

    def test_both_family_intervals_and_rates(self):
        windows=csr.parse_observation_windows(report())
        for test_type,cycle in [(1,200),(2,300),(3,300)]:
            metrics,intervals,thresholds=runner.window_metrics(windows,test_type)
            self.assertTrue(metrics)
            self.assertTrue(all(v==cycle for v in intervals.values()))
            self.assertEqual(bool(thresholds),test_type!=1)
        self.assertEqual(runner.target_coverage(windows,1,'icache_fill_req_N_dcache_fill_req'),'HIT')
        self.assertEqual(runner.target_coverage(csr.parse_observation_windows(report(zero=True)),1,'none'),'ZERO_EVENTS')

    def test_trim_preserves_all_raw_frames_and_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'run.log'
            path.write_text(''.join(report())+'noise\n'*250+'PERF: instrs=900, cycles=900000, IPC=0.001\n')
            first=runner.trim_log(str(path),3,keep=3)
            second=runner.trim_log(str(path),3,keep=3)
            self.assertFalse(first[5]);self.assertEqual(first[2],second[2])
            self.assertEqual(len(csr.parse_observation_windows(path.read_text().splitlines())),9)


class RunnerTests(unittest.TestCase):
    def fixture(self,test_type=1,valid=True,interrupted=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'reg/vecadd').mkdir(parents=True);(root/'build').mkdir()
            kernel=root/'reg/vecadd/kernel.cpp'
            original='csr_write(VX_CSR_DFV_CTRL, 1);\n'+'\n'.join(f'csr_write({name}, 0);' for _,name in runner.STALL_TYPES)+'\ncsr_write(VX_CSR_DFV_CTRL, 0);\n'
            kernel.write_text(original);modes=[]
            def launch(command,**kwargs):
                modes.append(kernel.read_text().splitlines()[0])
                if interrupted: raise KeyboardInterrupt
                kwargs['stdout'].write((''.join(report()) if valid else 'legacy RTL display only\n')+'PERF: instrs=900, cycles=900000, IPC=0.001\n')
                return subprocess.CompletedProcess(command,0)
            with patch.multiple(runner,REG_DIR=str(root/'reg'),BUILD_DIR=str(root/'build'),RUN_LOG=str(root/'build/run.log'),LOG_BASE=str(root/'logs')),patch.object(runner.subprocess,'run',side_effect=launch),patch.object(sys,'argv',['run_dfv_combos.py','--type',str(test_type),'--apps','vecadd']),redirect_stdout(io.StringIO()):
                if interrupted:
                    with self.assertRaises(KeyboardInterrupt):runner.main()
                    self.assertEqual(kernel.read_text(),original);return
                rc=runner.main()
            self.assertEqual(kernel.read_text(),original)
            self.assertIn(', 0);',modes[0]);self.assertIn(', 1);',modes[1])
            with (root/'logs'/f'dfv_type{test_type}'/'dfv_perf.csv').open() as stream:rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),4 if test_type==3 else 3)
            self.assertEqual(rc,0 if valid else 1)
            self.assertTrue(all(row['status']==('PASS' if valid else 'CSR_ERROR') for row in rows))
            if valid:
                key='core0_collision_dcache_owner0_unit0_bank1_total' if test_type==1 else ('core0_throttle_global_owner0_unit0_events' if test_type==2 else 'core0_throttle_bank_owner0_unit0_bank1_events')
                self.assertEqual(float(rows[0][key+'_rate']),16/200 if test_type==1 else 5/300)
                self.assertEqual(rows[0]['coverage'],'HIT')

    def test_all_three_types_use_their_csr_family(self):
        for test_type in (1,2,3):
            with self.subTest(test_type=test_type):self.fixture(test_type)

    def test_missing_csr_does_not_pass(self):
        for test_type in (1,2,3):self.fixture(test_type,valid=False)

    def test_interruption_restores_source(self):
        self.fixture(interrupted=True)


class ProducerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'),'native compiler unavailable')
    def test_actual_cpp_producer_matches_both_family_parser(self):
        root=Path(__file__).resolve().parents[1]
        source=r'''
#include <cstdarg>
#include <cstdio>
#define __VX_INTRINSICS_H__
static inline unsigned vx_core_id(){return 0;}
static inline unsigned vx_num_cores(){return 1;}
#include <vx_dfv_collision_report.h>
extern "C" int vx_printf(const char* f,...){va_list a;va_start(a,f);int n=std::vprintf(f,a);va_end(a);return n;}
int main(int argc,char**){
 vx_dfv_sample_t ca[2]={{0,0,9,5000,6000,0},{0x02000000,0,9,8,9,0}};
 vx_dfv_sample_t cb[2]={{0,0,9,5005,6011,0},{0x02000000,0,9,10,13,0}};
 vx_dfv_sample_t ta[1]={{65535,0,9,10,0,50}},tb[1]={{65535,0,9,15,0,50}};
 vx_dfv_snapshot_t a={{VX_DFV_COLLISION_ID,2,1,100,0,ca},{VX_DFV_THROTTLE_ID,1,1,101,0,ta}};
 vx_dfv_snapshot_t b={{VX_DFV_COLLISION_ID,2,1,300,0,cb},{VX_DFV_THROTTLE_ID,1,1,401,0,tb}};
 if(argc>1){tb[0].count0=0xffffffff;tb[0].status=11;}
 int result=vx_dfv_report(&a,&b);return ((result!=0)!=(argc>1))?42:0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp)
            subprocess.run([sys.executable,str(root/'hw/scripts/gen_config.py'),'-i',str(root/'hw/rtl/VX_types.vh'),'-o',str(work/'VX_types.h')],check=True,capture_output=True)
            (work/'report.cpp').write_text(source)
            subprocess.run(['c++','-std=gnu++17','-Wall','-Wextra','-Werror','-I'+str(work),'-I'+str(root/'kernel/include'),str(work/'report.cpp'),'-o',str(work/'report')],check=True,capture_output=True)
            valid=subprocess.run([str(work/'report')],check=True,capture_output=True,text=True)
            windows=csr.parse_observation_windows(valid.stdout.splitlines())
            self.assertEqual([w.total for w in windows if w.family=='collision'],[16,6])
            self.assertEqual([w.cycles for w in windows],[200,200,300])
            bad=subprocess.run([str(work/'report'),'bad'],check=True,capture_output=True,text=True)
            with self.assertRaises(csr.CollisionReportError):csr.parse_observation_windows(bad.stdout.splitlines())


if __name__=='__main__':unittest.main()
