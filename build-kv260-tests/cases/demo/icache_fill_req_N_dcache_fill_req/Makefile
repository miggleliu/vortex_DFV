include /home/miggleliu/Documents/vortex_DFV/vortex_DFV/build-kv260-tests/config.mk
PROJECT := demo
SRC_DIR := /home/miggleliu/Documents/vortex_DFV/vortex_DFV/build-kv260-tests/cases/demo/icache_fill_req_N_dcache_fill_req/source
SRCS := $(SRC_DIR)/main.cpp
VX_SRCS := $(SRC_DIR)/kernel.cpp
CONFIGS := -DXLEN_32 -DNUM_CLUSTERS=1 -DNUM_CORES=1 -DNUM_WARPS=4 -DNUM_THREADS=4 -DEXT_F_DISABLE -DICACHE_SIZE=4096 -DDCACHE_SIZE=4096 -DDCACHE_NUM_BANKS=4 -DPLATFORM_MEMORY_NUM_BANKS=1
include /home/miggleliu/Documents/vortex_DFV/vortex_DFV/tests/regression/common.mk
