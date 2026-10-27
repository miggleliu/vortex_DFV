# KV260: reuse the Vortex DFV regression tests

Hardware image baseline: `1a2cde44fe8567dbac360861db7c3d311d572985` (main, checked 2026-10-25).
This is a new BRAM board image and a runtime backend for the existing Vortex API.
It is not compatible with the earlier 64 KiB image. Do not run its old smoke loader
against the new image or use this runtime with the old image.

The first apps are **demo** (its original integer type) and **vecadd with TYPE=int**
(the test's existing compile-time option). Host `main.cpp`, GPU `kernel.cpp`,
startup, `vx_spawn_threads`, functional result verification, CSR report helpers,
DFV combo definitions and CSR decoder all come from the pinned repository.
The build script applies the existing combo patcher to private source copies.
No shared regression source or simulation configuration is edited.

The default floating-point vecadd and float matrix tests require a later FPU
profile; they are not claimed to run on this integer-only image. Only the first
two apps are currently integrated into the bundle builder. More tests need an
instruction/memory audit, not a replacement kernel.

## 1. Ubuntu VM: prepare the Windows Vivado package

The regression builder accepts newer software commits after checking the FPGA
interface and profile. Keep your existing checkout and Ubuntu simulation build.
Hardware preparation below remains pinned to the image baseline. These commands run from the repository root:

```bash
python3 fpga/kv260/prepare.py --out build-kv260-vivado
```

Requires Python 3 and Verilator 5 on Ubuntu. `prepare.py` checks the commit and
rejects modified upstream RTL. It preprocesses the actual latest RTL, including
the GPU collision/throttle CSR implementation. Transfer `build-kv260-vivado/`
through the Mac to Windows, for example `C:/Xilinx/Vivado/vortex-regression`.
The downloadable prepared directory was already produced by this command.

## 2. Windows: synthesize and implement the new image

Vivado 2024.1, installed KV260 SOM board files and production K26 device support:

```tcl
# Save and close the previous project first.
source C:/Xilinx/Vivado/vortex-regression/create_project.tcl
# First check synthesis resources for this new memory/bank configuration:
launch_runs synth_1 -jobs 4
wait_on_run synth_1
open_run synth_1
report_utilization -file C:/Xilinx/Vivado/vortex-regression/utilization_synth.rpt
# Continue after synthesis fits the K26 resources:
source C:/Xilinx/Vivado/vortex-regression/build_bitstream.tcl
```

The project uses the corrected auxiliary reset: active-low aux input tied high.
The asynchronous RAM netlist repair runs before implementation optimization.
Check the routed timing, DRC and utilization reports in `artifacts/` before
deployment. Synthesis/implementation has NOT been run here; FPGA fit and timing
must be established on Windows. Keep the extracted sources while using the project.

Image profile: one RV32 integer core, four warps, four threads per warp,
four D-cache banks, 4 KiB I-cache, 4 KiB D-cache, existing 16 KiB local memory,
256 KiB backing BRAM, nominal 100 MHz, no L2/L3 and no external DDR backend.
This differs from the Ubuntu default 32 threads/warp and original cache sizes.
For quantitative platform comparisons simulate these exact defines as well.
BRAM latency does not reproduce simulated DRAM latency; coverage may differ.

## 3. Ubuntu VM: build real regression binaries

Use the RV32 Vortex LLVM/GCC/newlib/compiler-rt SDK in the same tool directory as
your simulation tools. RV64-only SDK installations need the RV32 components too.
This script checks that the required tools exist. Obtain them using the Vortex
repository's toolchain installation instructions for your environment.

```bash
sudo apt update
sudo apt install g++-aarch64-linux-gnu
mkdir build-kv260-sdk
cd build-kv260-sdk
../configure --xlen=32 --tooldir="$HOME/tools"
cd ..
python3 fpga/kv260/build_regressions.py \
  --build build-kv260-sdk --out build-kv260-tests \
  --apps demo --type 1
```

Confirm your SDK path if it differs from `$HOME/tools`. The script builds
`libvortex.a` from the upstream startup/library with matching profile settings,
cross-compiles the original host program for ARM64 and each combo kernel for
RV32, and checks code/TLS bounds and unsupported instructions. Three type-1
variants use the exact latest main thresholds, seeds and injection combinations.
For the integer vecadd variant use `--apps demo vecadd`. For types 2/3 build a
separate output directory with `--type 2` or `--type 3`; their existing thresholds
are retained and require calibration for BRAM when interpreting coverage.

The GPU SDK application build has NOT been executed in this environment, which
lacks the Vortex compiler/newlib SDK. The ARM64 runtime and original demo/vecadd
host programs have been cross-compiled. Report the first error if the GPU build fails.
Compilation for x86 Ubuntu alone does not produce the ARM64 host executable.

## 4. Transfer, load, run on KV260 Ubuntu

Transfer `build-kv260-tests/` (including runtime, tests, cases and bundle.json)
and the new bitstream through your Mac. Load the new bitstream using the same
Ubuntu bootgen + xmutil flow as the previous KV260 project:

```bash
# In the directory containing the bitstream and the prepared package files:
bootgen -arch zynqmp -image vortex.bif -process_bitstream bin -w
dtc -@ -I dts -O dtb -o kv260-vortex.dtbo kv260-vortex.dts
sudo mkdir -p /lib/firmware/xilinx/kv260-vortex
sudo cp kv260-vortex.bit.bin kv260-vortex.dtbo shell.json \
  /lib/firmware/xilinx/kv260-vortex/
sudo xmutil unloadapp
sudo xmutil loadapp kv260-vortex
sudo xmutil listapps
cat /sys/class/fpga_manager/fpga0/state
find /sys/devices/platform -path '*vortex-clock*/set_rate' -print -exec cat {} \;
```

Confirm `kv260-vortex` is the active package (`0->0`), FPGA state `operating`, and
the PL clock near 100 MHz before MMIO access. The new runtime checks peripheral
version, configuration and upstream signature before touching the larger RAM.
After loading the correct image:

```bash
cd ~/build-kv260-tests
sudo python3 run_board.py --apps demo --out results-demo-type1
```

The original ARM host test allocates inputs, uploads the real kernel and argument
structure, launches Vortex, downloads the result and compares against its CPU
reference. GPU `vx_printf` output, including CSR reports, uses a lossless
backpressured console FIFO. The driver drains it during execution. Full logs,
CSR JSON, provenance and CSV are retained. `PASS` requires successful application
return plus valid CSR and PERF data. `ZERO_EVENTS` remains valid zero coverage;
it is not a hardware failure or proof of useful DFV activation.

The driver caps waits at 60 seconds by default and resets a timed-out GPU.
Override with `VORTEX_KV260_TIMEOUT_MS` using `sudo env` if needed. Software timeout
cannot rescue an AXI access if the PL clock/reset itself is wrong. No board
execution is claimed until these steps pass on your KV260.

## Memory and software reuse

| GPU address | Purpose | ARM offset from 0xA0000000 |
|---|---|---|
| 0x80000000–0x8000FFFF | Kernel, globals, per-hart TLS | 0x40000–0x4FFFF |
| 0x00010000–0x0001FFFF | Host-allocated inputs, outputs, arguments | 0x50000–0x5FFFF |
| 0xFFFD0000–0xFFFEFFFF | 16 standard 8 KiB thread stacks | 0x60000–0x7FFFF |
| 0xFFFF0000 onward | Existing Vortex local memory | GPU-local |
| 0x40–0x7F | Standard per-hart console | FIFO pop 0x40; count 0x44 |
| 0x80–0x17F | Standard MPM and exit code | 0x1000–0x10FF |

Register ABI 0x00020000. RAM apertures reject accesses during GPU execution.
Register 0x48 reports the upstream signature 0x1a2cde44. Runtime exposes the
actual four threads/warp; demo therefore sizes its workload for 16 hardware
thread contexts. This is test-source reuse, not binary compatibility with RV64.
The CSR count definitions and BEGIN/END window are unchanged from latest main.
Natural collisions before BEGIN are still excluded from window deltas.

## Reproduce infrastructure validation on Ubuntu

```bash
python3 fpga/kv260/tests/run_checks.py --work build-kv260-validation
python3 hw/unittest/dfv_collision_csr/run_test.py --xlen 32 --configs \
  -DEXT_F_DISABLE -DDCACHE_NUM_BANKS=4 -DPLATFORM_MEMORY_NUM_BANKS=1 \
  -DICACHE_SIZE=4096 -DDCACHE_SIZE=4096
```

The infrastructure checks use small generated instruction sequences only to
check the AXI/start/memory/reset paths. They are separate from the board test
suite, which runs the compiled `tests/regression/demo` and integer `vecadd` sources.

## Software revision compatibility update (2026-10-26)

The regression builder no longer requires software HEAD to equal the hardware
image commit. It evaluates 70 interface constants per selected app using the
current headers and the image's fixed profile: RV32, thread geometry, I/O/memory
map, stack layout, CSR addresses and CSR family IDs. It also rejects software
features absent from the image. No special override flag or older checkout is
required. A native g++ is needed for this small preflight probe.

For an already-installed version of this port, apply only the incremental patch:

```bash
git apply --check /path/to/package/updates/revision_guard_fix.patch
git apply /path/to/package/updates/revision_guard_fix.patch
```

Then rerun the same `build_regressions.py --build ... --out ... --apps demo --type 1`
command. The earlier commit-guard failure happened before creating the output
folder, so that output name is normally still available. Existing output folders
are refused; use a fresh --out name if a later compilation attempt created one.
To check compatibility without the RV32 SDK or building outputs:

```bash
python3 fpga/kv260/build_regressions.py --check-only --apps demo
```

Hardware and software commits, modified software paths, and changed RTL paths
are recorded separately in bundle.json. Compatible source commits are accepted,
but the loaded FPGA continues to implement the RTL from which its bitstream was
built. Interface checks do not prove functional equivalence of later RTL. Rebuild
the FPGA if you intend to run or measure changed hardware behavior.
The original code/TLS bounds and instruction checks remain in force.
No HDL, memory-map, driver, or Vivado change is included in this update.
