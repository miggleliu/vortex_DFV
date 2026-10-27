module tb_backend;
    import VX_gpu_pkg::*;
    logic clk=0; always #5 clk=!clk;
    logic gpu_reset=1,io_clear=1,host_en=0,host_write=0;
    logic [17:0] host_addr=0;
    logic [31:0] host_wdata=0;
    logic [3:0] host_wstrb=0;
    wire [31:0] host_rdata;
    logic req_valid=0,req_write=0;
    wire req_ready;
    logic [VX_MEM_ADDR_WIDTH-1:0] req_addr=0;
    logic [VX_MEM_DATA_WIDTH-1:0] req_data=0;
    logic [VX_MEM_BYTEEN_WIDTH-1:0] req_byteen=0;
    logic [VX_MEM_TAG_WIDTH-1:0] req_tag=0;
    wire rsp_valid;
    logic rsp_ready=0;
    wire [VX_MEM_DATA_WIDTH-1:0] rsp_data;
    wire [VX_MEM_TAG_WIDTH-1:0] rsp_tag;
    wire address_fault;
    logic console_pop=0;
    wire [31:0] console_data,console_count;
    logic [5:0] perf_addr=0;
    wire [31:0] perf_rdata;
    kv260_native_bram dut(.*);
    task automatic tick; @(posedge clk); @(negedge clk); endtask
    task automatic host_put(input [17:0] a,input [31:0] v);
        host_en=1;host_write=1;host_addr=a;host_wdata=v;host_wstrb=15;tick();host_en=0;host_write=0;
    endtask
    task automatic host_check(input [17:0] a,input [31:0] v);
        host_en=1;host_write=0;host_addr=a;tick();host_en=0;
        if(host_rdata!==v) $fatal(1,"host RAM mismatch addr=%h got=%h expected=%h",a,host_rdata,v);
    endtask
    task automatic send(input [31:0] a,input bit wr,input [511:0] d,input [63:0] mask,input bit fault=0);
        req_addr=26'(a>>6);req_write=wr;req_data=d;req_byteen=mask;req_valid=1;req_tag=VX_MEM_TAG_WIDTH'(7);#1;
        while(!req_ready) tick();
        if(address_fault!==fault) $fatal(1,"fault decode wrong addr=%h",a);
        tick();req_valid=0;
    endtask
    task automatic get(input [31:0] a,input [31:0] expected);
        send(a,0,0,0);
        while(!rsp_valid) tick();
        if(rsp_data[31:0]!==expected || rsp_tag!=VX_MEM_TAG_WIDTH'(7)) $fatal(1,"GPU read mismatch addr=%h got=%h",a,rsp_data[31:0]);
        repeat(3) begin tick();if(!rsp_valid || rsp_data[31:0]!==expected) $fatal(1,"response changed under backpressure");end
        rsp_ready=1;tick();rsp_ready=0;
    endtask
    task automatic pop_check(input [31:0] expected);
        if(console_data!==expected) $fatal(1,"console ordering/data wrong got=%h expected=%h",console_data,expected);
        console_pop=1;tick();console_pop=0;
    endtask
    logic [511:0] data;
    initial begin
        tick();tick();io_clear=0;gpu_reset=0;tick();
        host_put(18'h0,32'h11111111);
        host_put(18'h10000,32'h22222222);
        host_put(18'h20000,32'h33333333);
        host_put(18'h3ffc0,32'h44444444);
        get(32'h80000000,32'h11111111);
        get(32'h00010000,32'h22222222);
        get(32'hfffd0000,32'h33333333);
        get(32'hfffeffc0,32'h44444444);
        send(32'h10000,1,512'(32'haabbccdd),64'h5);
        host_check(18'h10000,32'h22bb22dd);
        send(32'h90000000,1,0,0,1);
        get(0,0);
        $display("PASS: code/heap/stack translation, byte masks, fence and address faults");
        data=0; data[7:0]="A";data[23:16]="B";data[511:504]="Z";
        send(32'h40,1,data,64'h8000000000000005);
        repeat(5) tick();
        if(console_count!=3) $fatal(1,"console byte lost");
        pop_check(32'h80000041);pop_check(32'h80000242);pop_check(32'h80003f5a);
        if(console_data!=0) $fatal(1,"empty FIFO must not invent data");
        for(integer i=0;i<64;i++) send(32'h40,1,512'(i),64'hffffffffffffffff);
        repeat(66) tick();
        req_addr=26'(32'h40>>6);
        if(console_count!=4096 || req_ready) $fatal(1,"full console must backpressure GPU");
        for(integer i=0;i<64;i++) begin console_pop=1;tick();end
        console_pop=0;
        if(!req_ready || console_count!=4032) $fatal(1,"console did not recover after drain");
        $display("PASS: console multi-byte/channel ordering, full backpressure and recovery");
        send(32'h80,1,512'(32'h11223344),64'hf);
        send(32'h140,1,512'(32'h55667788),64'hf);
        perf_addr=0;tick();if(perf_rdata!=32'h11223344) $fatal(1,"MPM low address wrong");
        perf_addr=48;tick();if(perf_rdata!=32'h55667788) $fatal(1,"MPM high address wrong");
        get(32'h80,32'h11223344);get(32'h140,32'h55667788);
        gpu_reset=1;tick();
        perf_addr=0;tick();if(perf_rdata!=32'h11223344 || console_count!=4032) $fatal(1,"completion reset erased IO results");
        io_clear=1;tick();io_clear=0;tick();
        if(perf_rdata!=0 || console_count!=0) $fatal(1,"new launch did not clear IO");
        $display("PASS: MPM line decoding, completion retention and new-run clearing");
        $finish;
    end
    initial begin #2000000;$fatal(1,"backend test timed out");end
endmodule
