// SPDX-License-Identifier: Apache-2.0
// GPU DFV observation API. Collision and throttle have independent CSR families.
#ifndef VX_DFV_COLLISION_H
#define VX_DFV_COLLISION_H
#include <stdint.h>
#include <VX_types.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t descriptor, owner, status, count0, count1, threshold;
} vx_dfv_sample_t;
typedef struct {
    uint32_t info, count, valid, cycle_lo, cycle_hi;
    vx_dfv_sample_t* samples;
} vx_dfv_frame_t;
typedef struct {
    vx_dfv_frame_t collision, throttle;
} vx_dfv_snapshot_t;

#define VX_DFV_READ_FN(name, address) \
static inline uint32_t name(void) { \
    uintptr_t value; \
    __asm__ __volatile__("csrr %0, %1" : "=r"(value) : "i"(address) : "memory"); \
    return (uint32_t)value; \
}
VX_DFV_READ_FN(vx_dfv_coll_info, VX_CSR_DFV_COLL_INFO)
VX_DFV_READ_FN(vx_dfv_coll_count, VX_CSR_DFV_COLL_COUNT)
VX_DFV_READ_FN(vx_dfv_coll_lo, VX_CSR_DFV_COLL_CYCLE_LO)
VX_DFV_READ_FN(vx_dfv_coll_hi, VX_CSR_DFV_COLL_CYCLE_HI)
VX_DFV_READ_FN(vx_dfv_coll_status, VX_CSR_DFV_COLL_STATUS)
VX_DFV_READ_FN(vx_dfv_coll_desc, VX_CSR_DFV_COLL_DESC)
VX_DFV_READ_FN(vx_dfv_coll_owner, VX_CSR_DFV_COLL_OWNER)
VX_DFV_READ_FN(vx_dfv_coll_natural, VX_CSR_DFV_COLL_NATURAL)
VX_DFV_READ_FN(vx_dfv_coll_dfv, VX_CSR_DFV_COLL_DFV)
VX_DFV_READ_FN(vx_dfv_thr_info, VX_CSR_DFV_THR_INFO)
VX_DFV_READ_FN(vx_dfv_thr_count, VX_CSR_DFV_THR_COUNT)
VX_DFV_READ_FN(vx_dfv_thr_lo, VX_CSR_DFV_THR_CYCLE_LO)
VX_DFV_READ_FN(vx_dfv_thr_hi, VX_CSR_DFV_THR_CYCLE_HI)
VX_DFV_READ_FN(vx_dfv_thr_status, VX_CSR_DFV_THR_STATUS)
VX_DFV_READ_FN(vx_dfv_thr_desc, VX_CSR_DFV_THR_DESC)
VX_DFV_READ_FN(vx_dfv_thr_owner, VX_CSR_DFV_THR_OWNER)
VX_DFV_READ_FN(vx_dfv_thr_events, VX_CSR_DFV_THR_EVENTS)
VX_DFV_READ_FN(vx_dfv_thr_threshold, VX_CSR_DFV_THR_THRESHOLD)
#undef VX_DFV_READ_FN

static inline int vx_dfv_discover(vx_dfv_snapshot_t* s) {
    s->collision.info = vx_dfv_coll_info();
    s->collision.count = s->throttle.count = 0;
    s->collision.valid = s->throttle.valid = 0;
    s->collision.cycle_lo = s->collision.cycle_hi = 0;
    s->throttle.cycle_lo = s->throttle.cycle_hi = s->throttle.info = 0;
    if (s->collision.info != VX_DFV_COLLISION_ID)
        return -1; // Do not access the new addresses on a COL2 image.
    s->throttle.info = vx_dfv_thr_info();
    if (s->throttle.info != VX_DFV_THROTTLE_ID)
        return -1;
    const uint32_t collisions = vx_dfv_coll_count(), throttles = vx_dfv_thr_count();
    // Bound allocations on corrupt/incompatible images, never silently truncate.
    if (!collisions || collisions > 4096 || throttles > 4096)
        return -1;
    s->collision.count = collisions;
    s->throttle.count = throttles;
    return 0;
}

// Allocate at the caller's function scope so buffers survive through END.
// Storage is proportional to discovered hardware, not a fixed all-bank maximum.
// Each BEGIN/END pair uses 48 * (collision_count + throttle_count) bytes plus
// frame headers; choose a sufficient GPU stack size for large configurations.
#define VX_DFV_ALLOC(s) do { \
    (void)vx_dfv_discover(s); \
    (s)->collision.samples = (vx_dfv_sample_t*)__builtin_alloca( \
        sizeof(vx_dfv_sample_t) * (s)->collision.count); \
    (s)->throttle.samples = (vx_dfv_sample_t*)__builtin_alloca( \
        sizeof(vx_dfv_sample_t) * (s)->throttle.count); \
} while (0)

// One owner thread per core; join local workers before END. A fence is not a
// cross-core barrier. Both family commands precede any readout/stores.
static inline int vx_dfv_capture(vx_dfv_snapshot_t* s) {
    if (s->collision.info != VX_DFV_COLLISION_ID || !s->collision.count
        || s->throttle.info != VX_DFV_THROTTLE_ID)
        return -1;
    __asm__ __volatile__("fence rw,rw\n\tcsrwi %0, 1\n\tcsrwi %1, 1"
        :: "i"(VX_CSR_DFV_COLL_CTRL), "i"(VX_CSR_DFV_THR_CTRL) : "memory");
    s->collision.cycle_lo = vx_dfv_coll_lo();
    s->collision.cycle_hi = vx_dfv_coll_hi();
    s->collision.valid = vx_dfv_coll_status() & 1;
    s->throttle.cycle_lo = vx_dfv_thr_lo();
    s->throttle.cycle_hi = vx_dfv_thr_hi();
    s->throttle.valid = vx_dfv_thr_status() & 1;
    for (uint32_t i = 0; i < s->collision.count; ++i) {
        __asm__ __volatile__("csrw %0, %1" :: "i"(VX_CSR_DFV_COLL_SELECT), "r"((uintptr_t)i) : "memory");
        vx_dfv_sample_t* row = &s->collision.samples[i];
        row->descriptor = vx_dfv_coll_desc(); row->owner = vx_dfv_coll_owner();
        row->status = vx_dfv_coll_status(); row->count0 = vx_dfv_coll_natural();
        row->count1 = vx_dfv_coll_dfv(); row->threshold = 0;
    }
    for (uint32_t i = 0; i < s->throttle.count; ++i) {
        __asm__ __volatile__("csrw %0, %1" :: "i"(VX_CSR_DFV_THR_SELECT), "r"((uintptr_t)i) : "memory");
        vx_dfv_sample_t* row = &s->throttle.samples[i];
        row->descriptor = vx_dfv_thr_desc(); row->owner = vx_dfv_thr_owner();
        row->status = vx_dfv_thr_status(); row->count0 = vx_dfv_thr_events();
        row->count1 = 0; row->threshold = vx_dfv_thr_threshold();
    }
    return 0;
}

static inline uint64_t vx_dfv_cycles(const vx_dfv_frame_t* f) {
    return ((uint64_t)f->cycle_hi << 32) | f->cycle_lo;
}
static inline int vx_dfv_frame_valid(const vx_dfv_frame_t* a,
                                      const vx_dfv_frame_t* b, uint32_t info) {
    if (a->info != info || b->info != info || !a->valid || !b->valid
        || a->count != b->count || a->cycle_hi >= 4096 || b->cycle_hi >= 4096
        || vx_dfv_cycles(b) <= vx_dfv_cycles(a)) return 0;
    for (uint32_t i = 0; i < a->count; ++i) {
        const vx_dfv_sample_t *x=&a->samples[i], *y=&b->samples[i];
        if (x->status != 9 || y->status != 9 || x->descriptor != y->descriptor
            || x->owner != y->owner || x->threshold != y->threshold
            || x->count0 == UINT32_MAX || y->count0 == UINT32_MAX
            || x->count1 == UINT32_MAX || y->count1 == UINT32_MAX
            || y->count0 < x->count0 || y->count1 < x->count1) return 0;
    }
    return 1;
}
#ifdef __cplusplus
}
#endif
#endif
