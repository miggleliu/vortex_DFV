# SPDX-License-Identifier: Apache-2.0
# Search-only acceleration for the pinned fork's original RAM repair algorithm.
# Load after its procedure definitions and before vortex::resolve_async_brams.
namespace eval vortex {
    variable query_cache_cell ""
    variable query_cache_nets {}
    variable query_cache_names {}
    variable placeholder_cache {}
    variable placeholder_cache_enabled 0
    rename find_cell_nets original_find_cell_nets
    rename get_cell_net original_get_cell_net
    rename find_nested_cells original_find_nested_cells
    rename remove_cell_from_netlist original_remove_cell_from_netlist
    rename resolve_async_brams original_resolve_async_brams

    proc find_cell_nets {cell name_match {should_exist 1}} {
        variable query_cache_cell
        variable query_cache_nets
        if {$query_cache_cell ne $cell} {
            return [original_find_cell_nets $cell $name_match $should_exist]
        }
        set matches {}
        foreach pair $query_cache_nets {
            lassign $pair net name
            if {[regexp $name_match $name]} {lappend matches $net}
        }
        if {[llength $matches] == 0} {
            # Preserve original lookup/error behavior on unexpected naming.
            return [original_find_cell_nets $cell $name_match $should_exist]
        }
        return $matches
    }
    proc get_cell_net {cell name} {
        variable query_cache_cell
        variable query_cache_names
        if {$query_cache_cell eq $cell && [dict exists $query_cache_names $name]} {
            return [dict get $query_cache_names $name]
        }
        return [original_get_cell_net $cell $name]
    }
    proc find_nested_cells {parent_cell name_match {should_exist 1}} {
        variable placeholder_cache
        variable placeholder_cache_enabled
        if {!$placeholder_cache_enabled || $name_match ni {placeholder1$ g_async_ram.placeholder2$}} {
            return [original_find_nested_cells $parent_cell $name_match $should_exist]
        }
        set sep [get_hierarchy_separator]
        set prefix "${parent_cell}${sep}"
        set matches {}
        dict for {cell metadata} $placeholder_cache {
            lassign $metadata name parent
            if {[string first $prefix $name] != 0} {continue}
            set pattern "[regex_escape ${parent}${sep}]${name_match}"
            if {[regexp $pattern $name]} {lappend matches $cell}
        }
        if {[llength $matches] == 0} {
            return [original_find_nested_cells $parent_cell $name_match $should_exist]
        }
        return $matches
    }
    proc remove_cell_from_netlist {cell} {
        variable placeholder_cache
        original_remove_cell_from_netlist $cell
        if {[dict exists $placeholder_cache $cell]} {dict unset placeholder_cache $cell}
    }
    proc resolve_async_brams {} {
        variable query_cache_cell
        variable query_cache_nets
        variable query_cache_names
        variable placeholder_cache
        variable placeholder_cache_enabled
        set cells [get_cells -hierarchical -filter {REF_NAME =~ "*VX_async_ram_patch*"}]
        set placeholder_cache {}
        foreach cell [get_cells -quiet -hierarchical -filter {REF_NAME =~ "*VX_placeholder*"}] {
            dict set placeholder_cache $cell [list [get_property NAME $cell] [get_property PARENT $cell]]
        }
        set placeholder_cache_enabled 1
        set index 0
        set code [catch {
            foreach cell $cells {
                incr index
                puts "INFO: RAM repair $index/[llength $cells]: $cell"
                set query_cache_cell $cell
                set query_cache_nets {}
                set query_cache_names {}
                # These nets are all queried before resolve_async_bram rewires
                # anything. Keep their original Vivado collection order.
                foreach net [get_nets -hierarchical -filter "PARENT_CELL == $cell"] {
                    set name [get_property NAME $net]
                    lappend query_cache_nets [list $net $name]
                    dict set query_cache_names $name $net
                }
                resolve_async_bram $cell
                set query_cache_cell ""
            }
        } result options]
        set query_cache_cell ""
        set query_cache_nets {}
        set query_cache_names {}
        set placeholder_cache_enabled 0
        set placeholder_cache {}
        if {$code} {return -options $options $result}
        puts "INFO: RAM repair finished for [llength $cells] instances."
    }
}
