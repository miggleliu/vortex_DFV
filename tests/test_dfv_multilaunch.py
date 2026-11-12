#!/usr/bin/env python3
"""Regressions for applications such as dogfood that launch many kernels."""
import json
from pathlib import Path
import tempfile
import unittest

import dfv_csr as csr
import run_dfv_combos as runner
from test_dfv_csr import report


def launch_report(index, cores=1):
    lines = report(cores=cores)
    # Each launch may reset counters/cycles. Make its deltas distinguishable.
    return [line.replace('count0=5005 ', f'count0={5005 + index} ')
            .replace('cycle_lo=300 ', f'cycle_lo={300 + index} ')
            if 'phase=END' in line else line for line in lines]


class MultiLaunchTests(unittest.TestCase):
    def test_all_dogfood_launches_keep_independent_deltas(self):
        windows = csr.parse_observation_windows(
            line for index in range(24) for line in launch_report(index))
        self.assertEqual(len(windows), 24 * 9)
        self.assertEqual({w.launch for w in windows}, set(range(24)))
        self.assertEqual({w.launches for w in windows}, {24})
        for index in range(24):
            first = next(w for w in windows if w.launch == index
                         and w.family == 'collision' and w.index == 0)
            self.assertEqual((first.natural, first.dfv, first.cycles),
                             (5 + index, 11, 200 + index))
            self.assertTrue(first.prefix.startswith(f'launch{index}_core0_'))
        self.assertEqual(len({w.prefix for w in windows}), len(windows))

    def test_single_launch_preserves_existing_metric_names(self):
        windows = csr.parse_observation_windows(report())
        self.assertEqual(windows[0].prefix,
                         'core0_collision_dcache_owner0_unit0_bank0')
        self.assertEqual((windows[0].launch, windows[0].launches), (0, 1))

    def test_shared_reader_cores_and_launches_are_separate(self):
        windows = csr.parse_observation_windows(report(cores=2) * 2)
        self.assertEqual(len(windows), 36)
        self.assertEqual(len({w.prefix for w in windows}), 36)
        self.assertEqual({(w.launch, w.core) for w in windows},
                         {(0, 0), (0, 1), (1, 0), (1, 1)})

    def test_runner_metrics_do_not_overwrite_earlier_launches(self):
        windows = csr.parse_observation_windows(launch_report(0) + launch_report(1))
        for test_type, monitors in ((1, 6), (2, 1), (3, 2)):
            with self.subTest(test_type=test_type):
                metrics, cycles, thresholds = runner.window_metrics(windows, test_type)
                self.assertEqual(len(cycles), monitors * 2)
                self.assertEqual(len(metrics), monitors * 2 * (3 if test_type == 1 else 1))
                self.assertEqual(len(thresholds), monitors * 2 if test_type != 1 else 0)
        key = 'core0_collision_dcache_owner0_unit0_bank0_natural'
        metrics, _, _ = runner.window_metrics(windows, 1)
        self.assertEqual((metrics['launch0_' + key], metrics['launch1_' + key]), (5, 6))
        decoded = json.loads(json.dumps([w.as_dict() for w in windows]))
        self.assertEqual({w['launch'] for w in decoded}, {0, 1})

    def test_trim_keeps_every_launch_and_can_reparse_existing_logs(self):
        lines = launch_report(0) + ['noise\n'] * 250 + launch_report(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dogfood.log'
            # Previously trimmed failing logs keep the original raw CSR records.
            path.write_text(''.join(lines) + 'CSR_MEASUREMENT_ERROR: Duplicate CSR frame\n')
            result = runner.trim_log(path, 1, keep=3)
            self.assertEqual(result[5], '')
            before = [w.as_dict() for w in result[1]]
            self.assertEqual(before, [w.as_dict() for w in
                             csr.parse_observation_windows(path.read_text().splitlines())])
            self.assertEqual(result[2], runner.trim_log(path, 1, keep=3)[2])

    def test_incomplete_or_duplicate_records_still_fail(self):
        lines = report()
        end_frame = next(line for line in lines
                         if 'DFV_CSR_FRAME' in line and 'phase=END' in line)
        cases = [
            lines + launch_report(1)[:-1],  # Partial final launch.
            lines[:-1] + launch_report(1),  # Partial earlier launch.
            lines[:2] + lines,             # BEGIN duplicated before completion.
            lines + [end_frame],           # Repeated END is not a new launch.
            lines + [lines[1]],            # Repeated sample is not a new launch.
            report(cores=2)[:len(lines)] + report(cores=2),  # Missing reader core.
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(csr.CollisionReportError):
                csr.parse_observation_windows(case)


if __name__ == '__main__':
    unittest.main()
