// SPDX-License-Identifier: Apache-2.0
`include "VX_define.vh"
`ifndef DFV_TEST_CORE_ID
`define DFV_TEST_CORE_ID 0
`endif
module tb_dfv_collision_csr import VX_gpu_pkg::*; ();
    logic clk=0, reset=1, write_enable=0, write_ready=1, read_enable=0;
    logic [11:0] read_addr='0, write_addr='0;
    logic [`XLEN-1:0] write_data='0;
    wire [`XLEN-1:0] read_data_ro, read_data_rw;
    base_dcrs_t base_dcrs='0;
    logic [PERF_CTR_BITS-1:0] cycles='0;
    dfv_collision_t dfv_collision [DFV_COLLISION_COUNT];
    dfv_throttle_t dfv_throttle [`UP(DFV_THROTTLE_COUNT)];
    wire dfv_enable;
    VX_commit_csr_if commit_csr_if();
    assign commit_csr_if.instret='0;
`ifdef EXT_F_ENABLE
    VX_fpu_csr_if fpu_csr_if [`NUM_FPU_BLOCKS]();
    for (genvar i=0; i<`NUM_FPU_BLOCKS; ++i) begin : g_fpu
        assign fpu_csr_if[i].write_enable=0;
        assign fpu_csr_if[i].write_wid='0;
        assign fpu_csr_if[i].write_fflags='0;
        assign fpu_csr_if[i].read_wid='0;
        `UNUSED_VAR (fpu_csr_if[i].read_frm)
    end
`endif
    VX_csr_data #(.INSTANCE_ID("indexed-dfv-test"), .CORE_ID(`DFV_TEST_CORE_ID)) dut (
        .clk(clk), .reset(reset), .base_dcrs(base_dcrs), .cycles(cycles),
        .commit_csr_if(commit_csr_if),
`ifdef EXT_F_ENABLE
        .fpu_csr_if(fpu_csr_if),
`endif
        .active_warps('1), .thread_masks('1),
        .read_enable(read_enable), .read_uuid('0), .read_wid('0),
        .read_addr(read_addr), .read_data_ro(read_data_ro), .read_data_rw(read_data_rw),
        .write_enable(write_enable), .write_uuid('0), .write_wid('0),
        .write_addr(write_addr), .write_data(write_data), .write_ready(write_ready),
        .dfv_collision(dfv_collision), .dfv_throttle(dfv_throttle), .dfv_enable(dfv_enable),
        `UNUSED_PIN (dfv_stall_icache_fill_req), `UNUSED_PIN (dfv_stall_dcache_fill_req),
        `UNUSED_PIN (dfv_stall_dcache_core_req), `UNUSED_PIN (dfv_stall_writeback),
        `UNUSED_PIN (dfv_stall_dcache_fill_rsp), `UNUSED_PIN (dfv_fill_bank_mask),
        `UNUSED_PIN (dfv_throttle_threshold), `UNUSED_PIN (dfv_bank_throttle_threshold)
    );

    logic ca=0, cb=0, ce=0, ts=0;
    logic [15:0] threshold=0;
    wire [31:0] natural_count, dfv_count, throttle_events;
    wire [15:0] observed_threshold;
    VX_dfv_collision_ctr primitive_collision (
        .clk(clk), .reset(reset), .enable(ce), .event_a(ca), .event_b(cb),
        .natural_edge_count(natural_count), .dfv_edge_count(dfv_count)
    );
    VX_dfv_throttle_ctr primitive_throttle (
        .clk(clk), .reset(reset), .signal_in(ts), .threshold(threshold),
        .event_count(throttle_events), .observed_threshold(observed_threshold),
        `UNUSED_PIN (reached)
    );
    task automatic tick;
        #1; clk=1; #1; clk=0; #1;
    endtask
    task automatic write_csr(input logic [11:0] addr,
                             input logic [`XLEN-1:0] value, input logic ready_value=1);
        read_enable=0; write_enable=1; write_addr=addr;
        write_data=value; write_ready=ready_value; tick();
        write_enable=0; write_ready=1; #1;
    endtask
    task automatic check_csr(input logic [11:0] addr, input logic [`XLEN-1:0] expected);
        read_enable=1; read_addr=addr; #1;
        if ((read_data_ro | read_data_rw) !== expected)
            $fatal(1,"CSR %h expected %h got %h",addr,expected,read_data_ro | read_data_rw);
        tick(); read_enable=0;
    endtask
    initial begin
        for (integer i=0;i<DFV_COLLISION_COUNT;++i)
            dfv_collision[i]='{available:1'b1,natural_count:32'(10000+i),dfv_count:32'(20000+i)};
        for (integer i=0;i<`UP(DFV_THROTTLE_COUNT);++i)
            dfv_throttle[i]='{available:1'b1,event_count:32'(30000+i),threshold:16'(500+i)};
        tick();tick();reset=0;tick();
        if ($test$plusargs("invalid_write")) begin
            write_csr(12'h7d3,`XLEN'(1)); $fatal(1,"unknown write accepted");
        end
        if ($test$plusargs("invalid_read")) begin
            check_csr(12'hce1,'0); $fatal(1,"unknown read accepted");
        end
        check_csr(`VX_CSR_DFV_COLL_INFO,`XLEN'(`VX_DFV_COLLISION_ID));
        check_csr(`VX_CSR_DFV_THR_INFO,`XLEN'(`VX_DFV_THROTTLE_ID));
        check_csr(`VX_CSR_DFV_COLL_COUNT,`XLEN'(DFV_COLLISION_COUNT));
        check_csr(`VX_CSR_DFV_THR_COUNT,`XLEN'(DFV_THROTTLE_COUNT));
        check_csr(`VX_CSR_DFV_COLL_STATUS,'0);
        if (DFV_THROTTLE_COUNT!=0) check_csr(`VX_CSR_DFV_THR_STATUS,'0);
        write_csr(12'h7c0,`XLEN'(1));
        cycles=44'habc12345678;
        write_csr(`VX_CSR_DFV_COLL_CTRL,`XLEN'(1));
        if (DFV_THROTTLE_COUNT!=0) check_csr(`VX_CSR_DFV_THR_STATUS,'0);
        cycles=44'hdef87654321;
        write_csr(`VX_CSR_DFV_THR_CTRL,`XLEN'(1));
        for (integer i=0;i<DFV_COLLISION_COUNT;++i) begin
            // Live changes after capture cannot leak into any selected bank.
            dfv_collision[i].natural_count=32'(40000+i);
            write_csr(`VX_CSR_DFV_COLL_SELECT,`XLEN'(i));
            check_csr(`VX_CSR_DFV_COLL_NATURAL,`XLEN'(10000+i));
            check_csr(`VX_CSR_DFV_COLL_DFV,`XLEN'(20000+i));
            check_csr(`VX_CSR_DFV_COLL_STATUS,`XLEN'(9));
            check_csr(`VX_CSR_DFV_COLL_DESC,`XLEN'(dfv_collision_desc(32'(i))));
            check_csr(`VX_CSR_DFV_COLL_OWNER,`XLEN'((i<DFV_LOCAL_COLL_COUNT)?(`DFV_TEST_CORE_ID/`SOCKET_SIZE):((i<DFV_LOCAL_COLL_COUNT+DFV_L2_COLL_COUNT)?(`DFV_TEST_CORE_ID/`NUM_CORES):0)));
            check_csr(`VX_CSR_DFV_COLL_CYCLE_LO,`XLEN'(32'h12345678));
            check_csr(`VX_CSR_DFV_COLL_CYCLE_HI,`XLEN'(12'habc));
        end
        for (integer i=0;i<DFV_THROTTLE_COUNT;++i) begin
            dfv_throttle[i].event_count=32'(50000+i);
            write_csr(`VX_CSR_DFV_THR_SELECT,`XLEN'(i));
            check_csr(`VX_CSR_DFV_THR_EVENTS,`XLEN'(30000+i));
            check_csr(`VX_CSR_DFV_THR_STATUS,`XLEN'(9));
            check_csr(`VX_CSR_DFV_THR_DESC,`XLEN'(dfv_throttle_desc(32'(i))));
            check_csr(`VX_CSR_DFV_THR_THRESHOLD,`XLEN'(500+i));
            check_csr(`VX_CSR_DFV_THR_OWNER,`XLEN'(`DFV_TEST_CORE_ID/`SOCKET_SIZE));
            check_csr(`VX_CSR_DFV_THR_CYCLE_LO,`XLEN'(32'h87654321));
            check_csr(`VX_CSR_DFV_THR_CYCLE_HI,`XLEN'(12'hdef));
        end
        write_csr(`VX_CSR_DFV_COLL_SELECT,'0);
        write_csr(`VX_CSR_DFV_COLL_CTRL,`XLEN'(1),0);
        check_csr(`VX_CSR_DFV_COLL_NATURAL,`XLEN'(10000));
        write_csr(`VX_CSR_DFV_COLL_CTRL,`XLEN'(2));
        check_csr(`VX_CSR_DFV_COLL_NATURAL,`XLEN'(10000));
        write_csr(`VX_CSR_DFV_COLL_SELECT,`XLEN'(DFV_COLLISION_COUNT));
        check_csr(`VX_CSR_DFV_COLL_STATUS,`XLEN'(17));
        check_csr(`VX_CSR_DFV_COLL_NATURAL,'0);
        write_csr(`VX_CSR_DFV_COLL_SELECT,'0);
        write_csr(`VX_CSR_DFV_THR_SELECT,`XLEN'(DFV_THROTTLE_COUNT));
        check_csr(`VX_CSR_DFV_THR_STATUS,`XLEN'(17));
        check_csr(`VX_CSR_DFV_COLL_SELECT,'0);
        write_csr(`VX_CSR_DFV_COLL_CTRL,'0);
        check_csr(`VX_CSR_DFV_COLL_STATUS,'0);
        write_csr(`VX_CSR_DFV_THR_SELECT,'0);
        if (DFV_THROTTLE_COUNT!=0) check_csr(`VX_CSR_DFV_THR_STATUS,`XLEN'(9));
        dfv_collision[0].natural_count=32'hffffffff;
        write_csr(`VX_CSR_DFV_COLL_CTRL,`XLEN'(1));
        check_csr(`VX_CSR_DFV_COLL_STATUS,`XLEN'(11));
        dfv_collision[0].available=0;
        dfv_collision[0].natural_count=0;
        write_csr(`VX_CSR_DFV_COLL_CTRL,`XLEN'(1));
        check_csr(`VX_CSR_DFV_COLL_STATUS,`XLEN'(1));
        write_csr(`VX_CSR_DFV_THR_CTRL,'0);
        if (DFV_THROTTLE_COUNT!=0) check_csr(`VX_CSR_DFV_THR_STATUS,'0);
        if (!dfv_enable) $fatal(1,"observation changed DFV enable");

        // The existing event definition, including registered inputs, is kept.
        ce=0;ca=0;cb=0; repeat(3) tick();
        ca=1;cb=1;repeat(4) tick();
        ca=0;cb=0;ce=1;repeat(4) tick();
        ca=1;cb=1;repeat(8) tick();
        if(natural_count!=1 || dfv_count!=1) $fatal(1,"collision event classification/edge semantics changed");
        ts=1;threshold=3;repeat(6) tick();
        ts=0;repeat(10) tick();ts=1;repeat(6) tick();ts=0;repeat(10) tick();
        if(throttle_events!=2 || observed_threshold!=3) $fatal(1,"throttle rising-edge count mismatch");
        $display("THROTTLE_CSR_REFERENCE events=%0d",throttle_events);
        reset=1;tick();reset=0;tick();
        check_csr(`VX_CSR_DFV_COLL_STATUS,'0);
        if (DFV_THROTTLE_COUNT!=0) check_csr(`VX_CSR_DFV_THR_STATUS,'0);
        if(dfv_enable) $fatal(1,"reset failed");
        $display("PASS: RV%0d indexed collision=%0d throttle=%0d independent CSR families",`XLEN,DFV_COLLISION_COUNT,DFV_THROTTLE_COUNT);
        $finish;
    end
endmodule
