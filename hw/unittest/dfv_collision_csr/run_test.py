#!/usr/bin/env python3
"""Run the complete VX_csr_data module with Verilator assertions enabled.

No Vortex SDK or generated RISC-V program is needed. An optional --csr-source
allows checking an earlier module against the same testbench.
"""
import argparse
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verilator", default=shutil.which("verilator"))
    parser.add_argument("--xlen", type=int, choices=(32, 64), nargs="+", default=[32, 64])
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--csr-source", type=Path)
    parser.add_argument("--configs", nargs=argparse.REMAINDER, default=[], help="extra -D configuration arguments")
    args = parser.parse_args()
    if not args.verilator:
        parser.error("Verilator is not on PATH; use --verilator /path/to/verilator")
    # Expected assertion failures must not create large core files.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    root = Path(__file__).resolve().parents[3]
    local = Path(__file__).resolve().parent
    source = (args.csr_source or root / "hw/rtl/core/VX_csr_data.sv").resolve()
    version = subprocess.run([args.verilator, "--version"], check=True,
                             capture_output=True, text=True).stdout
    match = re.search(r"Verilator (\d+)\.(\d+)", version)
    newer_tool_flags = []
    if match and (int(match[1]), int(match[2])) >= (5, 50):
        # Existing DFV controls have declaration initializers plus procedural
        # writes. Keep this newer-tool compatibility flag local to this test.
        newer_tool_flags = ["-Wno-PROCASSINIT"]
    temporary = tempfile.TemporaryDirectory(prefix="dfv-csr-test-") if args.build_dir is None else None
    work = (args.build_dir or Path(temporary.name)).resolve()
    work.mkdir(parents=True, exist_ok=True)
    print(version.strip(), flush=True)
    try:
        for xlen in args.xlen:
            obj = work / f"rv{xlen}"
            command = [args.verilator, "--binary", "--timing", "--assert", "-j", "2",
                       "--language", "1800-2009", "--timescale", "1ns/1ns",
                       "-Wall", "-Wpedantic", "-Wno-DECLFILENAME", "-Wno-REDEFMACRO",
                       *newer_tool_flags, "-DSIMULATION", "-DNDEBUG", f"-DXLEN_{xlen}",
                       "-DNUM_THREADS=4", "-DNUM_WARPS=4", "--Mdir", str(obj),
                       "--top-module", "tb_dfv_collision_csr"]
            for directory in ("hw/rtl", "hw/rtl/core", "hw/rtl/interfaces", "hw/rtl/fpu", "hw/rtl/libs"):
                command.append("-I" + str(root / directory))
            command += args.configs
            command += [str(root / "hw/rtl/VX_gpu_pkg.sv"),
                        str(root / "hw/rtl/fpu/VX_fpu_pkg.sv"), str(source),
                        str(local / "tb_dfv_collision_csr.sv")]
            environment = os.environ.copy()
            if not shutil.which("ccache"):
                environment.setdefault("OBJCACHE", "")
            compiled = subprocess.run(command, cwd=root, text=True, capture_output=True,
                                      env=environment)
            (work / f"rv{xlen}-build.log").write_text(compiled.stdout + compiled.stderr)
            if compiled.returncode:
                print(compiled.stderr)
                return 1
            executable = obj / "Vtb_dfv_collision_csr"
            for mode, diagnostic in (("", "PASS:"),
                                     ("invalid_write", "invalid CSR write address: 7d3"),
                                     ("invalid_read", "invalid CSR read address: 0xce1")):
                command = [str(executable)] + (["+" + mode] if mode else [])
                result = subprocess.run(command, cwd=work, text=True, capture_output=True)
                output = result.stdout + result.stderr
                label = mode or "valid_commands"
                (work / f"rv{xlen}-{label}.log").write_text(output)
                valid = diagnostic in output and (result.returncode == 0 if not mode else result.returncode != 0)
                if not valid:
                    print(f"FAIL: RV{xlen} {label}: rc={result.returncode}\n{output}")
                    return 1
                if not mode:
                    events = re.findall(r'DFV_THROTTLE_REACHED:.*primitive_throttle\s+threshold=(\d+)', output)
                    actual = sum(int(value) != 0 for value in events)
                    if actual != 2 or 'THROTTLE_CSR_REFERENCE events=2' not in output:
                        print('FAIL: hardware throttle count disagrees with filtered legacy display events')
                        return 1
                print(f"PASS: RV{xlen} {label}", flush=True)
        return 0
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
