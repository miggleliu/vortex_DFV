// SPDX-License-Identifier: Apache-2.0
#ifndef VX_DFV_HOST_H
#define VX_DFV_HOST_H
#include <dfv_config.h>
#include <vx_intrinsics.h>
#include <vx_print.h>

// Called by one active lane on each core before the BEGIN capture. Architectural
// CSR instructions need immediate addresses; the host values are runtime data.
static inline int vx_dfv_apply_host_config(void) {
    volatile vx_dfv_config_t* mailbox =
        (volatile vx_dfv_config_t*)(uintptr_t)VX_DFV_CONFIG_ADDR;
    if (mailbox->magic != VX_DFV_CONFIG_MAGIC
        || mailbox->version != VX_DFV_CONFIG_VERSION || !mailbox->sequence) {
        vx_printf("DFV_CSR_ERROR missing host configuration; rebuild the runtime\n");
        return -1;
    }
    uint32_t values[VX_DFV_CSR_COUNT];
    for (unsigned i = 0; i < VX_DFV_CSR_COUNT; ++i) {
        values[i] = mailbox->values[i];
        if (!vx_dfv_value_valid(i, values[i])) {
            vx_printf("DFV_CSR_ERROR invalid host configuration\n");
            return -1;
        }
    }
    csr_write(0x7c0, 0); // Configure gates and seeds before enabling injection.
#define VX_DFV_WRITE(n) csr_write(0x7c0 + n, values[n])
    VX_DFV_WRITE(1); VX_DFV_WRITE(2); VX_DFV_WRITE(3); VX_DFV_WRITE(4);
    VX_DFV_WRITE(5); VX_DFV_WRITE(6); VX_DFV_WRITE(7); VX_DFV_WRITE(8);
    VX_DFV_WRITE(9); VX_DFV_WRITE(10); VX_DFV_WRITE(11); VX_DFV_WRITE(12);
    VX_DFV_WRITE(13); VX_DFV_WRITE(14);
    VX_DFV_WRITE(0);
#undef VX_DFV_WRITE
    if (vx_core_id() == 0) {
#define VX_DFV_READ(n) mailbox->readback[n] = (uint32_t)csr_read(0x7c0 + n)
        VX_DFV_READ(0); VX_DFV_READ(1); VX_DFV_READ(2); VX_DFV_READ(3);
        VX_DFV_READ(4); VX_DFV_READ(5); VX_DFV_READ(6); VX_DFV_READ(7);
        VX_DFV_READ(8); VX_DFV_READ(9); VX_DFV_READ(10); VX_DFV_READ(11);
        VX_DFV_READ(12); VX_DFV_READ(13); VX_DFV_READ(14);
#undef VX_DFV_READ
        __asm__ volatile ("" ::: "memory");
        vx_fence();
        __asm__ volatile ("" ::: "memory");
        mailbox->applied = mailbox->sequence;
        vx_fence();
        __asm__ volatile ("" ::: "memory");
    }
    return 0;
}
#endif
