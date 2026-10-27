# LFSR-Based DFV Stall Injection

## Overview

This document describes the LFSR (Linear Feedback Shift Register) based Design-for-Verification (DFV) stall injection mechanism implemented in Vortex GPU. The system allows software-controllable, pseudo-random ICache backpressure injection for stress testing the pipeline.

## Motivation

### Why LFSR Instead of Deterministic Stalls?

**Problem with deterministic (always-on) stalls:**
- Complete blocking stalls cause **deadlock** - the release instruction itself cannot be fetched
- Example: `csr_write(ICACHE_STALL, 1)` → ICache blocks → cannot fetch `csr_write(ICACHE_STALL, 0)` → DEADLOCK

**LFSR-based probabilistic stalls solve this:**
- ✅ **Guaranteed forward progress** - stalls are probabilistic, not absolute
- ✅ **Reproducible** - same seed produces same stall pattern
- ✅ **Tunable stress** - adjust threshold to control stall rate
- ✅ **No deadlock** - system can always make progress eventually
- ✅ **Better coverage** - explores many timing scenarios

### Advantages Over Small FIFOs

**Small FIFOs (traditional approach):**
- Require RTL recompilation to change depth
- Test natural backpressure only
- Always active throughout execution

**LFSR-based DFV (this implementation):**
- Runtime controllable via CSRs
- Can target specific code regions
- Adjustable stress levels (0-100% stall rate)
- Same binary for stressed and unstressed tests
- Reproducible for debugging

---

## Architecture

### CSR Registers

| Address | Name | Width | Description |
|---------|------|-------|-------------|
| `0x7C0` | `VX_CSR_DFV_CTRL` | 1-bit | DFV global enable |
| `0x7C1` | `VX_CSR_DFV_ICACHE_STALL` | 1-bit | Enable LFSR-based stall injection |
| `0x7C2` | `VX_CSR_DFV_RANDOM_SEED` | 32-bit | LFSR seed for reproducibility |
| `0x7C3` | `VX_CSR_DFV_STALL_THRESHOLD` | 8-bit | Stall probability (0-255) |

### LFSR Implementation

**Location:** `VX_csr_data.sv`

**Type:** 32-bit Galois LFSR with polynomial: x^32 + x^22 + x^2 + x + 1

**Tap positions:** [31, 21, 1, 0] → polynomial `0xD0000001`

```systemverilog
// LFSR update logic (Galois configuration)
dfv_lfsr <= {1'b0, dfv_lfsr[31:1]} ^ (dfv_lfsr[0] ? 32'hD0000001 : 32'h0);
```

**Properties:**
- Maximum length sequence (period = 2^32 - 1)
- Advances every clock cycle when DFV is active
- Seeded via CSR write
- Lower 8 bits used for stall decision

### Stall Decision Logic

```systemverilog
wire stall = dfv_ctrl_icache_stall && (dfv_lfsr[7:0] < dfv_stall_threshold);
```

**Probability calculation:**
- `threshold = 0` → 0% stall rate (never stalls)
- `threshold = 128` → ~50% stall rate
- `threshold = 192` → ~75% stall rate
- `threshold = 255` → ~100% stall rate

**Why this works:**
- LFSR produces uniformly distributed values in [0, 255] for lower 8 bits
- Comparison `lfsr[7:0] < threshold` gives probability `threshold/256`

---

## Usage

### Basic Example

```cpp
#include "common.h"

// Setup DFV with 50% stall rate
if (blockIdx.x == 0) {
    csr_write(VX_CSR_DFV_CTRL, 1);              // Enable DFV
    csr_write(VX_CSR_DFV_RANDOM_SEED, 0x12345678);  // Fixed seed
    csr_write(VX_CSR_DFV_STALL_THRESHOLD, 128);    // 50% stall rate
    csr_write(VX_CSR_DFV_ICACHE_STALL, 1);       // Enable stall
}

vx_barrier(0, num_tasks);  // Sync all threads

// Computation under stress
for (int i = 0; i < count; i++) {
    output[i] = input[i] + 1;
}

// Cleanup
if (blockIdx.x == 0) {
    csr_write(VX_CSR_DFV_ICACHE_STALL, 0);
    csr_write(VX_CSR_DFV_CTRL, 0);
}
```

### Varying Stress Levels

```cpp
// High stress for first half
csr_write(VX_CSR_DFV_STALL_THRESHOLD, 192);  // 75% stall
for (int i = 0; i < count/2; i++) {
    output[i] = input[i] + 1;
}

vx_barrier(0, num_tasks);

// Lower stress for second half
csr_write(VX_CSR_DFV_STALL_THRESHOLD, 64);   // 25% stall
for (int i = count/2; i < count; i++) {
    output[i] = input[i] + 1;
}
```

### Reproducible vs. Random Testing

**Reproducible (debugging):**
```cpp
// Use fixed seed - same stall pattern every run
csr_write(VX_CSR_DFV_RANDOM_SEED, 0x12345678);
```

**Random (coverage exploration):**
```cpp
// Use varying seed - different pattern each run
uint32_t seed = time() ^ thread_id ^ iteration;
csr_write(VX_CSR_DFV_RANDOM_SEED, seed);
printf("Testing with seed: 0x%x\n", seed);  // Save for reproduce
```

---

## Running Tests

### Build and Run

```bash
cd /path/to/vortex/build/tests/regression/demo

# Rebuild (required after RTL changes)
make clean
make

# Run without DFV (baseline)
make run-rtlsim

# Run with DFV stress testing
LD_LIBRARY_PATH=... VORTEX_DRIVER=rtlsim ./demo -n64 -d
```

### Expected Output

**With DFV enabled:**
```
open device connection
allocate device memory
data type: integer
number of points: 1024
buffer size: 4096 bytes
DFV stress testing: ENABLED (LFSR-based)
  - ICache backpressure injection will be activated by kernel
  - Using LFSR (Linear Feedback Shift Register) for pseudo-random stalls
  - Stall pattern is reproducible (fixed seed: 0x12345678)
  - Stall probability: ~50% initially, ~25% in second half
  - This tests pipeline resilience under random fetch stalls
upload source buffer0
upload source buffer1
Upload kernel binary
upload kernel argument
start device
wait for completion
download destination buffer
verify result
cleanup
PASSED!
```

---

## How It Works: Avoiding Deadlock

### The Key Insight

**LFSR advances every clock cycle**, so the stall pattern keeps changing:

```
Cycle 0: lfsr[7:0] = 0x78 (120) < 128 → STALL
Cycle 1: lfsr[7:0] = 0x3C (60)  < 128 → STALL
Cycle 2: lfsr[7:0] = 0x1E (30)  < 128 → STALL
Cycle 3: lfsr[7:0] = 0x8F (143) > 128 → ALLOW  ← ICache request succeeds!
Cycle 4: lfsr[7:0] = 0xC7 (199) > 128 → ALLOW
...
```

**Even with 99% stall rate**, the system will eventually:
1. Hit a cycle where LFSR produces value > threshold
2. Allow ICache request to proceed
3. Fetch the next instruction
4. Make forward progress

**Guaranteed forward progress** (probabilistic proof):
- Probability of stalling N consecutive cycles = (threshold/256)^N
- For threshold=255: P(100 stalls) = (255/256)^100 ≈ 68%
- For threshold=255: P(1000 stalls) = (255/256)^1000 ≈ 0.02%
- System will eventually make progress with probability approaching 100%

### Why This Is Safe

Unlike deterministic stalls where:
```
csr_write(STALL, 1) → deadlock forever
```

LFSR-based stalls:
```
csr_write(STALL, 1) → LFSR advances → eventually allows request → forward progress
```

---

## Debugging Tips

### When Tests Fail

1. **Check the seed:**
   - If failure is reproducible, the seed is correct
   - If failure is random, add seed logging

2. **Reduce stall rate:**
   ```cpp
   csr_write(VX_CSR_DFV_STALL_THRESHOLD, 32);  // Try 12.5% first
   ```
   Binary search to find minimum threshold that triggers bug

3. **Read LFSR state:**
   ```cpp
   uint32_t seed = csr_read(VX_CSR_DFV_RANDOM_SEED);
   printf("Current LFSR: 0x%x\n", seed);
   ```

4. **Check waveforms:**
   - Look for `dfv_lfsr` signal in VX_csr_data
   - Verify stall pattern matches LFSR values

### Verification Checklist

- [ ] Test passes without DFV (`./demo -n64`)
- [ ] Test passes with DFV low stress (`threshold=32`)
- [ ] Test passes with DFV medium stress (`threshold=128`)
- [ ] Test passes with DFV high stress (`threshold=192`)
- [ ] Same seed produces same results (reproducibility)
- [ ] Different seeds produce different stall patterns

---

## Implementation Details

### File Changes

**RTL:**
- `VX_csr_data.sv` - Added LFSR logic and CSR registers
- `VX_csr_unit.sv` - Pass-through (no changes needed)
- `VX_sfu_unit.sv` - Pass-through (no changes needed)
- `VX_execute.sv` - Pass-through (no changes needed)
- `VX_core.sv` - Internal wiring (no changes needed)
- `VX_fetch.sv` - Uses `dfv_stall_icache_req` signal (existing)

**Software:**
- `common.h` - Added new CSR definitions
- `kernel.cpp` - Updated to use LFSR-based stall pattern
- `main.cpp` - Updated help text and status output

### LFSR Polynomial Choice

**Chosen:** `0xD0000001` (x^32 + x^22 + x^2 + x + 1)

**Why this polynomial:**
- Maximum length (period = 2^32 - 1)
- Good statistical properties
- Balanced tap distribution
- Commonly used in hardware (CRC, scrambling)

**Alternative polynomials:**
- `0x80200003` (x^32 + x^26 + x^2 + x + 1) - also valid
- `0x80000057` (x^32 + x^7 + x^5 + x^3 + x^2 + x + 1) - more taps

All produce maximum-length sequences, choice is arbitrary.

---

## Performance Impact

### Without DFV (baseline)
- No overhead
- DFV logic is gated by `dfv_ctrl_enable`
- Zero timing/area impact when disabled

### With DFV enabled
- LFSR advances every cycle (1 XOR + 1 shift)
- Stall comparison (1 8-bit comparison)
- **Minimal logic overhead** (~32 FFs + 1 XOR gate + 1 comparator)

### Execution Time Impact

Depends on stall threshold:
- `threshold=0`: No slowdown
- `threshold=128`: ~2x slowdown (50% of cycles stalled)
- `threshold=192`: ~4x slowdown (75% of cycles stalled)
- `threshold=255`: ~256x slowdown (99.6% of cycles stalled)

**Note:** Actual slowdown varies based on:
- ICache hit rate
- Pipeline occupancy
- Instruction mix

---

## Comparison: Deterministic vs. LFSR

| Aspect | Deterministic Periodic | LFSR Pseudo-Random |
|--------|----------------------|-------------------|
| **Reproducibility** | ✅ Perfect | ✅ Perfect (with seed) |
| **Deadlock Risk** | ⚠️ Possible | ✅ No risk |
| **Coverage** | ❌ Single pattern | ✅ Many patterns |
| **Debugging** | ✅ Easy (waveforms clear) | ⚠️ Harder (noisy waveforms) |
| **Resonance** | ⚠️ Can sync with design | ✅ Avoids resonance |
| **Implementation** | ✅ Simple counter | ⚠️ More complex (LFSR) |
| **Industry Use** | Rare | ✅ Common |

**Verdict:** LFSR is superior for safety and coverage, worth the complexity.

---

## Future Enhancements

### Possible Extensions

1. **Multiple LFSR instances:**
   - Separate LFSRs for ICache, DCache, writeback
   - Independent stall patterns per component

2. **Burst mode:**
   - Stall for N consecutive cycles when triggered
   - Test sustained backpressure scenarios

3. **Correlated stalls:**
   - Stall multiple components simultaneously
   - Test multi-resource contention

4. **Observability counters:**
   ```systemverilog
   reg [31:0] stall_count;      // Count stall events
   reg [31:0] total_cycles;     // Total cycles with DFV active
   ```

5. **Formal verification properties:**
   ```systemverilog
   // Assert: LFSR never gets stuck at zero
   assert property (@(posedge clk) dfv_lfsr != 0);

   // Assert: Eventually makes progress (bounded liveness)
   assert property (@(posedge clk) icache_req |-> ##[1:1000] icache_grant);
   ```

---

## References

### LFSR Theory
- Golomb, S. W. (1982). *Shift Register Sequences*. Aegean Park Press.
- Xilinx XAPP052: "Efficient Shift Registers, LFSR Counters, and Long Pseudo-Random Sequence Generators"

### GPU Verification
- Industry practice: Pseudo-random stimulus generation
- UVM (Universal Verification Methodology) - constraint-random testing

### Vortex Documentation
- `MISALIGNMENT_FIX.md` - Race condition fixed by barriers
- `SEGFAULT_FIX.md` - Register initialization importance
- `README_DFV.md` - Original DFV documentation

---

## Summary

The LFSR-based DFV stall injection provides:

✅ **Safe** - No deadlock risk, guaranteed forward progress
✅ **Reproducible** - Same seed → same behavior
✅ **Tunable** - Adjust stall rate from 0-100%
✅ **Effective** - Exposes timing bugs and race conditions
✅ **Flexible** - Runtime control without RTL recompilation

This is a production-quality verification feature suitable for:
- Automated regression testing
- Stress testing new features
- Reproducing timing-dependent bugs
- Academic research and teaching
