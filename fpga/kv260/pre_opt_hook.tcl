# Apply the fork's memory repair immediately before implementation optimization.
set hook_root [file dirname [file normalize [info script]]]
source [file join $hook_root xilinx_async_bram_patch_fast.tcl]
set unresolved [get_cells -quiet -hierarchical -filter {IS_BLACKBOX == 1}]
if {[llength $unresolved]} {
    error "Unresolved black boxes after RAM repair: $unresolved"
}
