// SPDX-License-Identifier: Apache-2.0
#ifndef VX_DFV_COLLISION_REPORT_H
#define VX_DFV_COLLISION_REPORT_H
#include <vx_dfv_collision.h>
#include <vx_intrinsics.h>
#include <vx_print.h>
#ifdef __cplusplus
extern "C" {
#endif

static inline void vx_dfv_print_frame(const vx_dfv_frame_t* f,
    const char* family, const char* phase, unsigned core, unsigned cores) {
    vx_printf("DFV_CSR_FRAME v=2 core=%u cores=%u family=%s phase=%s info=0x%08x "
              "count=%u valid=%u cycle_lo=%u cycle_hi=%u\n", core, cores, family,
              phase, (unsigned)f->info, (unsigned)f->count, (unsigned)f->valid,
              (unsigned)f->cycle_lo, (unsigned)f->cycle_hi);
    for (uint32_t i = 0; i < f->count; ++i) {
        const vx_dfv_sample_t* row = &f->samples[i];
        vx_printf("DFV_CSR_SAMPLE v=2 core=%u family=%s phase=%s index=%u "
                  "desc=0x%08x owner=%u flags=%u count0=%u count1=%u threshold=%u\n",
                  core, family, phase, (unsigned)i, (unsigned)row->descriptor,
                  (unsigned)row->owner, (unsigned)row->status, (unsigned)row->count0,
                  (unsigned)row->count1, (unsigned)row->threshold);
    }
}
// Print only after the END capture. Core IDs identify readers, not independent
// ownership of shared socket/cluster/chip monitors. Never sum across readers.
static inline int vx_dfv_report(const vx_dfv_snapshot_t* begin,
                                const vx_dfv_snapshot_t* end) {
    const unsigned core = (unsigned)vx_core_id(), cores = (unsigned)vx_num_cores();
    vx_dfv_print_frame(&begin->collision, "collision", "BEGIN", core, cores);
    vx_dfv_print_frame(&begin->throttle, "throttle", "BEGIN", core, cores);
    vx_dfv_print_frame(&end->collision, "collision", "END", core, cores);
    vx_dfv_print_frame(&end->throttle, "throttle", "END", core, cores);
    if (!begin->collision.count
        || !vx_dfv_frame_valid(&begin->collision, &end->collision, VX_DFV_COLLISION_ID)
        || !vx_dfv_frame_valid(&begin->throttle, &end->throttle, VX_DFV_THROTTLE_ID)) {
        vx_printf("DFV_CSR_ERROR core=%u: invalid DFV snapshot pair\n", core);
        return -1;
    }
    return 0;
}
#ifdef __cplusplus
}
#endif
#endif
