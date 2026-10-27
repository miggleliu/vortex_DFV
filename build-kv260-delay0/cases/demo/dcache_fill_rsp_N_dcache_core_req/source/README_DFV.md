# Vortex Demo with DFV (Design-for-Verification) Testing

This demo demonstrates the Vortex GPGPU vector addition kernel with integrated DFV stress testing capabilities.

## Overview

The demo has been enhanced to support **DFV hooks** that allow software-controlled hardware verification. Specifically, it can inject **ICache backpressure** to stress-test the pipeline's ability to handle fetch stalls without deadlock.

## Files

- **common.h**: Shared definitions including DFV CSR addresses and kernel arguments
- **kernel.cpp**: GPU kernel with DFV stress testing logic
- **main.cpp**: Host code with command-line DFV control
- **README_DFV.md**: This file

## DFV CSR Addresses

```c
#define VX_CSR_DFV_CTRL          0x7C0  // DFV global enable (bit 0)
#define VX_CSR_DFV_ICACHE_STALL  0x7C1  // ICache request stall (bit 0)
```

These CSRs are implemented in hardware (VX_csr_data.sv) and allow the kernel to control verification hooks.

## Usage

### Normal Operation (No DFV)

```bash
./demo -n 32
```

This runs the standard vector addition test without DFV stress testing.

### DFV Stress Testing Mode

```bash
./demo -n 32 -d
```

**What happens:**
1. Kernel enables DFV globally (`VX_CSR_DFV_CTRL = 1`)
2. Kernel activates ICache stall (`VX_CSR_DFV_ICACHE_STALL = 1`)
3. First half of computation executes under ICache backpressure
4. Kernel releases stall (`VX_CSR_DFV_ICACHE_STALL = 0`)
5. Second half completes with normal operation
6. Kernel disables DFV (`VX_CSR_DFV_CTRL = 0`)

### Command-Line Options

```
Usage: [-k: kernel] [-n words] [-d: enable DFV test] [-h: help]
  -k <file>   : Specify kernel binary (default: kernel.vxbin)
  -n <count>  : Elements per thread (default: 16)
  -d          : Enable DFV stress testing (icache backpressure)
  -h          : Show this help message
```

## How DFV Testing Works

### Hardware Side (RTL)

When `VX_CSR_DFV_ICACHE_STALL = 1`:

```
VX_csr_data.sv (CSR storage)
     │
     ▼
VX_csr_unit.sv → VX_sfu_unit.sv → VX_execute.sv → VX_core.sv
     │
     ▼
VX_fetch.sv intercepts icache_bus_if.req_ready
     │
     ▼ Forces to 0
ICache cannot accept requests → Backpressure to scheduler
```

### Software Side (Kernel)

```cpp
// Thread 0 controls DFV to avoid race conditions
if (blockIdx.x == 0) {
    vx_csr_write(VX_CSR_DFV_CTRL, 1);           // Enable DFV
    vx_csr_write(VX_CSR_DFV_ICACHE_STALL, 1);   // Inject stall
}

// All threads execute under stress
for (int i = 0; i < count/2; i++) {
    dst[i] = src0[i] + src1[i];  // Fetch requests will stall
}

if (blockIdx.x == 0) {
    vx_csr_write(VX_CSR_DFV_ICACHE_STALL, 0);   // Release stall
}
```

## Verification Properties Tested

### 1. **No Deadlock Under ICache Stall**
- Pipeline correctly handles fetch backpressure
- Scheduler stops issuing when fetch is blocked
- No circular dependencies between fetch and other stages

### 2. **Correctness Under Stress**
- Results remain correct even with DFV injection
- Demonstrates functional equivalence when DFV is disabled/enabled

### 3. **Pipeline Resilience**
- System recovers gracefully when stall is released
- No instruction loss or corruption during stall

## Expected Output

### Normal Mode
```
Vortex Test.
data type: integer
number of points: 512
buffer size: 2048 bytes
DFV stress testing: DISABLED (use -d to enable)
...
PASSED!
```

### DFV Mode
```
Vortex Test.
data type: integer
number of points: 512
buffer size: 2048 bytes
DFV stress testing: ENABLED
  - ICache backpressure injection will be activated by kernel
  - This tests pipeline resilience under fetch stalls
...
PASSED!
```

## Cross-Platform Testing

The same test runs on:
- **Pre-silicon**: RTL simulation (ModelSim, VCS, Verilator)
- **Emulation**: FPGA-based emulation
- **Post-silicon**: Actual hardware

DFV hooks are memory-mapped CSRs accessible from software on all platforms.

## Adding More DFV Tests

To add additional DFV hooks:

1. **Define CSR address** in common.h:
   ```c
   #define VX_CSR_DFV_NEW_HOOK  0x7C2
   ```

2. **Implement in RTL** (VX_csr_data.sv):
   ```systemverilog
   12'h7C2: dfv_new_hook <= write_data[0];
   ```

3. **Use in kernel**:
   ```cpp
   vx_csr_write(VX_CSR_DFV_NEW_HOOK, 1);
   ```

## Notes

- DFV hooks are **LEC-friendly**: Zero logic impact when disabled
- Only thread 0 should write DFV CSRs to avoid conflicts
- Always disable DFV at kernel end to avoid affecting other kernels
- Results must be identical with/without DFV (functional equivalence)

## References

- VX_csr_data.sv: CSR register implementation
- VX_fetch.sv: ICache backpressure injection point
- VX_dfv_primitives.sv: Reusable DFV building blocks
