#!/usr/bin/env python3
"""
Parse DFV_THROTTLE_REACHED lines (per-bank instances) to calibrate a Type-3
asymmetric-slowdown threshold, or to read out results from an actual Type-3
run. Companion to the per-bank VX_dfv_throttle_ctr instantiation sketched for
cache/VX_cache.sv (see memory/frontdoor_vs_dfv.md and this session's Type-3
discussion) -- NOT yet applied to the live RTL, this script is ready for once
it is.

Log format (libs/VX_dfv_throttle_ctr.sv, unmodified):
    <time>: DFV_THROTTLE_REACHED: <hierarchical_path>  threshold=<n>

Per-bank instances show up as ...g_dfv_bank_throttle[N].dfv_bank_throttle in
the hierarchical path; the existing global instance (Type 2's full-freeze
detector) has no such suffix and is reported separately as "global".

Calibration workflow:
    1. Run a stalls=none (no DFV) baseline several times, each with
       DFV_THROTTLE_THRESHOLD set to a different small candidate value
       (e.g. 20, 50, 100, 200 cycles), saving each run's log separately.
    2. ./analyze_bank_throttle.py baseline_t20.log baseline_t50.log ...
    3. Pick the smallest threshold where ALL banks' event counts drop to
       ~0 in the baseline -- that's the noise floor. Use a value
       comfortably above it (but well below the Type-2 full-freeze
       threshold) as the real Type-3 per-bank threshold.

Type-3 test readout:
    ./analyze_bank_throttle.py run.log
    Compare the masked/stalled bank's event count against the others' --
    it should be far higher if the induced slowdown is being detected.
"""
import argparse
import re
from collections import defaultdict

LINE_RE = re.compile(
    r'^\s*(?P<time>\d+)\s*:\s*DFV_THROTTLE_REACHED\s*:\s*'
    r'(?P<inst>\S+)\s+threshold=(?P<threshold>\d+)'
)
BANK_RE = re.compile(r'g_dfv_bank_throttle\[(\d+)\]')


def parse_log(path):
    per_bank = defaultdict(list)  # bank index (or None for the global instance) -> [timestamps]
    thresholds_seen = set()
    with open(path) as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            thresholds_seen.add(int(m.group("threshold")))
            bm = BANK_RE.search(m.group("inst"))
            bank = int(bm.group(1)) if bm else None
            per_bank[bank].append(int(m.group("time")))
    return thresholds_seen, per_bank


def summarize(path):
    thresholds_seen, per_bank = parse_log(path)
    print(f"=== {path} ===")
    if not per_bank:
        print("  no DFV_THROTTLE_REACHED lines found")
        return
    if len(thresholds_seen) > 1:
        print(f"  WARNING: multiple threshold values seen in this log ({sorted(thresholds_seen)}) "
              f"-- results below mix runs at different thresholds, re-check how this log was generated")
    else:
        print(f"  threshold={thresholds_seen.pop()}")
    for bank in sorted(per_bank.keys(), key=lambda b: (b is None, b)):
        events = per_bank[bank]
        label = f"bank {bank}" if bank is not None else "global (Type-2 full-freeze) instance"
        print(f"  {label}: {len(events)} event(s)")
        if len(events) > 1:
            gaps = [b - a for a, b in zip(events, events[1:])]
            print(f"    time between events: min={min(gaps)} max={max(gaps)} "
                  f"(sanity check: are these distinct events, not one pulse train?)")
    return per_bank


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfiles", nargs="+",
                     help="one or more run.log files, each ideally from a run at a single "
                          "DFV_THROTTLE_THRESHOLD value")
    args = ap.parse_args()

    for path in args.logfiles:
        summarize(path)
        print()

    if len(args.logfiles) > 1:
        print("Calibration hint: pick the smallest threshold where the per-bank event counts "
              "drop to ~0 across ALL banks in a stalls=none baseline -- that's your noise floor. "
              "Use a threshold comfortably above it (but well below the Type-2 full-freeze "
              "threshold) for the real Type-3 per-bank counters.")


if __name__ == "__main__":
    main()
