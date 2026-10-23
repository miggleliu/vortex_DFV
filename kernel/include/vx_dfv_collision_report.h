// SPDX-License-Identifier: Apache-2.0
// GPU software reporting of the platform-independent COL2 CSR snapshots.
#ifndef VX_DFV_COLLISION_REPORT_H
#define VX_DFV_COLLISION_REPORT_H

#include <vx_dfv_collision.h>
#include <vx_intrinsics.h>
#include <vx_print.h>

#ifdef __cplusplus
extern "C" {
#endif

// Call from main's single active thread after local spawned warps have joined.
// Capture END before disabling DFV, then print both snapshots AFTER that capture.
// These are local-core sampling windows of a shared socket monitor; readings
// from cores sharing that monitor must not be summed as independent counters.
static inline int vx_dfv_collision_report(
    const vx_dfv_collision_snapshot_t* begin,
    const vx_dfv_collision_snapshot_t* end) {
    const unsigned core = (unsigned)vx_core_id();
    const unsigned cores = (unsigned)vx_num_cores();
    vx_printf("DFV_CSR_SNAPSHOT v=1 core=%u cores=%u cache=0 bank=0 phase=BEGIN "
              "info=0x%08x flags=%u natural=%u dfv=%u cycle_lo=%u cycle_hi=%u\n",
              core, cores, (unsigned)begin->info, (unsigned)begin->status,
              (unsigned)begin->natural_count, (unsigned)begin->dfv_count,
              (unsigned)begin->cycle_lo, (unsigned)begin->cycle_hi);
    vx_printf("DFV_CSR_SNAPSHOT v=1 core=%u cores=%u cache=0 bank=0 phase=END "
              "info=0x%08x flags=%u natural=%u dfv=%u cycle_lo=%u cycle_hi=%u\n",
              core, cores, (unsigned)end->info, (unsigned)end->status,
              (unsigned)end->natural_count, (unsigned)end->dfv_count,
              (unsigned)end->cycle_lo, (unsigned)end->cycle_hi);

    if (!vx_dfv_collision_valid(begin) || !vx_dfv_collision_valid(end)
        || begin->cycle_hi >= (1u << 12) || end->cycle_hi >= (1u << 12)
        || end->natural_count < begin->natural_count
        || end->dfv_count < begin->dfv_count
        || vx_dfv_collision_cycles(end) <= vx_dfv_collision_cycles(begin)) {
        vx_printf("DFV_CSR_ERROR core=%u: invalid, unavailable, at-limit, or reset snapshot\n", core);
        return -1;
    }
    return 0;
}

#ifdef __cplusplus
}
#endif
#endif
