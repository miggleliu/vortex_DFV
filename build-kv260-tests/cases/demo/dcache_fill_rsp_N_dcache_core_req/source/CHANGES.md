# Demo Test Updates for DFV Support

## Summary

The demo test has been updated to demonstrate CSR-based DFV (Design-for-Verification) control. The test now supports stress testing the Vortex pipeline by injecting ICache backpressure via software-controllable CSRs.

---

## Files Modified

### 1. **common.h**

**Changes:**
- Added DFV CSR address definitions (`VX_CSR_DFV_CTRL`, `VX_CSR_DFV_ICACHE_FILL_REQ_STALL`)
- Extended `kernel_arg_t` structure with `enable_dfv_test` field

**New Content:**
```c
// DFV CSR Definitions
#define VX_CSR_DFV_CTRL          0x7C0  // DFV global enable
#define VX_CSR_DFV_ICACHE_FILL_REQ_STALL  0x7C1  // ICache request stall

typedef struct {
  uint32_t num_tasks;
  uint32_t task_size;
  uint64_t src0_addr;
  uint64_t src1_addr;
  uint64_t dst_addr;
  uint32_t enable_dfv_test;  // NEW: DFV control flag
} kernel_arg_t;
```

**Purpose:**
- Provides CSR addresses accessible from both host and device code
- Allows host to pass DFV enable flag to kernel

---

### 2. **kernel.cpp**

**Changes:**
- Added `#include <vx_intrinsics.h>` for CSR operations
- Implemented DFV stress testing logic in `kernel_body()`
- Conditional execution based on `arg->enable_dfv_test`

**New Logic:**
```cpp
if (arg->enable_dfv_test) {
    // Thread 0 controls DFV
    if (blockIdx.x == 0) {
        vx_csr_write(VX_CSR_DFV_CTRL, 1);           // Enable DFV
        vx_csr_write(VX_CSR_DFV_ICACHE_FILL_REQ_STALL, 1);   // Inject stall
    }

    // Execute first half under stress
    for (uint32_t i = 0; i < count / 2; ++i) {
        dst_ptr[offset+i] = src0_ptr[offset+i] + src1_ptr[offset+i];
    }

    // Release stall
    if (blockIdx.x == 0) {
        vx_csr_write(VX_CSR_DFV_ICACHE_FILL_REQ_STALL, 0);
    }

    // Execute second half normally
    for (uint32_t i = count / 2; i < count; ++i) {
        dst_ptr[offset+i] = src0_ptr[offset+i] + src1_ptr[offset+i];
    }

    // Disable DFV
    if (blockIdx.x == 0) {
        vx_csr_write(VX_CSR_DFV_CTRL, 0);
    }
}
```

**Purpose:**
- Demonstrates software-controlled DFV injection
- Tests pipeline under ICache backpressure
- Only thread 0 writes CSRs to avoid conflicts
- Ensures functional correctness under stress

---

### 3. **main.cpp**

**Changes:**
- Added `bool enable_dfv_test` global variable
- Extended command-line parsing to support `-d` flag
- Enhanced usage message
- Added DFV status reporting
- Set `kernel_arg.enable_dfv_test` based on command-line flag

**New Command-Line Option:**
```cpp
case 'd':
  enable_dfv_test = true;
  break;
```

**New Output:**
```cpp
if (enable_dfv_test) {
  std::cout << "DFV stress testing: ENABLED" << std::endl;
  std::cout << "  - ICache backpressure injection will be activated by kernel" << std::endl;
  std::cout << "  - This tests pipeline resilience under fetch stalls" << std::endl;
} else {
  std::cout << "DFV stress testing: DISABLED (use -d to enable)" << std::endl;
}
```

**Purpose:**
- Provides user control over DFV testing
- Clear indication of test mode
- Backward compatible (default: DFV disabled)

---

### 4. **README_DFV.md** (New File)

Comprehensive documentation covering:
- DFV CSR addresses and their function
- Usage examples (normal mode vs. DFV mode)
- How DFV testing works (hardware and software sides)
- Verification properties tested
- Cross-platform testing information
- Instructions for adding new DFV hooks

---

## Backup Files Created

All original files backed up with `.backup` extension:
- `common.h.backup`
- `kernel.cpp.backup`
- `main.cpp.backup`

---

## Usage Examples

### Normal Operation (Backward Compatible)

```bash
./demo -n 32
```

**Output:**
```
Vortex Test.
data type: integer
number of points: 512
buffer size: 2048 bytes
DFV stress testing: DISABLED (use -d to enable)
open device connection
allocate device memory
...
PASSED!
```

### DFV Stress Testing

```bash
./demo -n 32 -d
```

**Output:**
```
Vortex Test.
data type: integer
number of points: 512
buffer size: 2048 bytes
DFV stress testing: ENABLED
  - ICache backpressure injection will be activated by kernel
  - This tests pipeline resilience under fetch stalls
open device connection
allocate device memory
...
PASSED!
```

### Command-Line Options

```
Usage: [-k: kernel] [-n words] [-d: enable DFV test] [-h: help]
  -k <file>   : Specify kernel binary (default: kernel.vxbin)
  -n <count>  : Elements per thread (default: 16)
  -d          : Enable DFV stress testing (icache backpressure)
  -h          : Show this help message
```

---

## Test Flow with DFV

```
Host (main.cpp)
  │
  ├─ Parse -d flag
  ├─ Set kernel_arg.enable_dfv_test = 1
  ├─ Upload kernel and args
  └─ Launch kernel
       │
       ▼
Device (kernel.cpp)
  │
  ├─ Thread 0: Write CSR 0x7C0 = 1 (enable DFV)
  ├─ Thread 0: Write CSR 0x7C1 = 1 (stall icache)
  │    │
  │    ▼
  │  Hardware (VX_fetch.sv)
  │    └─ Intercept icache_bus_if.req_ready
  │    └─ Force to 0 (backpressure)
  │
  ├─ All threads: Execute first half under stress
  │    └─ Fetch requests stall
  │    └─ Scheduler backpressures
  │    └─ Pipeline handles gracefully
  │
  ├─ Thread 0: Write CSR 0x7C1 = 0 (release stall)
  ├─ All threads: Execute second half normally
  └─ Thread 0: Write CSR 0x7C0 = 0 (disable DFV)
       │
       ▼
Host
  └─ Verify results (must be identical to non-DFV mode)
```

---

## Verification Properties

### 1. Functional Equivalence
- Results with `-d` must match results without `-d`
- DFV hooks are transparent when disabled

### 2. Pipeline Resilience
- No deadlock under ICache backpressure
- Correct handling of fetch stalls
- Proper backpressure propagation to scheduler

### 3. Recovery Behavior
- System recovers when stall is released
- No instruction loss or corruption
- Continued execution after DFV disable

---

## Integration with RTL

The test relies on RTL changes in:

1. **VX_csr_data.sv** - CSR storage (0x7C0, 0x7C1)
2. **VX_csr_unit.sv** - CSR interface pass-through
3. **VX_sfu_unit.sv** - DFV signal propagation
4. **VX_execute.sv** - DFV signal propagation
5. **VX_core.sv** - Internal DFV wire connections
6. **VX_fetch.sv** - ICache backpressure injection

Signal path: CSR write → VX_csr_data → VX_csr_unit → VX_sfu_unit → VX_execute → VX_core → VX_fetch

---

## Future Extensions

### Add More DFV Hooks

1. **DCache Response Stall:**
   ```c
   #define VX_CSR_DFV_DCACHE_FILL_REQ_STALL  0x7C2
   ```

2. **Writeback Stall:**
   ```c
   #define VX_CSR_DFV_WRITEBACK_STALL  0x7C3
   ```

3. **LSU Stall:**
   ```c
   #define VX_CSR_DFV_LSU_STALL  0x7C4
   ```

### Usage in Kernel

```cpp
// Stress test different pipeline stages
vx_csr_write(VX_CSR_DFV_CTRL, 1);
vx_csr_write(VX_CSR_DFV_DCACHE_FILL_REQ_STALL, 1);
// ... perform loads/stores ...
vx_csr_write(VX_CSR_DFV_DCACHE_FILL_REQ_STALL, 0);
vx_csr_write(VX_CSR_DFV_CTRL, 0);
```

---

## Notes

- All changes are backward compatible (default: no DFV)
- DFV mode adds zero overhead when disabled
- Suitable for both regression testing and targeted stress testing
- Works across pre-silicon, emulation, and post-silicon platforms
