#!/usr/bin/env python3
"""Read global/per-bank throttle event CSRs from one or more GPU logs.

For Type-3 calibration, sweep VX_CSR_DFV_BANK_THROTTLE_THRESHOLD in DFV-OFF
runs. Compare each bank's captured-window event count/rate and choose a
threshold above baseline idle gaps. Global Type-2 thresholds are separate.
The CSR ABI provides counts and window cycles, not per-event timestamps.
"""
import argparse
from dfv_csr import CollisionReportError, parse_observation_windows


def parse_log(path):
    with open(path, errors='replace') as stream:
        return [w for w in parse_observation_windows(stream) if w.family == 'throttle']


def summarize(path, core=None):
    windows = parse_log(path)
    if core is not None:
        windows = [w for w in windows if w.core == core]
    if not windows:
        raise CollisionReportError('No throttle monitors for the requested core/configuration')
    print(f'=== {path} ===')
    for w in windows:
        print(f'{w.prefix}: events={w.count0}, threshold={w.threshold}, '
              f'window_cycles={w.cycles}, events/Mcycle={w.count0 * 1e6 / w.cycles:.6f}')
    return windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logfiles', nargs='+')
    parser.add_argument('--core', type=int)
    args = parser.parse_args()
    try:
        for path in args.logfiles:
            summarize(path, args.core)
    except (OSError, CollisionReportError) as exc:
        parser.exit(1, f'CSR_ERROR: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
