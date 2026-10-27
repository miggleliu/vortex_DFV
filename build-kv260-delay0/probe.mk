include config.mk
probe:
	@echo $(XLEN)
	@echo $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-gcc
	@echo $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-objdump
	@echo $(RISCV_TOOLCHAIN_PATH)/bin/$(RISCV_PREFIX)-nm
