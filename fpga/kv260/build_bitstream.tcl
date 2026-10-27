# Run after create_project.tcl has successfully validated the block design.
set board_root [file dirname [file normalize [info script]]]
if {[llength [get_projects -quiet]] != 1} {error "Open the generated kv260_vortex project first."}
set synth [get_runs synth_1]
if {![string match "*synth_design Complete*" [get_property STATUS $synth]]} {
    launch_runs $synth -jobs 4
    wait_on_run $synth
    if {![string match "*synth_design Complete*" [get_property STATUS $synth]]} {
        error "Synthesis failed or stopped: [get_property STATUS $synth]. Inspect the first ERROR."
    }
}
set run [get_runs impl_1]
launch_runs $run -to_step write_bitstream -jobs 4
wait_on_run $run
if {![string match "*write_bitstream Complete*" [get_property STATUS $run]]} {
    error "Bitstream build failed or stopped: [get_property STATUS $run]. Inspect the first ERROR in the run log."
}
open_run $run
set artifacts [file join $board_root artifacts]
file mkdir $artifacts
report_utilization -file [file join $artifacts utilization_routed.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $artifacts timing_routed.rpt]
report_drc -file [file join $artifacts drc_routed.rpt]
write_hw_platform -fixed -include_bit -force -file [file join $artifacts kv260-vortex.xsa]
set bitfile [file join [get_property DIRECTORY $run] vortex_bd_wrapper.bit]
file copy -force $bitfile [file join $artifacts kv260-vortex.bit]
puts "INFO: Board bitstream exported to $artifacts. Review routed timing and DRC before deployment."
