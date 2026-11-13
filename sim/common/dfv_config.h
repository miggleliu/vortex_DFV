// SPDX-License-Identifier: Apache-2.0
#ifndef VX_DFV_CONFIG_H
#define VX_DFV_CONFIG_H
#include <stdint.h>
#include <VX_config.h>

// Reserved by the common runtime before any application allocations. Transport
// uses ordinary device memory, so it works with every Vortex memory backend.
#define VX_DFV_CONFIG_ADDR USER_BASE_ADDR
#define VX_DFV_CONFIG_MAGIC 0x44465631u
#define VX_DFV_CONFIG_VERSION 1u
#define VX_DFV_CSR_FIRST 0x7c0u
#define VX_DFV_CSR_COUNT 15u
typedef struct vx_dfv_config {
    uint32_t magic, version, sequence, applied;
    uint32_t values[VX_DFV_CSR_COUNT];
    uint32_t readback[VX_DFV_CSR_COUNT]; // actual GPU CSR readback, reader core 0
    uint32_t reserved[14];
} vx_dfv_config_t;

static inline int vx_dfv_value_valid(unsigned index, uint32_t value) {
    switch (index) {
    case 0: case 1: case 4: case 5: case 6: case 10: case 13:
        return value <= 1;
    case 2: case 8: return 1; // 32-bit LFSR seeds
    case 9:
        return value <= 0xffffu && (value & 15u) <= 8u
            && ((value >> 4) & 15u) <= 8u && ((value >> 8) & 15u) <= 8u
            && ((value >> 12) & 15u) <= 8u;
    default: return index < VX_DFV_CSR_COUNT && value <= 0xffffu;
    }
}
#endif
