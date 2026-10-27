# Vivado 2024.1: source C:/Xilinx/Vivado/vortex-board/create_project.tcl
# Creates and validates the complete PS + AXI-Lite + Vortex/BRAM block design.
proc kv260_board_ip {pattern name} {
    set definitions [lsort -dictionary [get_ipdefs -quiet $pattern]]
    if {![llength $definitions]} {error "Required Vivado IP unavailable: $pattern"}
    create_bd_cell -type ip -vlnv [lindex $definitions end] $name
}
proc kv260_create_board_project {root out} {
    if {[llength [get_projects -quiet]]} {error "Save and close the current project first."}
    if {[file exists $out]} {error "Output already exists: $out. Set kv260_board_out to a new directory."}
    set part xck26-sfvc784-2LV-c
    if {[llength [get_parts -quiet $part]] != 1} {error "Install production Zynq UltraScale+ MPSoC device support."}
    set boards [lsort -dictionary [get_board_parts -quiet xilinx.com:kv260_som:part0:*]]
    if {![llength $boards]} {error "KV260 board preset unavailable. Install the KV260 SOM board files in Vivado's board manager."}
    create_project kv260_vortex $out -part $part
    set_property board_part [lindex $boards end] [current_project]
    set_property target_language Verilog [current_project]
    set_param general.maxThreads 4
    set h [open [file join $root sources.f] r]
    set source_list [split [read $h] "\n"]
    close $h
    set hdl_files {}
    foreach rel $source_list {
        set rel [string trim $rel]
        if {$rel ne ""} {lappend hdl_files [file join $root $rel]}
    }
    foreach name {kv260_native_bram.sv kv260_vortex_bram.sv kv260_vortex_bd.v} {
        lappend hdl_files [file join $root $name]
    }
    add_files -norecurse $hdl_files
    # A SystemVerilog module may be a child, but not the module-reference top.
    set_property file_type Verilog [get_files [file join $root kv260_vortex_bd.v]]
    set_property top kv260_vortex_bd [current_fileset]
    update_compile_order -fileset sources_1
    create_bd_design vortex_bd
    kv260_board_ip xilinx.com:ip:zynq_ultra_ps_e:* ps
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} [get_bd_cells ps]
    set_property -dict [list \
        CONFIG.PSU__USE__M_AXI_GP0 {1} \
        CONFIG.PSU__USE__M_AXI_GP1 {0} \
        CONFIG.PSU__USE__M_AXI_GP2 {0} \
        CONFIG.PSU__MAXIGP0__DATA_WIDTH {128} \
        CONFIG.PSU__FPGA_PL0_ENABLE {1} \
        CONFIG.PSU__OVERRIDE__BASIC_CLOCK {0} \
        CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100}] [get_bd_cells ps]
    create_bd_cell -type module -reference kv260_vortex_bd gpu
    kv260_board_ip xilinx.com:ip:smartconnect:* axi_sc
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells axi_sc]
    kv260_board_ip xilinx.com:ip:proc_sys_reset:* rst
    # Reset polarity is derived from ps/pl_resetn0 during BD propagation.
    # C_EXT_RESET_HIGH is read-only in Vivado 2024.1; verify it after validation.
    kv260_board_ip xilinx.com:ip:xlconstant:* const_one
    set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] [get_bd_cells const_one]
    kv260_board_ip xilinx.com:ip:xlconstant:* const_zero
    set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] [get_bd_cells const_zero]
    connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins axi_sc/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins gpu/S_AXI]
    connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ps/maxihpm0_fpd_aclk] \
        [get_bd_pins axi_sc/aclk] [get_bd_pins rst/slowest_sync_clk] [get_bd_pins gpu/aclk]
    connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst/ext_reset_in]
    connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins rst/dcm_locked]
    set_property CONFIG.C_AUX_RESET_HIGH {0} [get_bd_cells rst]
    connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins rst/aux_reset_in]
    connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins rst/mb_debug_sys_rst]
    connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins gpu/aresetn] [get_bd_pins axi_sc/aresetn]
    set slave_segs [get_bd_addr_segs -of_objects [get_bd_intf_pins gpu/S_AXI]]
    if {[llength $slave_segs] != 1} {error "Expected one inferred AXI-Lite register/memory segment, got: $slave_segs"}
    assign_bd_address -offset 0xA0000000 -range 0x00080000 \
        -target_address_space [get_bd_addr_spaces ps/Data] $slave_segs -force
    validate_bd_design
    # The PS can report 99,999,001 Hz for a nominal 100 MHz request. Input-clock
    # metadata must inherit that achieved rate rather than force 100,000,000 Hz.
    set ps_hz [get_property CONFIG.FREQ_HZ [get_bd_pins ps/pl_clk0]]
    set gpu_hz [get_property CONFIG.FREQ_HZ [get_bd_pins gpu/aclk]]
    set axi_hz [get_property CONFIG.FREQ_HZ [get_bd_intf_pins gpu/S_AXI]]
    if {![string is integer -strict $ps_hz] || $ps_hz <= 0 || $gpu_hz ne $ps_hz || $axi_hz ne $ps_hz} {
        error "Clock propagation mismatch: PS=$ps_hz Hz, GPU=$gpu_hz Hz, GPU AXI=$axi_hz Hz."
    }
    set reset_high [get_property CONFIG.C_EXT_RESET_HIGH [get_bd_cells rst]]
    if {$reset_high ne "0"} {
        error "Expected active-low rst/ext_reset_in from ps/pl_resetn0; C_EXT_RESET_HIGH=$reset_high."
    }
    puts "INFO: Shared clock is $ps_hz Hz; external reset is active-low."
    save_bd_design
    set bd_file [get_files */vortex_bd.bd]
    generate_target all $bd_file
    set wrapper [make_wrapper -files $bd_file -top]
    add_files -norecurse $wrapper
    set_property top vortex_bd_wrapper [current_fileset]
    update_compile_order -fileset sources_1
    set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root pre_opt_hook.tcl] [get_runs impl_1]
    puts "INFO: KV260 Vortex BRAM project created and validated: $out"
    puts "INFO: Review the block design, then source [file join $root build_bitstream.tcl]"
}
set kv260_board_root [file dirname [file normalize [info script]]]
if {![info exists kv260_board_out]} {set kv260_board_out [file join $kv260_board_root vivado_project]}
kv260_create_board_project $kv260_board_root [file normalize $kv260_board_out]
