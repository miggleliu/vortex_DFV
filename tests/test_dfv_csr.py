#!/usr/bin/env python3
"""Contract tests for GPU CSR report parsing and combo-runner integration."""
from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import dfv_csr as csr
import run_dfv_combos as runner


def record(phase, **changes):
    fields = {"v": 1, "core": 0, "cores": 1, "cache": 0, "bank": 0,
              "phase": phase, "info": "0x434f4c32", "flags": 9,
              "natural": 2, "dfv": 3, "cycle_lo": 100, "cycle_hi": 0}
    if phase == "END":
        fields.update(natural=7, dfv=14, cycle_lo=300)
    fields.update(changes)
    return "#0: DFV_CSR_SNAPSHOT " + " ".join(f"{key}={value}" for key, value in fields.items()) + "\n"


class ParserTests(unittest.TestCase):
    def test_deltas_and_rates_use_the_snapshot_window(self):
        windows = csr.parse_collision_windows([record("BEGIN"), record("END")])
        self.assertEqual((windows[0].natural, windows[0].dfv, windows[0].total, windows[0].cycles), (5, 11, 16, 200))
        counts = csr.collision_counts(windows)
        rows = [("vecadd", "none", 1000, 900000, 0.1, "PASS", counts, 0)]
        rates = runner.compute_collision_rates(rows, list(counts), [csr.collision_cycles(windows)])
        self.assertEqual(rates[0]["core0_cache0_bank0_total"], 16 / 200)

    def test_valid_zero_and_cycle_low_word_carry(self):
        window = csr.parse_collision_windows([
            record("BEGIN", cycle_lo=0xFFFFFFF0, cycle_hi=3),
            record("END", natural=2, dfv=3, cycle_lo=0x10, cycle_hi=4)])[0]
        self.assertEqual((window.total, window.cycles), (0, 32))

    def test_multicore_records_stay_separate(self):
        windows = csr.parse_collision_windows([
            record("BEGIN", core=1, cores=2), record("BEGIN", cores=2),
            record("END", cores=2), record("END", core=1, cores=2, natural=4)])
        self.assertEqual([window.core for window in windows], [0, 1])
        self.assertEqual(len(csr.collision_counts(windows)), 6)

    def test_legacy_and_summary_lines_cannot_supply_counts(self):
        with self.assertRaises(csr.CollisionReportError):
            csr.parse_collision_windows([
                "123: DFV_COLLISION_DFV: top.dcache.g_banks[0].dfv_bank_ctr edge_count=88\n",
                "COLLISION: dfv_bank0=99\n"])
        lines = [record("BEGIN"), "COLLISION: core0_cache0_bank0_total=99999\n", record("END")]
        self.assertEqual(csr.parse_collision_windows(lines)[0].total, 16)

    def test_invalid_partial_saturated_or_reordered_records(self):
        invalid = [
            [], [record("BEGIN")], [record("END"), record("BEGIN")],
            [record("BEGIN"), record("BEGIN"), record("END")],
            [record("BEGIN", cores=2), record("END", cores=2)],
            [record("BEGIN"), record("END", cores=2)],
            [record("BEGIN"), record("END", flags=1)],
            [record("BEGIN"), record("END", flags=8)],
            [record("BEGIN"), record("END", flags=11)],
            [record("BEGIN"), record("END", flags=13)],
            [record("BEGIN"), record("END", flags=25)],
            [record("BEGIN", natural=4095), record("END")],
            [record("BEGIN"), record("END", dfv=4095)],
            [record("BEGIN"), record("END", natural=-1)],
            [record("BEGIN"), record("END", info="0x434f4c31")],
            [record("BEGIN"), record("END", bank=1)],
            [record("BEGIN"), record("END", cache=1)],
            [record("BEGIN"), record("END", core=1)],
            [record("BEGIN"), record("END", cycle_lo=100)],
            [record("BEGIN"), record("END", cycle_hi=4096)],
            [record("BEGIN"), record("END", natural=1)],
            [record("BEGIN"), record("END", dfv=1)],
            [record("BEGIN"), record("END").replace("flags=9", "")],
            [record("BEGIN"), record("END").rstrip() + " flags=9\n"],
            [record("BEGIN"), record("END"), "DFV_CSR_ERROR bad reading\n"],
        ]
        for lines in invalid:
            with self.subTest(lines=lines), self.assertRaises(csr.CollisionReportError):
                csr.parse_collision_windows(lines)

    def test_trim_preserves_raw_records_and_is_reparseable(self):
        with tempfile.TemporaryDirectory() as temp:
            logfile = Path(temp) / "run.log"
            logfile.write_text(record("BEGIN") + record("END") + "noise\n" * 250 + "PERF: instrs=900, cycles=900000, IPC=0.001\n")
            with patch.object(runner, "DFV_TEST_TYPE", 1):
                first = runner.trim_log(str(logfile), keep=3)
                second = runner.trim_log(str(logfile), keep=3)
            self.assertEqual(first[1], second[1])
            self.assertEqual(first[4], second[4])
            self.assertFalse(first[5])
            self.assertEqual(csr.parse_collision_windows(logfile.read_text().splitlines())[0].total, 16)


class RunnerTests(unittest.TestCase):
    def run_fixture(self, valid=True, interrupted=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reg/vecadd").mkdir(parents=True)
            (root / "build").mkdir()
            kernel = root / "reg/vecadd/kernel.cpp"
            source = ("csr_write(VX_CSR_DFV_CTRL, 1);\n" +
                      "\n".join(f"csr_write({name}, 0);" for _, name in runner.STALL_TYPES) +
                      "\ncsr_write(VX_CSR_DFV_CTRL, 0);\n")
            kernel.write_text(source)
            modes = []
            def launch(command, **kwargs):
                modes.append(kernel.read_text().splitlines()[0])
                if interrupted:
                    raise KeyboardInterrupt
                log = record("BEGIN") + record("END") if valid else "legacy DFV_COLLISION_DFV only\n"
                kwargs["stdout"].write(log + "PERF: instrs=900, cycles=900000, IPC=0.001\n")
                return subprocess.CompletedProcess(command, 0)
            with patch.multiple(runner, REG_DIR=str(root / "reg"), BUILD_DIR=str(root / "build"),
                                RUN_LOG=str(root / "build/run.log"), LOG_BASE=str(root / "logs")), \
                    patch.object(runner.subprocess, "run", side_effect=launch), \
                    patch.object(sys, "argv", ["run_dfv_combos.py", "--type", "1", "--apps", "vecadd"]), \
                    redirect_stdout(io.StringIO()):
                if interrupted:
                    with self.assertRaises(KeyboardInterrupt):
                        runner.main()
                    self.assertEqual(kernel.read_text(), source)
                    return
                result = runner.main()
            self.assertEqual(kernel.read_text(), source)
            self.assertIn(", 0);", modes[0])
            self.assertIn(", 1);", modes[1])
            with (root / "logs/dfv_perf.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 3)
            self.assertEqual(result, 0 if valid else 1)
            if valid:
                self.assertTrue(all(row["status"] == "PASS" for row in rows))
                self.assertEqual(float(rows[0]["core0_cache0_bank0_total_rate"]), 16 / 200)
                self.assertEqual(rows[0]["core0_cache0_bank0_window_cycles"], "200")
            else:
                self.assertTrue(all(row["status"] == "CSR_ERROR" for row in rows))
                self.assertTrue(all(row["csr_error"] for row in rows))

    def test_successful_runs_and_exact_source_restore(self):
        self.run_fixture()

    def test_missing_csr_cannot_pass_even_when_app_succeeds(self):
        self.run_fixture(valid=False)

    def test_interruption_restores_source(self):
        self.run_fixture(interrupted=True)


class ProducerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "native C++ compiler unavailable")
    def test_actual_cpp_report_format_and_validation(self):
        root = Path(__file__).resolve().parents[1]
        source = r'''
#include <cstdarg>
#include <cstdio>
#include <cstdint>
#define __VX_INTRINSICS_H__
static unsigned reporting_core;
static inline unsigned vx_core_id() { return reporting_core; }
static inline unsigned vx_num_cores() { return 2; }
#include <vx_dfv_collision_report.h>
extern "C" int vx_printf(const char* format, ...) {
    va_list args; va_start(args, format);
    int result = std::vprintf(format, args); va_end(args); return result;
}
int main(int argc, char**) {
    bool invalid = argc > 1;
    vx_dfv_collision_snapshot_t begin = {VX_DFV_COLLISION_ID, 9, 2, 3, 100, 0};
    vx_dfv_collision_snapshot_t end = {VX_DFV_COLLISION_ID, 9, 7, 14, 300, 0};
    if (invalid) { end.natural_count = 4095; end.status = 11; }
    for (reporting_core = 0; reporting_core < 2; ++reporting_core) {
        int result = vx_dfv_collision_report(&begin, &end);
        if ((result != 0) != invalid) return 42;
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            subprocess.run([sys.executable, str(root / "hw/scripts/gen_config.py"),
                            "-i", str(root / "hw/rtl/VX_types.vh"),
                            "-o", str(work / "VX_types.h")], check=True, capture_output=True, text=True)
            (work / "report.cpp").write_text(source)
            subprocess.run(["c++", "-std=gnu++17", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(work), "-I" + str(root / "kernel/include"),
                            str(work / "report.cpp"), "-o", str(work / "report")],
                           check=True, capture_output=True, text=True)
            valid = subprocess.run([str(work / "report")], check=True, capture_output=True, text=True)
            windows = csr.parse_collision_windows(valid.stdout.splitlines())
            self.assertEqual(len(windows), 2)
            self.assertTrue(all(window.total == 16 and window.cycles == 200 for window in windows))
            invalid = subprocess.run([str(work / "report"), "at-limit"], check=True, capture_output=True, text=True)
            with self.assertRaises(csr.CollisionReportError):
                csr.parse_collision_windows(invalid.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
