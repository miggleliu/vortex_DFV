#include <vx_dfv_collision_report.h>
#include <vx_spawn.h>
#include <vx_print.h>
#include "common.h"

void kernel_body(kernel_arg_t* __UNIFORM__ arg) {
	uint32_t count    = arg->task_size;
	int32_t* src0_ptr = (int32_t*)arg->src0_addr;
	int32_t* src1_ptr = (int32_t*)arg->src1_addr;
	int32_t* dst_ptr  = (int32_t*)arg->dst_addr;

	uint32_t offset = blockIdx.x * count;
	for (uint32_t i = 0; i < count; ++i) {
		dst_ptr[offset+i] = src0_ptr[offset+i] + src1_ptr[offset+i];
	}

	vx_fence();
}

int main() {
    vx_dfv_collision_snapshot_t dfv_begin = {};
	kernel_arg_t* arg = (kernel_arg_t*)csr_read(VX_CSR_MSCRATCH);
    if (arg->enable_dfv_test) {
        csr_write(VX_CSR_DFV_CTRL, 1);
        csr_write(VX_CSR_DFV_RANDOM_SEED, 0xABCDEF00);
        csr_write(VX_CSR_DFV_SET_THRESHOLD, 240);
        csr_write(VX_CSR_DFV_RELEASE_THRESHOLD, 61440);  // release when lfsr2[15:0] >= 65504 (~0.04%)
        csr_write(VX_CSR_DFV_RELEASE_DELAY, 4096);
        csr_write(VX_CSR_DFV_RELEASE_FOREVER, 0);
        csr_write(VX_CSR_DFV_THROTTLE_THRESHOLD, 11985);
        csr_write(VX_CSR_DFV_BANK_THROTTLE_THRESHOLD, 6144);  // Type 3 per-bank threshold; needs calibration, see tests/analyze_bank_throttle.py
        csr_write(VX_CSR_DFV_FILL_BANK_MASK, 65535);
        csr_write(VX_CSR_DFV_DCACHE_CORE_REQ_STALL, 0);
        csr_write(VX_CSR_DFV_ICACHE_FILL_REQ_STALL, 0);
        csr_write(VX_CSR_DFV_DCACHE_FILL_REQ_STALL, 0);
        csr_write(VX_CSR_DFV_WRITEBACK_STALL, 0);
        csr_write(VX_CSR_DFV_DCACHE_FILL_RSP_STALL, 1);
        vx_printf("DFV_TEST_START\n");
        vx_dfv_collision_capture(&dfv_begin);
    }
	int __ret = vx_spawn_threads(1, &arg->num_tasks, nullptr, (vx_kernel_func_cb)kernel_body, arg);
	if (arg->enable_dfv_test) {
        vx_dfv_collision_snapshot_t dfv_end;
        vx_dfv_collision_capture(&dfv_end);
        csr_write(VX_CSR_DFV_CTRL, 0);
        // Functional status is separate; the runner rejects invalid CSR metrics.
        (void)vx_dfv_collision_report(&dfv_begin, &dfv_end);
    }
	return __ret;
}
