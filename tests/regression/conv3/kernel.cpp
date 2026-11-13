#include <vx_dfv_host.h>
#include <vx_dfv_collision_report.h>
#include <vx_spawn.h>
#include <vx_print.h>
#include <vx_intrinsics.h>
#include "common.h"

void kernel_body(kernel_arg_t* __UNIFORM__ arg) {
    auto I = reinterpret_cast<TYPE*>(arg->I_addr);
    auto W = reinterpret_cast<TYPE*>(arg->use_lmem ? __local_mem(0) : (void*)arg->W_addr);
	auto O = reinterpret_cast<TYPE*>(arg->O_addr);
    auto width = arg->width;

    int col = blockIdx.x;
    int row = blockIdx.y;

    // Adjust for padded borders
    int paddedWidth = width + 2;
    int paddedX = col + 1;
    int paddedY = row + 1;

    // Compute 3x3 convolution sum
    float sum = 0.0f;

    sum += I[(paddedY - 1) * paddedWidth + (paddedX - 1)] * W[0]; // Top-left
    sum += I[(paddedY - 1) * paddedWidth + paddedX] * W[1];       // Top-center
    sum += I[(paddedY - 1) * paddedWidth + (paddedX + 1)] * W[2]; // Top-right

    sum += I[paddedY * paddedWidth + (paddedX - 1)] * W[3];       // Middle-left
    sum += I[paddedY * paddedWidth + paddedX] * W[4];             // Center
    sum += I[paddedY * paddedWidth + (paddedX + 1)] * W[5];       // Middle-right

    sum += I[(paddedY + 1) * paddedWidth + (paddedX - 1)] * W[6]; // Bottom-left
    sum += I[(paddedY + 1) * paddedWidth + paddedX] * W[7];       // Bottom-center
    sum += I[(paddedY + 1) * paddedWidth + (paddedX + 1)] * W[8]; // Bottom-right

    O[row * width + col] = sum;
}

int main() {
    vx_dfv_snapshot_t dfv_begin = {}, dfv_end = {};
    kernel_arg_t* arg = (kernel_arg_t*)csr_read(VX_CSR_MSCRATCH);
    if (arg->enable_dfv_test) {
        if (vx_dfv_apply_host_config()) return -1;
        VX_DFV_ALLOC(&dfv_begin);
        VX_DFV_ALLOC(&dfv_end);
        vx_printf("DFV_TEST_START\n");
        vx_dfv_capture(&dfv_begin);
    }
    if (arg->use_lmem) {
        // populate local memory
        auto W = reinterpret_cast<TYPE*>(arg->W_addr);
        auto L = reinterpret_cast<TYPE*>(__local_mem(0));
        for (int i = 0; i < (3*3); ++i) {
            L[i] = W[i];
        }
    }
    int __ret = vx_spawn_threads(2, arg->grid_dim, nullptr, (vx_kernel_func_cb)kernel_body, arg);
    if (arg->enable_dfv_test) {
        vx_dfv_capture(&dfv_end);
        csr_write(VX_CSR_DFV_CTRL, 0);
        // Functional status is separate; the runner rejects invalid CSR metrics.
        (void)vx_dfv_report(&dfv_begin, &dfv_end);
    }
    return __ret;
}
