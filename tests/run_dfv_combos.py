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

from dfv_csr import (CollisionReportError, MARKER, parse_collision_windows,
                     collision_counts, collision_cycles)

VORTEX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REG_DIR = os.path.join(VORTEX_ROOT, "tests", "regression")
BUILD_DIR = os.path.join(VORTEX_ROOT, "build")
RUN_LOG = os.path.join(BUILD_DIR, "run.log")
LOG_BASE = os.path.join(VORTEX_ROOT, "logs")

# =============================================================================
# DFV TEST TYPE
#   1 = Arbitration race condition
#         Reports GPU-CSR snapshots of local D-cache unit 0 / bank 0.
#         Rates use the captured core-cycle interval, not whole-run PERF cycles.
#   2 = Downstream backpressure / upstream throttle
#         Reports whether the throttle counter ever reached its threshold.
#         Use fill stall (all banks); DFV_THROTTLE_REACHED $display lines are the signal.
#   3 = Asymmetric slowdown on multi-lane resources
#         Fill stall enabled on bank 0 only; other banks proceed normally.
#         Reports IPC reduction % vs. none baseline per app, AND how many
#         times each bank's per-bank throttle counter reached its (smaller,
#         separately-calibrated) threshold -- DFV_THROTTLE_REACHED $display
#         lines from the g_dfv_bank_throttle[N] instances are the signal.
#         release_forever disabled so stall cycles on/off via LFSR2.
# =============================================================================
DFV_TEST_TYPE = 1

STALL_TYPES = [
    ("icache",       "VX_CSR_DFV_ICACHE_FILL_REQ_STALL"),
    ("dcache",       "VX_CSR_DFV_DCACHE_FILL_REQ_STALL"),
    ("fill",         "VX_CSR_DFV_DCACHE_FILL_RSP_STALL"),
    ("dcache_core",  "VX_CSR_DFV_DCACHE_CORE_REQ_STALL"),
]
# VX_CSR_DFV_WRITEBACK_STALL is excluded from STALL_TYPES and always forced to 0.

# Defaults matching the normalized kernel.cpp baseline
DEFAULT_SET_THRESHOLD      = 0x00F0
DEFAULT_RELEASE_THRESHOLD  = 0xF000
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

# Type 1: observe the local D-cache unit-0/bank-0 collision CSR interface.
# L1 arbiter and other-bank counts are unavailable through the COL2 CSR ABI.
# Use faster release (threshold=240) so stalls cycle and produce many collision events
COMBOS_TYPE1 = [
    {"stalls": (False, False, False, False), "label": "none",
     "release_threshold": 0xF000, "release_forever": 0},
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
    "release_threshold": 0xFFF6, "release_forever": 0, "set_threshold": 0x0010},
    {"stalls": (True,  False, False, False), "label": "icache_fill_req_backpressure",
    "release_threshold": 0xFFF6, "release_forever": 0, "set_threshold": 0x0010},
    {"stalls": (False, False, True,  False), "label": "dcache_fill_rsp_backpressure",
    "release_threshold": 0xFFF6, "release_forever": 0, "set_threshold": 0x0010},
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
    "relu":       "-n64",
    "sgemm":      "-n8",
    "sgemm2":     "-n16",
    #"sgemm_tcu":  "-m8 -n8",
    "sgemv":      "-m16 -n16",
    #"sort":       "-n16",
    #"stencil3d":  "-n4",
    "vecadd":     "-n64",
    #"basic":      "-n16",
    "conv3":      "-n8",
    "cta":        "-x2 -y2 -z2 -a2 -b2 -c2",
    "demo":       "-n16",
    #"diverge":    "-n16",
    #"dogfood":    "-n16",
    "dotproduct": "-n64",
    "dropout":    "-n64",
    "fence":      "-n16",
    "io_addr":    "-n16",
    "madmax":     "-n4",
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
# Log parsing
# =============================================================================

def extract_collisions(lines, start_line=None):
    """Read only GPU CSR snapshots. Log-position markers do not define this ROI.

    start_line is retained for callers of the old function; capture boundaries
    now come from the CSR values themselves. Missing snapshots raise an error.
    """
    return collision_counts(parse_collision_windows(lines))


def find_test_start_line(lines):
    """Return the index of the LAST 'DFV_TEST_START' line, or None if not
    found (shouldn't happen for any app with the standard DFV setup block --
    every combo, including 'none', prints this via kernel.cpp's vx_printf
    right after the CSR setup block, right before vx_spawn_threads).

    Everything at or before this line is device/kernel setup (boot, DCR
    programming, the CSR write sequence itself) -- not part of the actual
    test sequence, and should not be counted. Using line POSITION rather
    than a $time value: vx_printf output has no RTL timestamp prefix, but it
    lands in the same run.log stream as the RTL $display lines in real
    execution order, so position-in-file is a valid ordering proxy. Using
    the LAST occurrence in case a line appears more than once for some
    reason (defensive; the standard kernel.cpp block prints it exactly once).
    """
    start = None
    for i, line in enumerate(lines):
        if "DFV_TEST_START" in line:
            start = i
    return start


def extract_throttle_reached(lines, start_line=None):
    """Count how many times the GLOBAL throttle counter fired (type 2).

    Filters out the spurious init-time fire that occurs before the CSR is
    programmed: at reset, threshold_buf=0 so the counter fires immediately
    with threshold=0 in the $display output.

    Also excludes the per-bank g_dfv_bank_throttle[N] instances (type 3) --
    those are always active whenever DFV_THROTTLE_ENABLE=1 (i.e. regardless
    of which test type is running), and would trivially fire during a type-2
    full-freeze too, since every bank being frozen also exceeds each bank's
    own much smaller per-bank threshold. Counting them here would inflate
    the type-2 signal with an unrelated type-3 one.

    If start_line is given, events at or before that line index (setup-stage,
    before the kernel's DFV_TEST_START print) are excluded -- see
    find_test_start_line().
    """
    count = 0
    for i, line in enumerate(lines):
        if "DFV_THROTTLE_REACHED" not in line:
            continue
        if "g_dfv_bank_throttle[" in line:
            continue  # per-bank instance -- not the global (type 2) signal
        m = re.search(r"threshold=(\d+)", line)
        if m and int(m.group(1)) == 0:
            continue  # skip init-time spurious fire
        if start_line is not None and i <= start_line:
            continue  # setup-stage event, before the test sequence started
        count += 1
    # Also pick up appended summary line from a previous trim
    for line in lines:
        m = re.search(r"THROTTLE_REACHED:\s*(\d+)", line)
        if m:
            count = int(m.group(1))
    return count


def extract_bank_throttle(lines, start_line=None):
    """Count how many times each bank's per-bank throttle counter fired (type 3).

    Per-bank instances show up as ...g_dfv_bank_throttle[N].dfv_bank_throttle
    in the $display's %m hierarchical path. Same init-time spurious-fire
    filter as extract_throttle_reached (threshold=0 at reset, before the CSR
    is programmed), and the same start_line setup-stage filter -- see
    find_test_start_line().
    """
    counts = {}
    for i, line in enumerate(lines):
        if "DFV_THROTTLE_REACHED" not in line:
            continue
        m = re.search(r"threshold=(\d+)", line)
        if m and int(m.group(1)) == 0:
            continue  # skip init-time spurious fire
        bm = re.search(r"g_dfv_bank_throttle\[(\d+)\]", line)
        if not bm:
            continue  # the global (non-per-bank) instance -- not a Type-3 signal
        if start_line is not None and i <= start_line:
            continue  # setup-stage event, before the test sequence started
        bank = int(bm.group(1))
        counts[bank] = counts.get(bank, 0) + 1
    # Also pick up appended summary lines from a previous trim
    for line in lines:
        m = re.search(r"BANK_THROTTLE_REACHED:\s*bank(\d+)=(\d+)", line)
        if m:
            counts[int(m.group(1))] = int(m.group(2))
    return counts


def extract_perf(lines):
    """Parse 'PERF: instrs=N, cycles=N, IPC=N' from log lines."""
    for line in lines:
        m = re.search(r"PERF:\s*instrs=(\d+),\s*cycles=(\d+),\s*IPC=([\d.]+)", line)
        if m:
            return int(m.group(1)), int(m.group(2)), float(m.group(3))
    return -1, -1, -1.0


def trim_log(path, keep=200):
    """Trim log to last N lines; extract metrics; append summary footer."""
    with open(path, "r", errors="replace") as f:
        all_lines = f.readlines()

    sig_mismatch = any(line.lower().startswith("error at result") for line in all_lines)

    # DFV_TEST_START is printed by kernel.cpp (vx_printf) right after the CSR
    # setup block, right before vx_spawn_threads -- for EVERY combo, including
    # 'none', so unlike an RTL-only marker this gives a fair setup/test
    # boundary across all combos, not just the ones that enable a stall.
    start_line = find_test_start_line(all_lines)

    window_cycles, metric_error = {}, ""
    if DFV_TEST_TYPE == 1:
        try:
            windows = parse_collision_windows(all_lines)
            collisions = collision_counts(windows)
            window_cycles = collision_cycles(windows)
        except CollisionReportError as exc:
            collisions = {}
            metric_error = str(exc)
        throttle_reached = 0
    elif DFV_TEST_TYPE == 2:
        collisions       = {}
        throttle_reached = extract_throttle_reached(all_lines, start_line)
    else:  # type 3: per-bank throttle-reached counts (reuses the "collisions"
           # dict slot: {bank_index: count}), plus IPC drop computed post-run
        collisions       = extract_bank_throttle(all_lines, start_line)
        throttle_reached = 0

    # Preserve the complete raw CSR records, even if later output pushes them
    # beyond the tail. Re-parsing a trimmed log must not trust summary footers.
    def csr_line(line):
        return MARKER in line or "DFV_CSR_ERROR" in line
    tail = [line for line in all_lines[-keep:] if not csr_line(line)]
    tail += [line for line in all_lines if csr_line(line)]
    tail.append("\n")
    if DFV_TEST_TYPE == 1:
        for key, val in sorted(collisions.items()):
            tail.append(f"COLLISION: {key}={val}\n")
        for key, val in sorted(window_cycles.items()):
            tail.append(f"CSR_WINDOW: {key}_cycles={val}\n")
        if metric_error:
            tail.append(f"CSR_MEASUREMENT_ERROR: {metric_error}\n")
    elif DFV_TEST_TYPE == 2:
        tail.append(f"THROTTLE_REACHED: {throttle_reached}\n")
    elif DFV_TEST_TYPE == 3:
        for bank, val in sorted(collisions.items()):
            tail.append(f"BANK_THROTTLE_REACHED: bank{bank}={val}\n")
    if sig_mismatch:
        tail.append("SIGMISMATCH: true\n")

    with open(path, "w") as f:
        f.writelines(tail)
    return tail, collisions, throttle_reached, sig_mismatch, window_cycles, metric_error


# =============================================================================
# Combo labeling
# =============================================================================

def combo_tag(combo_dict):
    if "label" in combo_dict:
        return combo_dict["label"]
    stalls = combo_dict.get("stalls", (False,) * len(STALL_TYPES))
    enabled = [name for (name, _), on in zip(STALL_TYPES, stalls) if on]
    return "_".join(enabled) if enabled else "none"


def combo_name(combo_dict):
    return f"run_{combo_tag(combo_dict)}.log"


def combo_desc(combo_dict):
    stalls = combo_dict.get("stalls", (False,) * len(STALL_TYPES))
    parts = [f"{s[0]}={'ON' if on else 'off'}" for s, on in zip(STALL_TYPES, stalls)]
    desc = ", ".join(parts)
    extras = []
    for key, default, fmt in [
        ("set_threshold",      DEFAULT_SET_THRESHOLD,      lambda v: f"set={v}"),
        ("release_threshold",  DEFAULT_RELEASE_THRESHOLD,  lambda v: f"rel={v}"),
        ("release_delay",      DEFAULT_RELEASE_DELAY,      lambda v: f"delay=0x{v:X}"),
        ("release_forever",    DEFAULT_RELEASE_FOREVER,    lambda v: f"forever={v}"),
        ("throttle_threshold", DEFAULT_THROTTLE_THRESHOLD, lambda v: f"throttle=0x{v:X}"),
        ("fill_bank_mask",     DEFAULT_FILL_BANK_MASK,     lambda v: f"bank_mask=0x{v:04X}"),
        ("bank_throttle_threshold", DEFAULT_BANK_THROTTLE_THRESHOLD, lambda v: f"bank_throttle=0x{v:X}"),
    ]:
        if key in combo_dict and combo_dict[key] != default:
            extras.append(fmt(combo_dict[key]))
    if extras:
        desc += ", " + ", ".join(extras)
    return desc


# =============================================================================
# Post-processing: derived metrics
# =============================================================================

def get_collision_columns(perf_rows):
    """Return sorted list of raw collision counter names."""
    cols = set()
    for row in perf_rows:
        cols.update(row[6].keys())  # collisions dict at index 6
    def counter_key(name):
        m = re.fullmatch(r"core(\d+)_cache0_bank0_(natural|dfv|total)", name)
        if not m:
            raise ValueError(f"Unknown CSR counter column: {name}")
        return int(m[1]), ("natural", "dfv", "total").index(m[2])
    return sorted(cols, key=counter_key)


def compute_collision_rates(perf_rows, coll_cols, cycle_rows):
    """Return count/captured-window-cycle for each core's reported counter.

    Rate is -1.0 when count or cycles are unavailable.
    """
    if len(perf_rows) != len(cycle_rows):
        raise ValueError("CSR cycle intervals must match the performance rows")
    rates = []
    for row, intervals in zip(perf_rows, cycle_rows):
        coll = row[6]
        row_rates = {}
        for c in coll_cols:
            count = coll.get(c, -1)
            cycles = intervals.get(c.rsplit("_", 1)[0], -1)
            row_rates[c] = count / cycles if (count >= 0 and cycles > 0) else -1.0
        rates.append(row_rates)
    return rates


def get_bank_throttle_columns(perf_rows):
    """Return sorted list of bank indices seen across all type-3 rows."""
    banks = set()
    for row in perf_rows:
        banks.update(row[6].keys())  # per-bank throttle-reached dict at index 6
    return sorted(banks)


def compute_ipc_drops(perf_rows):
    """Return per-row IPC drop % relative to the 'none' combo for the same app.

    Positive value = IPC decreased (slowdown). Returns -1.0 when unavailable.
    """
    baseline = {}  # app -> baseline IPC from 'none' combo
    for app, tag, _, _, ipc, _, _, _ in perf_rows:
        if tag == "none" and ipc > 0:
            baseline[app] = ipc

    drops = []
    for app, tag, _, _, ipc, _, _, _ in perf_rows:
        base = baseline.get(app, -1.0)
        if tag == "none" or base <= 0 or ipc < 0:
            drops.append(-1.0)
        else:
            drops.append((base - ipc) / base * 100.0)
    return drops


# =============================================================================
# Main
# =============================================================================

def main():
    global DFV_TEST_TYPE, COMBOS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", type=int, choices=(1, 2, 3), default=DFV_TEST_TYPE,
                        help="1: collision CSRs (default); 2/3: legacy RTL throttle logs")
    parser.add_argument("--apps", nargs="+", choices=sorted(TEST_APPS), default=list(TEST_APPS))
    parser.add_argument("--parse-log", help="Decode a Type-1 CSR log without building or running an app")
    args = parser.parse_args()
    DFV_TEST_TYPE = args.type
    COMBOS = {1: COMBOS_TYPE1, 2: COMBOS_TYPE2, 3: COMBOS_TYPE3}[DFV_TEST_TYPE]
    if args.parse_log:
        if DFV_TEST_TYPE != 1:
            parser.error("--parse-log supports the Type-1 collision CSR interface")
        try:
            with open(args.parse_log, errors="replace") as stream:
                windows = parse_collision_windows(stream)
            print(json.dumps([window.as_dict() for window in windows], indent=2))
        except (OSError, CollisionReportError) as exc:
            parser.exit(1, f"CSR_ERROR: {exc}\n")
        return 0
    if not os.path.isdir(BUILD_DIR):
        parser.error(f"Configured Vortex build directory is missing: {BUILD_DIR}")
    apps = args.apps
    total = len(apps) * len(COMBOS)
    type_label = {1: "arbitration_race", 2: "fill_throttle", 3: "asymmetric_slowdown"}[DFV_TEST_TYPE]
    print(f"DFV_TEST_TYPE={DFV_TEST_TYPE} ({type_label}), {len(apps)} apps x {len(COMBOS)} combos = {total} runs")

    perf_rows = []  # (app, tag, instrs, cycles, ipc, status, collisions, throttle_reached)
    cycle_rows = []  # per-core CSR window lengths, separate from whole-run PERF
    metric_errors = []
    idx = 0

    for app in apps:
        log_dir = os.path.join(LOG_BASE, app)
        os.makedirs(log_dir, exist_ok=True)

        with preserve_kernel(app):
            for combo_dict in COMBOS:
                idx += 1
                tag  = combo_tag(combo_dict)
                name = combo_name(combo_dict)
                desc = combo_desc(combo_dict)
                print(f"\n[{idx}/{total}] {app}/{name}  ({desc})")

                patch_kernel(app, combo_dict)
                run_args = TEST_APPS[app] + " -d"
                cmd = ["./ci/blackbox.sh", "--driver=rtlsim", f"--app={app}", f"--args={run_args}"]
                print(f"  CMD: {cmd}")
                with open(RUN_LOG, "w") as stream:
                    rc = subprocess.run(cmd, cwd=BUILD_DIR, stdout=stream,
                                        stderr=subprocess.STDOUT).returncode

                if not os.path.isfile(RUN_LOG):
                    print(f"  WARNING: {RUN_LOG} not found")
                    perf_rows.append((app, tag, -1, -1, -1.0, "NO_LOG", {}, 0))
                    cycle_rows.append({})
                    metric_errors.append("Run log was not created")
                    continue

                tail, collisions, throttle_reached, sig_mismatch, intervals, metric_error = trim_log(RUN_LOG)
                instrs, cycles, ipc = extract_perf(tail)
                dest = os.path.join(log_dir, name)
                shutil.move(RUN_LOG, dest)

                if sig_mismatch:
                    status = "SIGMISMATCH"
                elif rc != 0:
                    status = f"FAIL(rc={rc})"
                elif metric_error:
                    status = "CSR_ERROR"
                else:
                    status = "PASS"

                perf_rows.append((app, tag, instrs, cycles, ipc, status, collisions, throttle_reached))
                cycle_rows.append(intervals)
                metric_errors.append(metric_error)

                if DFV_TEST_TYPE == 1:
                    coll_str = " ".join(f"{k}={v}" for k, v in sorted(collisions.items()))
                    extra = f"[{coll_str}]" if coll_str else "[]"
                elif DFV_TEST_TYPE == 3:
                    bt_str = " ".join(f"bank{k}={v}" for k, v in sorted(collisions.items()))
                    extra = f"[throttle_reached: {bt_str}; ipc_drop computed post-run]" if bt_str \
                            else "[throttle_reached: none; ipc_drop computed post-run]"
                else:
                    extra = f"[throttle_reached={throttle_reached}]"

                perf_str = f"instrs={instrs} cycles={cycles} IPC={ipc:.4f}" if instrs != -1 else "no PERF line"
                print(f"  {status}  {perf_str} {extra} -> {dest}")


    # ------------------------------------------------------------------
    # Post-processing: derived metrics
    # ------------------------------------------------------------------
    os.makedirs(LOG_BASE, exist_ok=True)
    csv_path = os.path.join(LOG_BASE, "dfv_perf.csv")

    if DFV_TEST_TYPE == 1:
        coll_cols  = get_collision_columns(perf_rows)
        coll_rates = compute_collision_rates(perf_rows, coll_cols, cycle_rows)
        window_cols = sorted({key for row in cycle_rows for key in row})
        rate_cols  = [f"{c}_rate" for c in coll_cols]
    elif DFV_TEST_TYPE == 3:
        ipc_drops = compute_ipc_drops(perf_rows)
        bank_cols = get_bank_throttle_columns(perf_rows)

    # ------------------------------------------------------------------
    # CSV output
    # ------------------------------------------------------------------
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        if DFV_TEST_TYPE == 1:
            w.writerow(["app", "stall_combo", "instrs", "cycles", "IPC", "status", "csr_error"]
                       + [f"{key}_window_cycles" for key in window_cols] + coll_cols + rate_cols)
            for i, (app, tag, instrs, cycles, ipc, status, coll, _) in enumerate(perf_rows):
                row = [app, tag, instrs, cycles, ipc, status, metric_errors[i]]
                row += [cycle_rows[i].get(key, -1) for key in window_cols]
                row += [coll.get(c, -1) for c in coll_cols]
                row += [f"{coll_rates[i][c]:.12g}" if coll_rates[i][c] >= 0 else -1
                        for c in coll_cols]
                w.writerow(row)
        elif DFV_TEST_TYPE == 3:
            w.writerow(["app", "stall_combo", "instrs", "cycles", "IPC", "status",
                        "ipc_drop_pct"] + [f"bank{b}_throttle_reached" for b in bank_cols])
            for i, (app, tag, instrs, cycles, ipc, status, bank_thr, _) in enumerate(perf_rows):
                drop = ipc_drops[i]
                drop_s = f"{drop:.2f}" if drop >= 0 else -1
                row = [app, tag, instrs, cycles, ipc, status, drop_s]
                row += [bank_thr.get(b, 0) for b in bank_cols]
                w.writerow(row)
        else:  # type 2
            w.writerow(["app", "stall_combo", "instrs", "cycles", "IPC", "status",
                        "throttle_reached"])
            for app, tag, instrs, cycles, ipc, status, _, thr in perf_rows:
                w.writerow([app, tag, instrs, cycles, ipc, status, thr])
    print(f"\nPerf CSV written to {csv_path}")

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    app_w = max(len("APP"), max((len(r[0]) for r in perf_rows), default=0))
    tag_w = max(len("COMBO"), max((len(r[1]) for r in perf_rows), default=0))

    def _row_prefix(app, tag, instrs, cycles, ipc):
        i_s = str(instrs) if instrs != -1 else "-"
        c_s = str(cycles) if cycles != -1 else "-"
        p_s = f"{ipc:.4f}" if ipc != -1.0 else "-"
        return f"{app:<{app_w}} {tag:<{tag_w}} {i_s:>8} {c_s:>8} {p_s:>7}"

    def _print_table(hdr, rows_fn):
        width = max(len(hdr) + 2, 60)
        print(f"\n{'=' * width}\n{hdr}\n{'-' * width}")
        prev_app = None
        for row in perf_rows:
            app = row[0]
            if prev_app is not None and app != prev_app:
                print()           # blank line between app groups
            prev_app = app
            rows_fn(row)
        print(f"{'=' * width}")

    if DFV_TEST_TYPE == 1:
        coll_hdrs = [re.sub(r"core(\d+)_cache0_bank0_", r"c\1_", c) for c in coll_cols]
        hdr = f"{'APP':<{app_w}} {'COMBO':<{tag_w}} {'INSTRS':>8} {'CYCLES':>8} {'IPC':>7}"
        for h in coll_hdrs:
            hdr += f" {h:>16}"
        hdr += " STATUS"

        def _type1_row(row):
            i, (app, tag, instrs, cycles, ipc, status, coll, _) = \
                next((j, r) for j, r in enumerate(perf_rows) if r is row)
            line = _row_prefix(app, tag, instrs, cycles, ipc)
            for c in coll_cols:
                count = coll.get(c, -1)
                rate  = coll_rates[i][c]
                cell  = "-" if count < 0 else (f"{count}({rate:.2e})" if rate >= 0 else str(count))
                line += f" {cell:>16}"
            print(line + f" {status}")

        _print_table(hdr, _type1_row)
        print("  Column format: count(rate/captured GPU window cycle); PERF cycles cover the whole run.")
        print("  cN identifies the reporting core; target is its shared socket's cache unit 0 / bank 0.")

    elif DFV_TEST_TYPE == 3:
        baseline_ipc = {}
        for app, tag, _, _, ipc, _, _, _ in perf_rows:
            if tag == "none" and ipc > 0:
                baseline_ipc[app] = ipc
        bank_hdrs = [f"bank{b}" for b in bank_cols]
        hdr = (f"{'APP':<{app_w}} {'COMBO':<{tag_w}} {'INSTRS':>8} {'CYCLES':>8}"
               f" {'IPC':>7} {'IPC_BASE':>9} {'DROP%':>8}")
        for h in bank_hdrs:
            hdr += f" {h:>8}"
        hdr += " STATUS"

        def _type3_row(row):
            i = perf_rows.index(row)
            app, tag, instrs, cycles, ipc, status, bank_thr = row[:7]
            b_s = f"{baseline_ipc[app]:.4f}" if app in baseline_ipc else "-"
            d_s = f"{ipc_drops[i]:.2f}%" if ipc_drops[i] >= 0 else "-"
            line = _row_prefix(app, tag, instrs, cycles, ipc) + f" {b_s:>9} {d_s:>8}"
            for b in bank_cols:
                line += f" {bank_thr.get(b, 0):>8}"
            print(line + f" {status}")

        _print_table(hdr, _type3_row)
        print("  DROP%: (IPC_none - IPC_stalled) / IPC_none * 100")
        print("  bankN: number of times that bank's per-bank throttle threshold was reached")

    else:  # type 2
        hdr = (f"{'APP':<{app_w}} {'COMBO':<{tag_w}} {'INSTRS':>8} {'CYCLES':>8}"
               f" {'IPC':>7} {'THR':>4} STATUS")
        width = max(len(hdr) + 2, 60)
        print(f"\n{'=' * width}\n{hdr}\n{'-' * width}")
        prev_app = None
        for app, tag, instrs, cycles, ipc, status, _, thr in perf_rows:
            if prev_app is not None and app != prev_app:
                print()
            prev_app = app
            i_s = str(instrs) if instrs != -1 else "-"
            c_s = str(cycles) if cycles != -1 else "-"
            p_s = f"{ipc:.4f}" if ipc != -1.0 else "-"
            print(f"{app:<{app_w}} {tag:<{tag_w}} {i_s:>8} {c_s:>8} {p_s:>7} {thr:>4} {status}")
        print(f"{'=' * width}")

    print(f"Logs saved under {LOG_BASE}/")
    return int(any(row[5] != "PASS" for row in perf_rows))


if __name__ == "__main__":
    raise SystemExit(main())
