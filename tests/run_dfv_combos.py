#!/usr/bin/env python3
"""Run DFV stall injection tests across all regression apps.

For each app, patches kernel.cpp to enable/disable stall types,
runs the RTL simulation, preserves GPU CSR reports while trimming run.log,
extracts PERF and type-specific metrics, and saves logs + CSV.
"""

import argparse
from contextlib import contextmanager
import csv
import json
import os
import re
import shutil
import subprocess

from dfv_csr import (CollisionReportError, FRAME_MARKER, SAMPLE_MARKER,
                     parse_observation_windows)

VORTEX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REG_DIR = os.path.join(VORTEX_ROOT, "tests", "regression")
BUILD_DIR = os.path.join(VORTEX_ROOT, "build")
RUN_LOG = os.path.join(BUILD_DIR, "run.log")
LOG_BASE = os.path.join(VORTEX_ROOT, "logs")

# =============================================================================
# Separate indexed CSR families supply every test type:
# 1: all collision monitors; 2: global throttle events; 3: per-bank throttle.
# No RTL $display message is used as a measurement.
DFV_TEST_TYPE = 1

STALL_TYPES = [
    ("icache",       "VX_CSR_DFV_ICACHE_FILL_REQ_STALL"),
    ("dcache",       "VX_CSR_DFV_DCACHE_FILL_REQ_STALL"),
    ("fill",         "VX_CSR_DFV_DCACHE_FILL_RSP_STALL"),
    ("dcache_core",  "VX_CSR_DFV_DCACHE_CORE_REQ_STALL"),
]
# VX_CSR_DFV_WRITEBACK_STALL is excluded from STALL_TYPES and always forced to 0.

# Defaults matching the normalized kernel.cpp baseline
DEFAULT_SET_THRESHOLD      = 0xE000
DEFAULT_RELEASE_THRESHOLD  = 0xE000
DEFAULT_RELEASE_DELAY      = 0x1000
DEFAULT_RELEASE_FOREVER    = 0
DEFAULT_THROTTLE_THRESHOLD = 0x2ED1 #0x1800
DEFAULT_FILL_BANK_MASK     = 0xFFFF
# Type 3 per-bank throttle threshold (separate CSR, 0x7CE) -- NOT yet calibrated.
# This is the RTL reset placeholder value; calibrate against a stalls=none
# baseline with tests/analyze_bank_throttle.py before trusting Type-3 results.
DEFAULT_BANK_THROTTLE_THRESHOLD = 0x1800 #0x0100

# Each combo dict supports:
#   "stalls"                – tuple of bools, one per STALL_TYPES entry (required)
#   "label"                 – short name used in log filenames and CSV (optional)
#   "set_threshold"         – VX_CSR_DFV_SET_THRESHOLD              (default 240)
#   "release_threshold"     – VX_CSR_DFV_RELEASE_THRESHOLD          (default 65504)
#   "release_delay"         – VX_CSR_DFV_RELEASE_DELAY              (default 0x1000)
#   "release_forever"       – VX_CSR_DFV_RELEASE_FOREVER            (default 1)
#   "throttle_threshold"    – VX_CSR_DFV_THROTTLE_THRESHOLD         (default 0x1800, Type 2 global full-freeze)
#   "bank_throttle_threshold" – VX_CSR_DFV_BANK_THROTTLE_THRESHOLD  (default 0x0032, Type 3 per-bank, uncalibrated)

# Type 1: observe every cache bank and the I-cache/D-cache arbiter through CSRs.
# Existing injection settings are retained; a zero event count is valid, not coverage.
COMBOS_TYPE1 = [
    #{"stalls": (False, False, False, False), "label": "none",
    # "release_threshold": 0xF000, "release_forever": 0},
    {"stalls": (True,  True,  False, False), "label": "icache_fill_req_N_dcache_fill_req",
     "release_threshold": 0xF000, "release_forever": 0},
    {"stalls": (False, False, True,  True),  "label": "dcache_fill_rsp_N_dcache_core_req",
     "release_threshold": 0xF000, "release_forever": 0},
]

# Type 2: watch throttle counter — did the pipeline fully freeze?
# release_forever=0: after a long stall releases, LFSR1 can SET the gate again
# later in the same run (independent of the earlier release), producing
# MULTIPLE long stalls per test instead of one held open forever. Each
# individual stall is still reliably long (release_threshold near max, 0xFFF6)
# so it independently crosses the throttle threshold before releasing.
# extract_throttle_reached() already counts every occurrence, not just the
# first, so no script-side change was needed beyond this CSR flip.
COMBOS_TYPE2 = [
    {"stalls": (False, False, False, False), "label": "none",
    "release_threshold": 0xFFF6, "release_forever": 0, "set_threshold": 0x1000},
    {"stalls": (True,  False, False, False), "label": "icache_fill_req_backpressure",
    "release_threshold": 0xFFF6, "release_forever": 0, "set_threshold": 0x1000},
    {"stalls": (False, False, True,  False), "label": "dcache_fill_rsp_backpressure",
    "release_threshold": 0xFFF6, "release_forever": 0, "set_threshold": 0x1000},
]

# Type 3: asymmetric slowdown — stall only bank 0, let other banks proceed.
# release_forever=0: stall cycles on/off via LFSR2 to create intermittent bank 0 pressure.
COMBOS_TYPE3 = [
    {"stalls": (False, False, False, False), "label": "none",
     "release_threshold": 0xFFF4, "release_forever": 0, "set_threshold": 0x0200},
    {"stalls": (False, False, True,  False), "label": "dcache_fill_rsp_bank0_slowdown",
     "fill_bank_mask": 0x1, "release_threshold": 0xFFF4, "release_forever": 0, "set_threshold": 0x1000},
    {"stalls": (False, False, True,  False), "label": "dcache_fill_rsp_bank01_slowdown",
     "fill_bank_mask": 0x3, "release_threshold": 0xFFF4, "release_forever": 0, "set_threshold": 0x1000},
    {"stalls": (False, False, True,  False), "label": "dcache_fill_rsp_bank012_slowdown",
     "fill_bank_mask": 0x7, "release_threshold": 0xFFF4, "release_forever": 0, "set_threshold": 0x1000},
]

COMBOS = {1: COMBOS_TYPE1, 2: COMBOS_TYPE2, 3: COMBOS_TYPE3}[DFV_TEST_TYPE]

# Per-app arguments (size flags; -d is appended to enable DFV path)
TEST_APPS = {
    #"printf":     "-n4",
    #"relu":       "-n64",
    #"sgemm":      "-n8",
    #"sgemm2":     "-n16",
    ##"sgemm_tcu":  "-m8 -n8",
    #"sgemv":      "-m16 -n16",
    #"sort":       "-n4",
    #"stencil3d":  "-n4",
    #"vecadd":     "-n64",
    #"basic":      "-n16",
    #"conv3":      "-n8",
    "cta":        "-x2 -y2 -z2 -a2 -b2 -c2",
    "demo":       "-n16",
    #"diverge":    "-n16",
    #"dogfood":    "-n16",
    #"dotproduct": "-n64",
    #"dropout":    "-n64",
    #"fence":      "-n16",
    #"io_addr":    "-n16",
    #"madmax":     "-n4",
    #"mstress":    "-n16",
}


# =============================================================================
# Kernel patching
# =============================================================================

def kernel_path(app):
    return os.path.join(REG_DIR, app, "kernel.cpp")


def patch_kernel(app, combo_dict):
    """Rewrite CSR values in kernel.cpp according to combo_dict."""
    path = kernel_path(app)
    with open(path, "r") as f:
        src = f.read()

    stalls     = combo_dict.get("stalls", (False,) * len(STALL_TYPES))
    set_thr    = combo_dict.get("set_threshold",      DEFAULT_SET_THRESHOLD)
    rel_thr    = combo_dict.get("release_threshold",  DEFAULT_RELEASE_THRESHOLD)
    rel_dly    = combo_dict.get("release_delay",      DEFAULT_RELEASE_DELAY)
    rel_forev  = combo_dict.get("release_forever",    DEFAULT_RELEASE_FOREVER)
    thr_thresh = combo_dict.get("throttle_threshold", DEFAULT_THROTTLE_THRESHOLD)
    bank_mask  = combo_dict.get("fill_bank_mask",     DEFAULT_FILL_BANK_MASK)
    bank_thr_thresh = combo_dict.get("bank_throttle_threshold", DEFAULT_BANK_THROTTLE_THRESHOLD)

    for (_, csr_macro), enabled in zip(STALL_TYPES, stalls):
        pattern = rf"(csr_write\({csr_macro},\s*)\d(\))"
        src = re.sub(pattern, rf"\g<1>{int(enabled)}\2", src)

    # A genuine OFF baseline must also clear the global enable; otherwise the
    # unchanged RTL counter classifies natural alignments as DFV events.
    src, count = re.subn(r"(csr_write\(VX_CSR_DFV_CTRL,\s*)[01](\))",
                         rf"\g<1>{int(any(stalls))}\2", src, count=1)
    if count != 1:
        raise ValueError(f"Expected one DFV setup control in {path}")

    for csr_name, value in [
        ("VX_CSR_DFV_SET_THRESHOLD",      set_thr),
        ("VX_CSR_DFV_RELEASE_THRESHOLD",  rel_thr),
        ("VX_CSR_DFV_RELEASE_DELAY",      rel_dly),
        ("VX_CSR_DFV_RELEASE_FOREVER",    rel_forev),
        ("VX_CSR_DFV_THROTTLE_THRESHOLD", thr_thresh),
        ("VX_CSR_DFV_FILL_BANK_MASK",     bank_mask),
        ("VX_CSR_DFV_BANK_THROTTLE_THRESHOLD", bank_thr_thresh),
        ("VX_CSR_DFV_WRITEBACK_STALL",    0),          # always disabled
    ]:
        pattern = rf"(csr_write\({csr_name},\s*)[^)]+(\))"
        src = re.sub(pattern, rf"\g<1>{value}\2", src)

    with open(path, "w") as f:
        f.write(src)


@contextmanager
def preserve_kernel(app):
    """Restore the user's exact original source, including on failed runs/Ctrl+C."""
    path = kernel_path(app)
    with open(path, "rb") as stream:
        original = stream.read()
    try:
        yield
    finally:
        with open(path, "wb") as stream:
            stream.write(original)



# =============================================================================
# Raw CSR decoding and result selection
# =============================================================================

def extract_perf(lines):
    for line in lines:
        match = re.search(r'PERF:\s*instrs=(\d+),\s*cycles=(\d+),\s*IPC=([\d.]+)', line)
        if match:
            return int(match[1]), int(match[2]), float(match[3])
    return -1, -1, -1.0


def selected_windows(windows, test_type):
    if test_type == 1:
        return [w for w in windows if w.family == 'collision']
    return [w for w in windows if w.family == 'throttle'
            and w.domain == ('global' if test_type == 2 else 'bank')]


def window_metrics(windows, test_type):
    selected = selected_windows(windows, test_type)
    if not selected:
        raise CollisionReportError('No hardware monitors for the selected DFV type')
    metrics, intervals, thresholds = {}, {}, {}
    for w in selected:
        kinds = ('natural', 'dfv', 'total') if test_type == 1 else ('events',)
        for kind in kinds:
            metrics[f'{w.prefix}_{kind}'] = getattr(w, kind) if kind != 'events' else w.count0
        intervals[w.prefix] = w.cycles
        if w.family == 'throttle':
            thresholds[w.prefix] = w.threshold
    return metrics, intervals, thresholds


def trim_log(path, test_type=1, keep=200):
    with open(path, errors='replace') as stream:
        lines = stream.readlines()
    error, windows = '', []
    try:
        windows = parse_observation_windows(lines)
        metrics, intervals, thresholds = window_metrics(windows, test_type)
    except CollisionReportError as exc:
        error = str(exc)
        metrics, intervals, thresholds = {}, {}, {}
    raw = lambda line: any(marker in line for marker in (FRAME_MARKER, SAMPLE_MARKER, 'DFV_CSR_ERROR'))
    tail = [line for line in lines[-keep:] if not raw(line)]
    tail += [line for line in lines if raw(line)]
    if error:
        tail.append(f'CSR_MEASUREMENT_ERROR: {error}\n')
    with open(path, 'w') as stream:
        stream.writelines(tail)
    mismatch = any(line.lower().startswith('error at result') or line.startswith('SIGMISMATCH:') for line in lines)
    return tail, windows, metrics, intervals, thresholds, error, mismatch


def combo_tag(combo):
    return combo.get('label') or '_'.join(name for (name, _), on in zip(STALL_TYPES, combo['stalls']) if on) or 'none'


def target_coverage(windows, test_type, tag):
    candidates = selected_windows(windows, test_type)
    if test_type == 1:
        domain = None if tag == 'none' else ('l1arb' if tag == 'icache_fill_req_N_dcache_fill_req' else 'dcache')
        candidates = [w for w in candidates if domain is None or w.domain == domain]
        if not candidates:
            raise CollisionReportError(f'Missing intended {domain} collision monitor')
    return 'HIT' if any(w.total if test_type == 1 else w.count0 for w in candidates) else 'ZERO_EVENTS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--type', type=int, choices=(1, 2, 3), default=DFV_TEST_TYPE,
                        help='1 collision CSRs; 2 global throttle CSRs; 3 per-bank throttle CSRs')
    parser.add_argument('--apps', nargs='+', choices=sorted(TEST_APPS), default=list(TEST_APPS))
    parser.add_argument('--parse-log', help='Decode both CSR families without a Vortex build/SDK')
    args = parser.parse_args()
    if args.parse_log:
        try:
            with open(args.parse_log, errors='replace') as stream:
                windows = parse_observation_windows(stream)
            print(json.dumps([w.as_dict() for w in windows], indent=2))
        except (OSError, CollisionReportError) as exc:
            parser.exit(1, f'CSR_ERROR: {exc}\n')
        return 0
    if not os.path.isdir(BUILD_DIR):
        parser.error(f'Configured Vortex build directory is missing: {BUILD_DIR}')
    combos = {1: COMBOS_TYPE1, 2: COMBOS_TYPE2, 3: COMBOS_TYPE3}[args.type]
    result_dir = os.path.join(LOG_BASE, f'dfv_type{args.type}')
    rows = []
    for app in args.apps:
        directory = os.path.join(result_dir, app)
        os.makedirs(directory, exist_ok=True)
        with preserve_kernel(app):
            for combo in combos:
                tag = combo_tag(combo)
                print(f'\nRunning {app}/{tag}, DFV type {args.type}', flush=True)
                patch_kernel(app, combo)
                command = ['./ci/blackbox.sh', '--driver=rtlsim', f'--app={app}', f'--args={TEST_APPS[app]} -d']
                with open(RUN_LOG, 'w') as stream:
                    result = subprocess.run(command, cwd=BUILD_DIR, stdout=stream, stderr=subprocess.STDOUT)
                tail, windows, metrics, intervals, thresholds, error, mismatch = trim_log(RUN_LOG, args.type)
                instrs, cycles, ipc = extract_perf(tail)
                coverage = ''
                if not error:
                    try:
                        coverage = target_coverage(windows, args.type, tag)
                    except CollisionReportError as exc:
                        error = str(exc)
                status = ('SIGMISMATCH' if mismatch else f'FAIL(rc={result.returncode})' if result.returncode
                          else 'CSR_ERROR' if error else 'PASS')
                path = os.path.join(directory, f'run_{tag}.log')
                shutil.move(RUN_LOG, path)
                with open(path + '.csr.json', 'w') as stream:
                    json.dump({'status': status, 'csr_error': error, 'coverage': coverage,
                               'observations': [w.as_dict() for w in windows]}, stream, indent=2)
                row = dict(app=app, stall_combo=tag, instrs=instrs, cycles=cycles, IPC=ipc,
                           status=status, coverage=coverage, csr_error=error)
                for key, value in metrics.items():
                    row[key] = value
                    prefix = key.rsplit('_', 1)[0]
                    row[key + '_rate'] = value / intervals[prefix]
                row.update({k + '_window_cycles': v for k, v in intervals.items()})
                row.update({k + '_threshold': v for k, v in thresholds.items()})
                rows.append(row)
                print(f'  {status} coverage={coverage or "unavailable"}, instrs={instrs}, cycles={cycles}, IPC={ipc:.4f}')
                for w in selected_windows(windows, args.type):
                    value = f'natural={w.natural} dfv={w.dfv} total={w.total}' if args.type == 1 else f'events={w.count0} threshold={w.threshold}'
                    print(f'  {w.prefix}: {value}, window_cycles={w.cycles}')
                if error:
                    print(f'  CSR_ERROR: {error}')
    if args.type == 3:
        baselines = {row['app']: row['IPC'] for row in rows if row['stall_combo'] == 'none' and row['IPC'] > 0 and row['status'] == 'PASS'}
        for row in rows:
            baseline = baselines.get(row['app'])
            row['ipc_drop_pct'] = 100 * (baseline - row['IPC']) / baseline if baseline and row['IPC'] >= 0 else ''
    os.makedirs(result_dir, exist_ok=True)
    path = os.path.join(result_dir, 'dfv_perf.csv')
    fixed = ['app', 'stall_combo', 'instrs', 'cycles', 'IPC', 'status', 'coverage', 'csr_error']
    columns = fixed + sorted({key for row in rows for key in row} - set(fixed))
    with open(path, 'w', newline='') as stream:
        writer = csv.DictWriter(stream, columns, restval='')
        writer.writeheader()
        writer.writerows(rows)
    print(f'\nCSV: {path}')
    print('PASS checks functional completion and valid CSR data; HIT/ZERO_EVENTS reports event coverage.')
    print('All banks/units are separate. Reader cores may share physical monitors; do not sum across cores.')
    return int(any(row['status'] != 'PASS' for row in rows))


if __name__ == '__main__':
    raise SystemExit(main())
