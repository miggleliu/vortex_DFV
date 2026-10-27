// SPDX-License-Identifier: Apache-2.0
// Native RV32 memory: retain upstream code, heap, stack, console and MPM addresses.
module kv260_native_bram import VX_gpu_pkg::*; (
    input wire clk, gpu_reset, io_clear,
    input wire host_en, host_write,
    input wire [17:0] host_addr,
    input wire [31:0] host_wdata,
    input wire [3:0] host_wstrb,
    output wire [31:0] host_rdata,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [VX_MEM_ADDR_WIDTH-1:0] req_addr,
    input wire [VX_MEM_DATA_WIDTH-1:0] req_data,
    input wire [VX_MEM_BYTEEN_WIDTH-1:0] req_byteen,
    input wire [VX_MEM_TAG_WIDTH-1:0] req_tag,
    output logic rsp_valid,
    input wire rsp_ready,
    output logic [VX_MEM_DATA_WIDTH-1:0] rsp_data,
    output logic [VX_MEM_TAG_WIDTH-1:0] rsp_tag,
    output wire address_fault,
    input wire console_pop,
    output wire [31:0] console_data, console_count,
    input wire [5:0] perf_addr,
    output wire [31:0] perf_rdata
);
    if (VX_MEM_DATA_WIDTH != 512 || VX_MEM_ADDR_WIDTH != 26) begin : g_bad_config
        initial $fatal(1, "KV260 BRAM backend requires RV32 and 64-byte lines");
    end
    wire [31:0] byte_addr = {req_addr, 6'b0};
    wire is_code = byte_addr[31:16] == 16'h8000;
    wire is_heap = byte_addr[31:16] == 16'h0001;
    wire is_stack = byte_addr >= 32'hfffd0000 && byte_addr < 32'hffff0000;
    wire is_ram = is_code || is_heap || is_stack;
    wire is_console = byte_addr == 32'h40;
    wire is_perf = byte_addr >= 32'h80 && byte_addr < 32'h180;
    wire is_fence = !req_write && byte_addr == 0;
    wire [17:0] ram_addr = is_code ? {2'b00,byte_addr[15:0]} :
        is_heap ? {2'b01,byte_addr[15:0]} :
        18'h20000 + 18'(byte_addr - 32'hfffd0000);
    logic read_pending, read_ram, read_perf;
    logic [1:0] perf_line;
    logic [VX_MEM_TAG_WIDTH-1:0] read_tag;
    wire [511:0] gpu_line;
    wire [31:0] host_words [16];
    logic [3:0] host_bank;
    logic [31:0] perf_memory [64];
    assign perf_rdata = perf_memory[perf_addr];
    logic [511:0] cout_pending_data;
    logic [63:0] cout_pending_mask;
    // Store every enabled console byte and its upstream hart/channel ID.
    localparam FIFO_BITS=12;
    // Asynchronous first-word read for the AXI pop register uses LUT RAM.
    (* ram_style="distributed" *) logic [13:0] cout_fifo [0:(1<<FIFO_BITS)-1];
    logic [FIFO_BITS-1:0] cout_wr, cout_rd;
    logic [FIFO_BITS:0] cout_size;
    logic [5:0] cout_index;
    always_comb begin
        cout_index=0;
        for (integer i=63;i>=0;i=i-1)
            if(cout_pending_mask[i]) cout_index=6'(i);
    end
    wire cout_push = !io_clear && (cout_pending_mask != 0) && cout_size < 13'd4096;
    wire cout_pop = !io_clear && console_pop && cout_size != 0;
    assign console_data = cout_size == 0 ? 0 : {1'b1,17'b0,cout_fifo[cout_rd]};
    assign console_count = {19'b0,cout_size};
    // READY must not depend on the arbitrated request address: upstream's
    // bypass arbiters can select an address using the downstream READY signal.
    // Reserve enough FIFO room uniformly for every accepted request.
    assign req_ready = !gpu_reset && !read_pending && !rsp_valid &&
        cout_pending_mask == 0 && cout_size <= 13'd4032;
    wire fire = req_valid && req_ready;
    assign address_fault = fire && !(is_ram || is_console || is_perf || is_fence);
    wire gpu_ram_en = fire && is_ram;
    always @(posedge clk) begin
        if (host_en) host_bank <= host_addr[5:2];
        if(io_clear) begin
            cout_pending_mask<=0; cout_pending_data<=0;
            cout_wr<=0; cout_rd<=0; cout_size<=0;
            for(integer i=0;i<64;i++) perf_memory[i]<=0;
        end else begin
            if(cout_push) begin
                cout_fifo[cout_wr] <= {cout_index,cout_pending_data[8*cout_index +: 8]};
                cout_wr<=cout_wr+1'b1;
                cout_pending_mask<=cout_pending_mask & (cout_pending_mask-64'd1);
            end
            if(cout_pop) cout_rd<=cout_rd+1'b1;
            case ({cout_push,cout_pop})
                2'b10: cout_size<=cout_size+1'b1;
                2'b01: cout_size<=cout_size-1'b1;
                default:;
            endcase
            if(fire && req_write && is_console) begin
                cout_pending_mask<=req_byteen;
                cout_pending_data<=req_data;
            end
            if(fire && req_write && is_perf)
                for(integer i=0;i<16;i++)
                    for(integer b=0;b<4;b++)
                        if(req_byteen[4*i+b])
                            perf_memory[{byte_addr[7:6]-2'd2,4'(i)}][8*b+:8]
                                <=req_data[32*i+8*b+:8];
        end
    end
    assign host_rdata = host_words[host_bank];
    for(genvar bank=0;bank<16;bank++) begin : g_bank
        (* ram_style="block" *) logic [31:0] memory [0:4095];
        logic [31:0] host_q,gpu_q;
        always @(posedge clk) begin
            if(host_en && host_addr[5:2] == 4'(bank)) begin
                if(host_write)
                    for(integer b=0;b<4;b++)
                        if(host_wstrb[b]) memory[host_addr[17:6]][8*b+:8]<=host_wdata[8*b+:8];
                host_q<=memory[host_addr[17:6]];
            end
        end
        always @(posedge clk) begin
            if(gpu_ram_en) begin
                if(req_write)
                    for(integer b=0;b<4;b++)
                        if(req_byteen[4*bank+b]) memory[ram_addr[17:6]][8*b+:8]<=req_data[32*bank+8*b+:8];
                gpu_q<=memory[ram_addr[17:6]];
            end
        end
        assign host_words[bank]=host_q;
        assign gpu_line[32*bank+:32]=gpu_q;
    end
    always @(posedge clk) begin
        if(gpu_reset) begin
            read_pending<=0; read_ram<=0; read_perf<=0; perf_line<=0;
            read_tag<='0; rsp_valid<=0; rsp_data<='0; rsp_tag<='0;
        end else begin
            if(rsp_valid && rsp_ready) rsp_valid<=0;
            if(read_pending) begin
                rsp_valid<=1; rsp_tag<=read_tag; read_pending<=0;
                rsp_data<=read_ram ? gpu_line : '0;
                if(read_perf)
                    for(integer i=0;i<16;i++) rsp_data[32*i+:32]<=perf_memory[{perf_line,4'(i)}];
            end
            if(fire && !req_write) begin
                read_pending<=1; read_ram<=is_ram; read_perf<=is_perf;
                perf_line<=byte_addr[7:6]-2'd2; read_tag<=req_tag;
            end
        end
    end
endmodule
