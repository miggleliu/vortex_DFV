// SPDX-License-Identifier: Apache-2.0
// Portable GPU-side collision observation. No host/ARM MMIO addresses.
#ifndef VX_DFV_COLLISION_H
#define VX_DFV_COLLISION_H

#include <stdint.h>
#include <VX_types.h> // Generated from hw/rtl/VX_types.vh by the normal build.

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t info;
    uint32_t status;
    uint32_t natural_count;
    uint32_t dfv_count;
    uint32_t cycle_lo;
    uint32_t cycle_hi;
} vx_dfv_collision_snapshot_t;

enum {
    VX_DFV_COLL_VALID = 1,
    VX_DFV_COLL_NATURAL_AT_LIMIT = 2,
    VX_DFV_COLL_DFV_AT_LIMIT = 4,
    VX_DFV_COLL_AVAILABLE = 8
};

// One designated thread per core owns its snapshot. Cores in the same socket
// observe the same shared unit-0/bank-0 counter; do not sum their readings.
// Callers must synchronize other workers before the END capture. A fence is
// local memory ordering, not a cross-warp/core barrier or a universal cache flush.
static inline void vx_dfv_collision_read(vx_dfv_collision_snapshot_t* out) {
    uintptr_t info, status, natural, dfv, lo, hi;
    __asm__ __volatile__("csrr %0, %6\n\tcsrr %1, %7\n\tcsrr %2, %8\n\t"
                         "csrr %3, %9\n\tcsrr %4, %10\n\tcsrr %5, %11"
        : "=r"(info), "=r"(status), "=r"(natural), "=r"(dfv), "=r"(lo), "=r"(hi)
        : "i"(VX_CSR_DFV_COLL_INFO), "i"(VX_CSR_DFV_COLL_STATUS),
          "i"(VX_CSR_DFV_COLL_NATURAL), "i"(VX_CSR_DFV_COLL_DFV),
          "i"(VX_CSR_DFV_COLL_CYCLE_LO), "i"(VX_CSR_DFV_COLL_CYCLE_HI)
        : "memory");
    out->info = (uint32_t)info;
    out->status = (uint32_t)status;
    out->natural_count = (uint32_t)natural;
    out->dfv_count = (uint32_t)dfv;
    out->cycle_lo = (uint32_t)lo;
    out->cycle_hi = (uint32_t)hi;
}

static inline void vx_dfv_collision_capture(vx_dfv_collision_snapshot_t* out) {
    __asm__ __volatile__("fence rw,rw\n\tcsrwi %0, 1"
                         :: "i"(VX_CSR_DFV_COLL_CTRL) : "memory");
    vx_dfv_collision_read(out);
}

// Invalidation clears only this core's held snapshot, not the live counters.
static inline void vx_dfv_collision_invalidate(void) {
    __asm__ __volatile__("csrwi %0, 0" :: "i"(VX_CSR_DFV_COLL_CTRL) : "memory");
}

static inline uint64_t vx_dfv_collision_cycles(const vx_dfv_collision_snapshot_t* s) {
    return ((uint64_t)s->cycle_hi << 32) | s->cycle_lo;
}

static inline int vx_dfv_collision_valid(const vx_dfv_collision_snapshot_t* s) {
    return s->info == VX_DFV_COLLISION_ID
        && s->status == (VX_DFV_COLL_VALID | VX_DFV_COLL_AVAILABLE)
        && s->natural_count < ((1u << VX_DFV_COLLISION_WIDTH) - 1u)
        && s->dfv_count < ((1u << VX_DFV_COLLISION_WIDTH) - 1u);
}

#ifdef __cplusplus
}
#endif
#endif
