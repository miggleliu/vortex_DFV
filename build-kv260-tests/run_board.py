#!/usr/bin/env python3
"""Run cross-built upstream regression programs on the KV260 ARM CPU."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'tests'))
from dfv_csr import CollisionReportError, parse_observation_windows
from run_dfv_combos import extract_perf, window_metrics, target_coverage, selected_windows

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle',type=Path,default=HERE)
    ap.add_argument('--apps',nargs='+')
    ap.add_argument('--out',type=Path,default=Path('board-results'))
    a=ap.parse_args()
    bundle=a.bundle.resolve()
    cfg=json.loads((bundle/'bundle.json').read_text())
    if platform.machine() not in ['aarch64','arm64'] or os.geteuid()!=0:
        ap.error('Run on KV260 Ubuntu using sudo; this is an ARM hardware runner')
    if a.out.exists(): ap.error('--out must be a new directory')
    cases=[c for c in cfg['cases'] if not a.apps or c['app'] in a.apps]
    if not cases: ap.error('No matching built cases')
    if a.apps and set(a.apps)-{c['app'] for c in cases}: ap.error('Requested app is not in this bundle')
    # Check every selected image before starting hardware.
    for c in cases:
        image=bundle/c['directory']/'kernel.vxbin'
        if hashlib.sha256(image.read_bytes()).hexdigest()!=c['sha256']:
            ap.error('Kernel hash mismatch: '+str(image))
    a.out.mkdir(parents=True)
    env=os.environ.copy()
    env['VORTEX_DRIVER']='kv260'
    env['LD_LIBRARY_PATH']=str(bundle/'runtime')+':'+env.get('LD_LIBRARY_PATH','')
    # Profiling class 0 matches the verified runner, preserving CSR semantics.
    env['VORTEX_PROFILING']='0'
    env.pop('VORTEX_KV260_MEM',None)
    rows=[]
    failed=False
    for c in cases:
        directory=bundle/c['directory']
        path=a.out/f'{c["app"]}__{c["combo"]}.log'
        print(f'Running {c["app"]}/{c["combo"]} on KV260',flush=True)
        with path.open('w') as log:
            rc=subprocess.run([str(directory/c['app']),*c['args']],cwd=directory,env=env,
                              stdout=log,stderr=subprocess.STDOUT).returncode
        lines=path.read_text(errors='replace').splitlines()
        row={'app':c['app'],'combo':c['combo'],'returncode':rc,'upstream_commit':cfg['upstream_commit'],
             'dtype':c['dtype'],'status':'PASS' if rc==0 else f'FAIL(rc={rc})'}
        row['instrs'],row['cycles'],row['IPC']=extract_perf(lines)
        try:
            observations=parse_observation_windows(lines)
            row['coverage']=target_coverage(observations,cfg['type'],c['combo'])
            metrics,intervals,thresholds=window_metrics(observations,cfg['type'])
            row.update(metrics)
            row.update({k+'_window_cycles':v for k,v in intervals.items()})
            row.update({k+'_threshold':v for k,v in thresholds.items()})
            (path.with_suffix('.csr.json')).write_text(json.dumps([w.as_dict() for w in observations],indent=2)+'\n')
            for w in selected_windows(observations,cfg['type']):
                value=f'natural={w.natural} dfv={w.dfv} total={w.total}' if cfg['type']==1 else f'events={w.count0} threshold={w.threshold}'
                print(f'  {w.prefix}: {value}, window_cycles={w.cycles}')
        except CollisionReportError as exc:
            row.update(status='CSR_ERROR' if rc==0 else row['status'],csr_error=str(exc),coverage='unavailable')
        if row['instrs']<0 or row['cycles']<=0:
            row['status']='PERF_ERROR' if row['status']=='PASS' else row['status']
        print(f'  {row["status"]} coverage={row["coverage"]}; full log: {path}',flush=True)
        rows.append(row)
        failed |= row['status']!='PASS'
        # A hardware fault/time-out stops the experiment; do not compound it.
        if rc: break
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with (a.out/'dfv_perf.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    (a.out/'provenance.json').write_text(json.dumps(cfg,indent=2)+'\n')
    print('PASS validates host result comparison and CSR reports; coverage reports target events.')
    return int(failed)

if __name__=='__main__': raise SystemExit(main())
