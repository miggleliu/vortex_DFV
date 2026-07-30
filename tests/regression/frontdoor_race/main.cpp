#include <iostream>
#include <cstdio>
#include <cstdlib>
#include <unistd.h>
#include <vector>
#include <vortex.h>
#include "common.h"

#define RT_CHECK(_expr)                                         \
   do {                                                         \
     int _ret = _expr;                                          \
     if (0 == _ret)                                             \
       break;                                                   \
     printf("Error: '%s' returned %d!\n", #_expr, (int)_ret);   \
     cleanup();                                                 \
     exit(-1);                                                  \
   } while (false)

///////////////////////////////////////////////////////////////////////////////
//
// Frontdoor Race Test (no DFV)
//
// Software-only attempt at the same target as tests/regression/dfv_stress
// phase1: a same-cycle collision between a dcache fill response and a new
// dcache core request at one cache bank (VX_dfv_collision_ctr). This test
// never touches any DFV CSR -- see memory/frontdoor_vs_dfv.md for the
// reasoning behind why a directed software test has to resort to sweeping a
// coarse delay knob across many threads instead of a precise dial.
//
// Signal to check after a run: grep "DFV_COLLISION_NATURAL" in run.log.
// A functional PASS here only means the loads/stores executed correctly --
// it does NOT mean the race was ever hit. The collision count is the metric.
//
///////////////////////////////////////////////////////////////////////////////

const char* kernel_file = "kernel.vxbin";
uint32_t num_threads = 64;
uint32_t delay_step  = 80;  // default sweep: 0..(63*80)=5040cc, covers measured ~1000-5025cc DRAM latency range

vx_device_h device = nullptr;
vx_buffer_h buf_buffer  = nullptr;
vx_buffer_h dst_buffer  = nullptr;
vx_buffer_h krnl_buffer = nullptr;
vx_buffer_h args_buffer = nullptr;
kernel_arg_t kernel_arg = {};

static void show_usage() {
  std::cout << "Frontdoor Race Test (no DFV)." << std::endl;
  std::cout << "Tries to naturally trigger the dcache_fill_rsp x dcache_core_req" << std::endl;
  std::cout << "bank collision by sweeping a per-thread software delay." << std::endl;
  std::cout << "After running, check run.log for 'DFV_COLLISION_NATURAL' lines." << std::endl;
  std::cout << "Usage: [-k kernel] [-n threads] [-t delay_step] [-h help]" << std::endl;
}

static void parse_args(int argc, char **argv) {
  int c;
  while ((c = getopt(argc, argv, "n:t:k:h")) != -1) {
    switch (c) {
    case 'n':
      num_threads = atoi(optarg);
      break;
    case 't':
      delay_step = atoi(optarg);
      break;
    case 'k':
      kernel_file = optarg;
      break;
    case 'h':
      show_usage();
      exit(0);
      break;
    default:
      show_usage();
      exit(-1);
    }
  }
}

void cleanup() {
  if (device) {
    vx_mem_free(buf_buffer);
    vx_mem_free(dst_buffer);
    vx_mem_free(krnl_buffer);
    vx_mem_free(args_buffer);
    vx_dev_close(device);
  }
}

int main(int argc, char *argv[]) {
  parse_args(argc, argv);

  std::cout << "Frontdoor Race Test (no DFV)" << std::endl;
  std::cout << "  threads=" << num_threads << ", delay_step=" << delay_step
            << ", target_bank=" << FD_TARGET_BANK << " (of " << FD_NUM_BANKS << ")"
            << std::endl;
  std::cout << "  approx sweep range: [0, " << (num_threads - 1) * delay_step
            << "] cycles" << std::endl;

  RT_CHECK(vx_dev_open(&device));

  // Two distinct cache lines per thread, both forced to the same bank via
  // FD_BANK_STRIDE. Total footprint is intentionally >> DCACHE_SIZE (16KB
  // default) so every access is a genuine cold miss, not a capacity/way
  // conflict artifact. Extra FD_NUM_BANKS*FD_LINE_SIZE headroom covers the
  // worst-case bank-alignment padding computed below.
  uint32_t buf_size = 2 * num_threads * FD_BANK_STRIDE + FD_LINE_SIZE
                       + FD_NUM_BANKS * FD_LINE_SIZE;
  uint32_t dst_size = num_threads * sizeof(uint32_t);
  std::cout << "  buffer size: " << buf_size << " bytes" << std::endl;

  RT_CHECK(vx_mem_alloc(device, buf_size, VX_MEM_READ, &buf_buffer));
  RT_CHECK(vx_mem_address(buf_buffer, &kernel_arg.buf_addr));
  RT_CHECK(vx_mem_alloc(device, dst_size, VX_MEM_WRITE, &dst_buffer));
  RT_CHECK(vx_mem_address(dst_buffer, &kernel_arg.dst_addr));

  // Bank selection in RTL (VX_cache.sv) operates on the ABSOLUTE address,
  // not an offset relative to this buffer -- vx_mem_alloc's returned base
  // address is not guaranteed to be bank-0-aligned (or even line-aligned).
  // Compute how many bytes to pad so that (buf_addr + bank_align_pad) is a
  // line-aligned address whose bank is exactly FD_TARGET_BANK; every
  // per-thread offset in the kernel then adds this same pad, preserving the
  // "same bank for all threads" invariant regardless of where the allocator
  // actually placed the buffer.
  uint64_t base_addr       = kernel_arg.buf_addr;
  uint32_t misalign        = (uint32_t)(base_addr % FD_LINE_SIZE);
  uint32_t to_line_boundary = misalign ? (FD_LINE_SIZE - misalign) : 0;
  uint64_t aligned_addr    = base_addr + to_line_boundary;
  uint32_t base_bank       = (uint32_t)((aligned_addr / FD_LINE_SIZE) % FD_NUM_BANKS);
  uint32_t bank_shift      = (FD_TARGET_BANK + FD_NUM_BANKS - base_bank) % FD_NUM_BANKS;
  kernel_arg.bank_align_pad = to_line_boundary + bank_shift * FD_LINE_SIZE;

  std::cout << "  buf_addr=0x" << std::hex << base_addr << std::dec
            << " -> base_bank=" << base_bank
            << ", bank_align_pad=" << kernel_arg.bank_align_pad << " bytes"
            << " (compensating so offset 0 lands on bank " << FD_TARGET_BANK << ")"
            << std::endl;

  std::vector<uint32_t> h_buf(buf_size / sizeof(uint32_t));
  for (size_t i = 0; i < h_buf.size(); ++i) {
    h_buf[i] = (uint32_t)(i + 1);
  }
  RT_CHECK(vx_copy_to_dev(buf_buffer, h_buf.data(), 0, buf_size));

  RT_CHECK(vx_upload_kernel_file(device, kernel_file, &krnl_buffer));

  kernel_arg.num_threads = num_threads;
  kernel_arg.delay_step  = delay_step;
  RT_CHECK(vx_upload_bytes(device, &kernel_arg, sizeof(kernel_arg_t), &args_buffer));

  RT_CHECK(vx_start(device, krnl_buffer, args_buffer));
  RT_CHECK(vx_ready_wait(device, VX_MAX_TIMEOUT));

  std::vector<uint32_t> h_dst(num_threads);
  RT_CHECK(vx_copy_from_dev(h_dst.data(), dst_buffer, 0, dst_size));

  // Functional sanity check only -- this test's real signal is the
  // DFV_COLLISION_NATURAL count in run.log, not this pass/fail.
  uint32_t bank_stride_words = FD_BANK_STRIDE / sizeof(uint32_t);
  uint32_t line_words        = FD_LINE_SIZE / sizeof(uint32_t);
  uint32_t pad_words         = kernel_arg.bank_align_pad / sizeof(uint32_t);
  int errors = 0;
  for (uint32_t tid = 0; tid < num_threads; ++tid) {
    uint32_t miss_off  = pad_words + (FD_TARGET_BANK * line_words) + (2 * tid)     * bank_stride_words;
    uint32_t probe_off = pad_words + (FD_TARGET_BANK * line_words) + (2 * tid + 1) * bank_stride_words;
    uint32_t expected = h_buf[miss_off] + h_buf[probe_off];
    if (h_dst[tid] != expected) {
      if (errors < 10) {
        printf("*** error: [%u] expected=%u, actual=%u\n", tid, expected, h_dst[tid]);
      }
      ++errors;
    }
  }

  cleanup();

  if (errors != 0) {
    std::cout << "FAILED! (" << errors << " functional errors)" << std::endl;
    return 1;
  }

  std::cout << "PASSED (functional check only)" << std::endl;
  std::cout << "Now check for a real hit: grep DFV_COLLISION_NATURAL <run.log>" << std::endl;
  return 0;
}
