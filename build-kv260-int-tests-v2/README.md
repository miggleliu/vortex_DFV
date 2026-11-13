# KV260 integer-only DFV package

Base repository: main 0846375201fc3e8b3717c2f7e1065be7c3b40e2c.
This replaces the FPU profile with an integer-only profile while keeping runtime
host CSR programming, the larger memory map, and I/O-address test support.

## Enabled workloads

Original integer/character apps: basic, demo, printf, sort, cta, diverge, fence,
io_addr. Integer variants: vecadd, relu, sgemm, sgemm2, stencil3d.
The shared app_configs() function in tests/run_dfv_combos.py supplies -DTYPE=int
to BOTH the CPU reference/host build and GPU build for the five variants. This
works in the board builder and the RTL simulation runner. No per-combination
kernel patching or CSR values compiled into kernels are required.

The existing CPU checkers already support Comparator<int>. Integer variants
compare against their own integer references, not the previous floating-point
results. Their random generators now produce -8..8 values, avoiding signed
sum/product overflow at the configured sizes and exercising negative ReLU data.
Floating-point generation/checking code remains available in the source.

Commented out of TEST_APPS: sgemv, conv3, dogfood, dotproduct, dropout, madmax,
mstress. These contain explicit FP operations or mixed FP subtests and cannot
be made integer-only simply by changing TYPE. sgemm_tcu remains disabled because
this profile has no tensor core. Dedicated dfv_stress/frontdoor_race flows are
outside the configured combo suite and are unchanged.

## Existing bitstream compatibility

Use a new Vivado build for this package. The original 256 KiB integer image is
still usable with its old software, but does not provide this profile's enlarged
code/heap map and host I/O preload support. The preceding FPU package must not be
used unchanged. The new integer image is named kv260-vortex-int; ABI 0x00050000
and FPU capability register 0x54=0 distinguish it from all earlier images.

Hardware remains 1 RV32 integer core, 4 warps, 4 threads/warp, 4 D-cache banks,
4 KiB I/D caches, 16 KiB local memory, nominal 100 MHz. EXT_F_DISABLE is enabled.
No FPU floating-point IP is generated. Backing BRAM remains 384 KiB: 128 KiB
code/TLS, 128 KiB heap, 128 KiB stacks. The backing memory alone is expected to
use 96 RAMB36 tiles; total utilization/timing requires your Vivado report.
There is no TCU, L2/L3 or GPU DDR backend.

## Apply on Ubuntu VM

Choose ONE supplied patch:
- patches/from_main_0846375.patch if you have not applied the all-20/FPU update.
- patches/from_all20_fpu_package.patch if you already applied that exact update.

From the repository root, run git apply --check PATH, then git apply PATH for
only the selected patch. If it conflicts, merge the indicated hunks and preserve
local edits. files/ contains reference copies, not a complete repository.

## Build test software on the VM

Use your RV32 SDK and ARM64 Linux compiler (aarch64-linux-gnu-g++):

```sh
mkdir build-kv260-int-sdk
cd build-kv260-int-sdk
../configure --xlen=32 --tooldir="$HOME/tools"
cd ..
python3 fpga/kv260/build_regressions.py \
  --build build-kv260-int-sdk --out build-kv260-int-tests --types 1 2 3
```

Omitting --apps builds all 13 enabled apps. --apps vecadd relu selects a subset.
Each app compiles once for the current 130 runtime cases (3/3/4 scenarios per
app). Kernel ELF checks reject FP loads/stores/arithmetic as well as unsupported
atomic/vector/compressed instructions. The existing SDK calling convention is
retained for library compatibility; the instruction audit determines whether
a GPU binary is integer-only. Kernel compilation has not run here without the
Vortex SDK. Oversized images or heap allocations fail explicitly.

For the existing VM simulation, use a configured RV32 build, set matching
CONFIGS including EXT_F_DISABLE, rebuild the runtime/RTL and clean regression
outputs once when switching profile:

```sh
export CONFIGS="$(python3 -c 'import json; print(" ".join("-D"+x for x in json.load(open("fpga/kv260/config.json"))["defines"]))')"
cd build
make -C runtime/stub clean
make -C runtime/stub
make -C runtime/rtlsim clean
make -C runtime/rtlsim
make -C tests/regression clean
cd ..
python3 tests/run_dfv_combos.py --type 1 --apps vecadd relu --csr release_delay=0
```

The runner appends -DTYPE=int automatically for the five variants, to the shared
CONFIGS passed through blackbox.sh to host and kernel compilation. Do not add
TYPE=int only to one side. Match cache/topology/workload settings when comparing
simulation and FPGA; simulated DRAM and FPGA BRAM timing still differ.

## Vivado on Windows

Copy the contents of vivado/ in the ZIP into a NEW folder, for example
C:/Xilinx/Vivado/vortex-int. Save/close the old project, then:

```tcl
source C:/Xilinx/Vivado/vortex-int/create_project.tcl
launch_runs synth_1 -jobs 4
wait_on_run synth_1
open_run synth_1
report_utilization -file C:/Xilinx/Vivado/vortex-int/utilization_synth.rpt
source C:/Xilinx/Vivado/vortex-int/build_bitstream.tcl
```

Check synthesis fit and routed timing/DRC. No bitstream or physical fit/timing
result is included. To regenerate the prepared sources on Ubuntu instead:

```sh
python3 fpga/kv260/prepare.py --out build-kv260-int-vivado
```

## Load and run on the board

Transfer artifacts/kv260-vortex-int.bit and the prepared vortex.bif,
kv260-vortex.dts and shell.json through the Mac. From their directory:

```sh
bootgen -arch zynqmp -image vortex.bif -process_bitstream bin -w
dtc -@ -I dts -O dtb -o kv260-vortex-int.dtbo kv260-vortex.dts
sudo mkdir -p /lib/firmware/xilinx/kv260-vortex-int
sudo cp kv260-vortex-int.bit.bin kv260-vortex-int.dtbo shell.json \
  /lib/firmware/xilinx/kv260-vortex-int/
sudo xmutil unloadapp
sudo xmutil loadapp kv260-vortex-int
sudo xmutil listapps
cat /sys/class/fpga_manager/fpga0/state
find /sys/devices/platform -path '*vortex-clock*/set_rate' -print -exec cat {} \;
```

After confirming the new image is active/operating and clock near 100 MHz,
transfer the entire build-kv260-int-tests bundle and run:

```sh
cd ~/build-kv260-int-tests
python3 run_board.py --list --apps vecadd --type 1
sudo python3 -u run_board.py --apps vecadd --type 1 --csr release_delay=0 --out results-vecadd
sudo python3 -u run_board.py --types 1 2 3 --out results-all-int
```

Settings remain programmable on the ARM host at runtime, without rebuilding
kernels or bitstreams. --csr set_threshold=0xe000, --csr random_seed=0xabcdef00,
--csr bank_throttle_threshold=256 and --dfv-config JSON work as before. Raw
addresses also work: --csr 0x7c9=0. The same C APIs vx_dfv_csr_write/read remain.
This is staged launch-time programming: the shared GPU helper writes actual
DFV CSRs before BEGIN; setup-time core-0 readback is verified afterward. It is
not asynchronous live CSR access during execution. Observability stays in the
existing indexed GPU CSR families; no extra ARM DFV mirror register is needed.

The common runtime reserves a 192-byte mailbox at USER_BASE_ADDR (0x10000).
GPU addresses: code 0x80000000..0x8001ffff, heap 0x10000..0x2ffff, stacks
0xfffd0000..0xfffeffff. ARM RAM offsets: 0x40000..0x9ffff, in a 1 MiB aperture.
Host I/O preload/readback: GPU 0x80..0x17f maps to ARM offsets 0x1000..0x10ff.

## Validation and limits

Native tests execute the actual five integer workload bodies and their original
CPU references for 10 bounded-input trials per app, including tiled SGEMM2
barriers and stencil boundaries, under undefined-behavior sanitization. These
passed. This is not a physical GPU result or a guarantee that arbitrary data or
workload sizes cannot overflow. All 13 interface probes and shared software
checks are provided; native driver and RTL wrapper checks are reproducible:

```sh
python3 fpga/kv260/tests/test_integer_workloads.py
python3 fpga/kv260/tests/run_checks.py --work build-kv260-int-validation
```

Remaining acceptance steps: compile GPU/ARM64 binaries with your VM SDK,
complete Vivado implementation, and run functional/CSR checks on your board.
A valid ZERO_EVENTS result is zero coverage, not a failed functional comparison.
