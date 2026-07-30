ROOT_DIR := $(realpath ../../..)
include $(ROOT_DIR)/config.mk

PROJECT := frontdoor_race

SRC_DIR := $(VORTEX_HOME)/tests/regression/$(PROJECT)

SRCS := $(SRC_DIR)/main.cpp

VX_SRCS := $(SRC_DIR)/kernel.cpp

# -n = number of probe threads (sweep points), -t = delay_step (cycles/point)
# Default sweep covers 0..5040cc, matching the measured ~1000-5025cc DRAM
# latency range documented in project memory. Widen/narrow with -n/-t without
# rebuilding: e.g. `make OPTS="-n128 -t40" run-rtlsim` for a finer sweep.
OPTS ?= -n64 -t80

include ../common.mk
