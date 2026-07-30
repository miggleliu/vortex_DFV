#ifndef _COMMON_H_
#define _COMMON_H_

#include <stdint.h>

//==============================================================================
// Cache geometry constants -- MUST match the RTL config this runs against
// (see VX_config.vh). Override at build time if your config differs, e.g.:
//   make CONFIGS="-DFD_NUM_BANKS=8 -DFD_LINE_SIZE=64" run-rtlsim
//==============================================================================
// Prefixed FD_ (FrontDoor) to avoid colliding with any macro already defined
// by Vortex's own generated build/hw/VX_config.h (which vortex.h pulls in on
// the host side, and which defines its own same-named-but-differently-scoped
// DCACHE_NUM_BANKS -- discovered the hard way: that macro references
// DCACHE_NUM_REQS, which isn't available in the generated header, so an
// #ifndef guard here silently deferred to the broken macro and failed to
// compile at first use).
#ifndef FD_NUM_BANKS
#define FD_NUM_BANKS 4   // VX_config.vh default: MIN(DCACHE_NUM_REQS,16), NUM_THREADS=4 -> 4
#endif

#ifndef FD_LINE_SIZE
#define FD_LINE_SIZE 64  // VX_config.vh: MEM_BLOCK_SIZE
#endif

// All probe threads target the SAME bank so every thread's fill/probe pair
// can collide with every OTHER thread's pair at one shared bank.
#ifndef FD_TARGET_BANK
#define FD_TARGET_BANK 0
#endif

// bank = (byte_addr / FD_LINE_SIZE) % FD_NUM_BANKS  (VX_cache.sv:341-346:
// bank_id = addr[WORD_SEL_BITS +: BANK_SEL_BITS], i.e. the bits directly
// above the in-line word offset). Adding any multiple of FD_BANK_STRIDE to
// an address keeps it in the same bank while moving to a brand-new cache line.
#define FD_BANK_STRIDE (FD_LINE_SIZE * FD_NUM_BANKS)

typedef struct {
  uint32_t num_threads;    // number of probe threads == number of sweep points
  uint32_t delay_step;     // software delay-loop granularity (cycles, approximate)
  uint32_t bank_align_pad; // bytes added to buf_addr so offset 0 actually lands
                           // on FD_TARGET_BANK -- bank selection is on the
                           // ABSOLUTE address, not an offset relative to the
                           // buffer, and vx_mem_alloc's returned base address
                           // is not guaranteed to be bank-0-aligned. Computed
                           // at runtime in main.cpp once the real buf_addr is
                           // known; kernel.cpp just adds it to every offset.
  uint64_t buf_addr;
  uint64_t dst_addr;
} kernel_arg_t;

#endif
