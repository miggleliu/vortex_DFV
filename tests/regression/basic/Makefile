ROOT_DIR := $(realpath ../../..)
include $(ROOT_DIR)/config.mk

PROJECT := basic

SRC_DIR := $(VORTEX_HOME)/tests/regression/$(PROJECT)

SRCS := $(SRC_DIR)/main.cpp

# CSR snapshots/printing require the normal runtime's stack, GP and TLS setup.
# common.mk links libvortex.a, which supplies vx_start.S and vx_perf_dump.
VX_SRCS := $(SRC_DIR)/kernel.cpp

OPTS ?= -n256

include ../common.mk

VX_CC  = $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-gcc
VX_CXX = $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-g++
VX_DP  = $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-objdump
VX_CP  = $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-objcopy
