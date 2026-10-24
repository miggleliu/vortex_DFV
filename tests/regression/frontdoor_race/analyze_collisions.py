#!/usr/bin/env python3
"""Analyze GPU CSR collision snapshots from frontdoor or DFV regression logs.

Uses captured BEGIN/END count and cycle differences. The scope is the reporting
monitor's reported cache/domain, unit, bank and owner; reader cores are not summed.
Legacy DFV_COLLISION_* simulator prints do not supply measurements.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dfv_csr import CollisionReportError, parse_collision_windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logfile", type=Path)
    parser.add_argument("--total-cycles", type=int, default=None,
                        help="explicit external cycle denominator; default uses each captured CSR window")
    parser.add_argument("--core", type=int, default=None, help="show one reporting core")
    args = parser.parse_args()
    if args.total_cycles is not None and args.total_cycles <= 0:
        parser.error("--total-cycles must be positive")
    try:
        with args.logfile.open(errors="replace") as stream:
            windows = parse_collision_windows(stream)
    except (OSError, CollisionReportError) as exc:
        parser.exit(1, f"CSR_ERROR: {exc}\n")
    if args.core is not None:
        windows = [window for window in windows if window.core == args.core]
        if not windows:
            parser.error("requested reporting core is absent")
    for window in windows:
        cycles = args.total_cycles if args.total_cycles is not None else window.cycles
        source = "external override" if args.total_cycles is not None else "captured GPU CSR interval"
        print(f"{window.prefix} (monitor index {window.index})")
        print(f"  natural={window.natural}, DFV-classified={window.dfv}, total={window.total}")
        print(f"  cycles={cycles} ({source})")
        print(f"  edges/Mcycle={window.total * 1e6 / cycles:.6f}")
        if window.total:
            print(f"  cycles/qualified-edge={cycles / window.total:.6f}")
        else:
            print("  cycles/qualified-edge=undefined (zero qualified events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
