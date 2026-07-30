#include <vx_spawn.h>
#include "common.h"

// Frontdoor counterpart to tests/regression/dfv_stress phase1
// (dcache_fill_rsp x dcache_core_req bank collision) -- NO DFV CSRs are
// touched anywhere in this file. The only knob available to a pure software
// test is this delay loop; see memory/frontdoor_vs_dfv.md for why that makes
// hitting the exact same-cycle target a wide, state-dependent search rather
// than a precise dial like DFV's shared-LFSR2 release.
static inline void spin_delay(uint32_t iters) {
  for (volatile uint32_t d = 0; d < iters; ++d) {
    asm volatile("" ::: "memory");
  }
}

void kernel_body(kernel_arg_t* __UNIFORM__ arg) {
  auto base = reinterpret_cast<volatile uint32_t*>(arg->buf_addr);
  auto dst  = reinterpret_cast<uint32_t*>(arg->dst_addr);

  uint32_t tid = blockIdx.x;
  uint32_t bank_stride_words = FD_BANK_STRIDE / sizeof(uint32_t);
  uint32_t line_words        = FD_LINE_SIZE / sizeof(uint32_t);
  // bank_align_pad (computed on the host from the REAL buf_addr, since bank
  // selection is on the absolute address, not an offset relative to this
  // buffer) shifts every offset below so bank_align_pad+0 actually lands on
  // FD_TARGET_BANK, regardless of where the allocator placed the buffer.
  uint32_t pad_words = arg->bank_align_pad / sizeof(uint32_t);

  // Two distinct cache lines per thread, both forced to FD_TARGET_BANK via
  // the FD_BANK_STRIDE offset, and never reused across threads (2*tid, 2*tid+1).
  uint32_t miss_off  = pad_words + (FD_TARGET_BANK * line_words) + (2 * tid)     * bank_stride_words;
  uint32_t probe_off = pad_words + (FD_TARGET_BANK * line_words) + (2 * tid + 1) * bank_stride_words;

  // 1) Cold load -> guaranteed dcache miss -> triggers a fill request to
  //    FD_TARGET_BANK. The fill response's arrival cycle is set by DRAM/L2
  //    state (measured ~1000-5025cc range), not observable or directly
  //    controllable from software.
  uint32_t miss_val = base[miss_off];

  // 2) Software delay, linearly swept across threads: thread tid waits
  //    ~tid * delay_step (approximate) cycles. A single kernel launch thus
  //    samples num_threads points across the uncertainty window at once,
  //    instead of needing a separate recompiled run per candidate delay.
  spin_delay(tid * arg->delay_step);

  // 3) Second load to a DIFFERENT line in the SAME bank -> a fresh
  //    dcache_core_req. If it lands on the exact cycle (1)'s fill response
  //    arrives, VX_dfv_collision_ctr logs "DFV_COLLISION_NATURAL" for this
  //    bank. DFV is never enabled here, so every such log line is a genuine
  //    frontdoor hit, not an injected one.
  uint32_t probe_val = base[probe_off];

  dst[tid] = miss_val + probe_val;
}

int main() {
  kernel_arg_t* arg = (kernel_arg_t*)csr_read(VX_CSR_MSCRATCH);
  // Intentionally NO DFV CSR writes anywhere in this test.
  return vx_spawn_threads(1, &arg->num_threads, nullptr, (vx_kernel_func_cb)kernel_body, arg);
}
