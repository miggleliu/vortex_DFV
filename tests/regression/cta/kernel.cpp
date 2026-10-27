#include <vx_dfv_collision_report.h>
#include <vx_spawn.h>
#include <vx_print.h>
#include "common.h"

void kernel_body(kernel_arg_t* __UNIFORM__ arg) {
	auto src_ptr  = reinterpret_cast<int*>(arg->src_addr);
	auto dst_ptr  = reinterpret_cast<int*>(arg->dst_addr);
	uint32_t cta_size = blockDim.x * blockDim.y * blockDim.z;
 	uint32_t blockId  = blockIdx.x + blockIdx.y * gridDim.x + blockIdx.z * gridDim.x * gridDim.y;
 	uint32_t localId  = threadIdx.x + threadIdx.y * blockDim.x + threadIdx.z * blockDim.x * blockDim.y;
  uint32_t globalId = localId + blockId * cta_size;
  dst_ptr[globalId] = globalId + src_ptr[localId];
}

int main() {
    vx_dfv_snapshot_t dfv_begin = {}, dfv_end = {};
	kernel_arg_t* arg = (kernel_arg_t*)csr_read(VX_CSR_MSCRATCH);
    if (arg->enable_dfv_test) {
        csr_write(VX_CSR_DFV_CTRL, 1);
        csr_write(VX_CSR_DFV_RANDOM_SEED, 0xABCDEF00);
        csr_write(VX_CSR_DFV_SET_THRESHOLD, 57344);
        csr_write(VX_CSR_DFV_RELEASE_THRESHOLD, 61440);  // release when lfsr2[15:0] >= 65504 (~0.04%)
        csr_write(VX_CSR_DFV_RELEASE_DELAY, 4096);
        csr_write(VX_CSR_DFV_RELEASE_FOREVER, 0);
        csr_write(VX_CSR_DFV_THROTTLE_THRESHOLD, 11985);
        csr_write(VX_CSR_DFV_BANK_THROTTLE_THRESHOLD, 6144);  // Type 3 per-bank threshold; needs calibration, see tests/analyze_bank_throttle.py
        csr_write(VX_CSR_DFV_FILL_BANK_MASK, 65535);
        csr_write(VX_CSR_DFV_DCACHE_CORE_REQ_STALL, 0);
        csr_write(VX_CSR_DFV_ICACHE_FILL_REQ_STALL, 1);
        csr_write(VX_CSR_DFV_DCACHE_FILL_REQ_STALL, 1);
        csr_write(VX_CSR_DFV_WRITEBACK_STALL, 0);
        csr_write(VX_CSR_DFV_DCACHE_FILL_RSP_STALL, 0);
        VX_DFV_ALLOC(&dfv_begin);
        VX_DFV_ALLOC(&dfv_end);
        vx_printf("DFV_TEST_START\n");
        vx_dfv_capture(&dfv_begin);
    }
	int __ret = vx_spawn_threads(3, arg->grid_dim, arg->block_dim, (vx_kernel_func_cb)kernel_body, arg);
	if (arg->enable_dfv_test) {
        vx_dfv_capture(&dfv_end);
        csr_write(VX_CSR_DFV_CTRL, 0);
        // Functional status is separate; the runner rejects invalid CSR metrics.
        (void)vx_dfv_report(&dfv_begin, &dfv_end);
    }
	return __ret;
}