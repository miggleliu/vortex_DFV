#!/usr/bin/env python3
"""
Parse rtlsim run.log output for DFV collision counter $display lines and
compute TRT (test run-time efficiency).

Log line format, from libs/VX_dfv_collision_ctr.sv:
    <time>: DFV_COLLISION_NATURAL: <hierarchical_path>  edge_count=<n>
    <time>: DFV_COLLISION_DFV: <hierarchical_path>  edge_count=<n>

Usage:
    ./analyze_collisions.py run.log [--total-cycles N]

Without --total-cycles, only raw collision counts (overall and per-instance
path, e.g. per bank) are reported. TRT = total_cycles / N_collisions requires
--total-cycles since this script does not assume any particular "final PERF
summary" line format -- pass the cycle count from whatever your driver
reports (e.g. rtlsim's own cycle-count printout) to get a real TRT number.
"""
import argparse
import re
import sys
from collections import Counter

LINE_RE = re.compile(
    r'^\s*(?P<time>\d+)\s*:\s*DFV_COLLISION_(?P<kind>NATURAL|DFV)\s*:\s*'
    r'(?P<inst>\S+)\s+edge_count=(?P<count>\d+)'
)


def parse_log(path):
    natural = []  # list of (time, inst, cumulative_count)
    dfv = []
    with open(path) as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            entry = (int(m.group("time")), m.group("inst"), int(m.group("count")))
            if m.group("kind") == "NATURAL":
                natural.append(entry)
            else:
                dfv.append(entry)
    return natural, dfv


def summarize(label, events, total_cycles):
    print(f"--- {label} ---")
    if not events:
        print("  0 collisions observed")
        return
    n = len(events)
    per_inst = Counter(inst for _, inst, _ in events)
    first_t = events[0][0]
    last_t = events[-1][0]
    print(f"  total collisions: {n}")
    print(f"  first at t={first_t}, last at t={last_t}")
    print("  by instance (bank/arbiter):")
    for inst, cnt in per_inst.most_common():
        print(f"    {inst}: {cnt}")
    if total_cycles:
        trt = total_cycles / n
        print(f"  TRT = total_cycles / N_collisions = {total_cycles} / {n} = {trt:.1f} cycles/collision")
    else:
        # fall back to span between first and last observed collision --
        # NOT the same as total sim cycles, but a lower-bound proxy when the
        # true run length isn't supplied.
        span = last_t - first_t
        print(f"  (no --total-cycles given; observed span between first/last "
              f"collision = {span} time units -- pass --total-cycles for a "
              f"real TRT against the FULL run length)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile")
    ap.add_argument("--total-cycles", type=int, default=None,
                     help="total simulated cycles for this run, for TRT = total_cycles/N_collisions")
    args = ap.parse_args()

    natural, dfv = parse_log(args.logfile)

    if not natural and not dfv:
        print(f"No DFV_COLLISION_* lines found in {args.logfile}.", file=sys.stderr)
        print("Check that the log actually came from an rtlsim run with "
              "SIMULATION defined (the $display lines are compiled out otherwise).",
              file=sys.stderr)
        sys.exit(1)

    summarize("Natural collisions (DFV disabled)", natural, args.total_cycles)
    print()
    summarize("DFV-forced collisions (DFV enabled)", dfv, args.total_cycles)


if __name__ == "__main__":
    main()
