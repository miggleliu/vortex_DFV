#include <vx_dfv_host.h>
#include <vx_dfv_collision_report.h>
#include "common.h"
#include <vx_spawn.h>
#include <vx_print.h>

void kernel_body(kernel_arg_t *__UNIFORM__ arg) {
  auto dst = reinterpret_cast<float *>(arg->dst_addr);
  int size = arg->size;
  int col = blockIdx.x;
  int row = blockIdx.y;
  dst[row * size + col] = madmax_compute(row, col, size);
}

int main() {
    vx_dfv_snapshot_t dfv_begin = {}, dfv_end = {};
  kernel_arg_t *arg = (kernel_arg_t *)csr_read(VX_CSR_MSCRATCH);
    if (arg->enable_dfv_test) {
        if (vx_dfv_apply_host_config()) return -1;
        VX_DFV_ALLOC(&dfv_begin);
        VX_DFV_ALLOC(&dfv_end);
        vx_printf("DFV_TEST_START\n");
        vx_dfv_capture(&dfv_begin);
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