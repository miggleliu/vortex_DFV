// SPDX-License-Identifier: Apache-2.0
// First KV260 integration: AXI-Lite control + 256 KiB program/data/stack RAM + standard Vortex console/MPM.
module kv260_vortex_bram import VX_gpu_pkg::*; (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET aresetn" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
       X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR",
       X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 19, READ_WRITE_MODE READ_WRITE" *)
    input wire [18:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output logic [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output logic s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [18:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output logic [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output logic [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output logic s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire s_axi_rready
);
    if (VX_MEM_PORTS != 1) begin : g_bad_ports
        initial $fatal(1, "This first board wrapper requires one native memory port");
    end
    logic aw_pending, w_pending, read_pending;
    logic [18:0] awaddr;
    logic [31:0] wdata;
    logic [3:0] wstrb;
    logic run_enable, active, done, seen_busy;
    logic [3:0] idle_count;
    logic [63:0] cycles;
    logic [31:0] error_code;
    logic [11:0] dcr_addr;
    logic dcr_valid;
    logic [31:0] dcr_data;
    wire gpu_reset = !aresetn || !run_enable;
    wire gpu_busy;
    wire req_valid [VX_MEM_PORTS], req_rw [VX_MEM_PORTS], req_ready [VX_MEM_PORTS];
    wire [VX_MEM_ADDR_WIDTH-1:0] req_addr [VX_MEM_PORTS];
    wire [VX_MEM_DATA_WIDTH-1:0] req_data [VX_MEM_PORTS];
    wire [VX_MEM_BYTEEN_WIDTH-1:0] req_byteen [VX_MEM_PORTS];
    wire [VX_MEM_TAG_WIDTH-1:0] req_tag [VX_MEM_PORTS];
    wire rsp_valid [VX_MEM_PORTS], rsp_ready [VX_MEM_PORTS];
    wire [VX_MEM_DATA_WIDTH-1:0] rsp_data [VX_MEM_PORTS];
    wire [VX_MEM_TAG_WIDTH-1:0] rsp_tag [VX_MEM_PORTS];
    wire address_fault;
    wire [31:0] console_data, console_count, perf_rdata;
    wire console_pop = read_fire && s_axi_araddr == 19'h00040;
    wire [31:0] host_rdata;
    wire write_fire = aw_pending && w_pending && !s_axi_bvalid;
    wire read_fire = s_axi_arvalid && s_axi_arready;
    wire command_start = write_fire && awaddr == 19'h00008 && wstrb[0] && wdata[0];
    wire host_allowed = !active && !command_start;
    wire host_write = write_fire && awaddr[18] && awaddr[1:0] == 0 && host_allowed;
    wire host_read = read_fire && s_axi_araddr[18] && s_axi_araddr[1:0] == 0 && host_allowed;
    // Simultaneous host read+write is serialized: ARREADY waits for writes.
    assign s_axi_arready = aresetn && !s_axi_rvalid && !read_pending && !write_fire;
    assign s_axi_awready = aresetn && !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = aresetn && !w_pending && !s_axi_bvalid;
    kv260_native_bram ram (
        .clk(aclk), .gpu_reset(gpu_reset), .io_clear(!aresetn || command_start),
        .console_pop(console_pop), .console_data(console_data), .console_count(console_count),
        .perf_addr(s_axi_araddr[7:2]), .perf_rdata(perf_rdata),
        .host_en(host_write || host_read), .host_write(host_write),
        .host_addr(host_write ? awaddr[17:0] : s_axi_araddr[17:0]),
        .host_wdata(wdata), .host_wstrb(wstrb), .host_rdata(host_rdata),
        .req_valid(req_valid[0]), .req_ready(req_ready[0]), .req_write(req_rw[0]),
        .req_addr(req_addr[0]), .req_data(req_data[0]), .req_byteen(req_byteen[0]),
        .req_tag(req_tag[0]), .rsp_valid(rsp_valid[0]), .rsp_ready(rsp_ready[0]),
        .rsp_data(rsp_data[0]), .rsp_tag(rsp_tag[0]), .address_fault(address_fault)
    );
    Vortex gpu (
        .clk(aclk), .reset(gpu_reset),
        .mem_req_valid(req_valid), .mem_req_rw(req_rw), .mem_req_addr(req_addr),
        .mem_req_byteen(req_byteen), .mem_req_data(req_data), .mem_req_tag(req_tag),
        .mem_req_ready(req_ready), .mem_rsp_valid(rsp_valid), .mem_rsp_data(rsp_data),
        .mem_rsp_tag(rsp_tag), .mem_rsp_ready(rsp_ready),
        .dcr_wr_valid(dcr_valid), .dcr_wr_addr(dcr_addr), .dcr_wr_data(dcr_data),
        .busy(gpu_busy)
    );
    function automatic [31:0] read_register(input [15:0] offset);
        case (offset)
            16'h0000: read_register = 32'h56584446;
            16'h0004: read_register = 32'h00020000;
            16'h0008: read_register = {31'b0,run_enable};
            16'h000c: read_register = {26'b0,seen_busy,gpu_reset,(error_code != 0),done,active,gpu_busy};
            16'h0010: read_register = 32'h01040420; // 1 core, 4 warps, 4 threads, RV32
            16'h0014: read_register = 32'h00040000; // shared RAM bytes
            16'h0020: read_register = {20'b0,dcr_addr};
            16'h0030: read_register = cycles[31:0];
            16'h0034: read_register = cycles[63:32];
            16'h0038: read_register = error_code;
            16'h0040: read_register = console_data;
            16'h0044: read_register = console_count;
            16'h0048: read_register = 32'h1a2cde44; // upstream revision signature
            16'h004c: read_register = 32'h00000404; // D-cache banks=4, I-cache KiB=4
            16'h0050: read_register = 32'h00001000; // D-cache bytes=4096
            default: read_register = 0;
        endcase
    endfunction
    always @(posedge aclk) begin
        if (!aresetn) begin
            aw_pending <= 0; w_pending <= 0; read_pending <= 0;
            awaddr <= 0; wdata <= 0; wstrb <= 0;
            s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_rvalid <= 0; s_axi_rresp <= 0; s_axi_rdata <= 0;
            run_enable <= 0; active <= 0; done <= 0; seen_busy <= 0;
            idle_count <= 0; cycles <= 0; error_code <= 0;
            dcr_addr <= 0; dcr_data <= 0; dcr_valid <= 0;
        end else begin
            dcr_valid <= 0;
            if (s_axi_awvalid && s_axi_awready) begin
                awaddr <= s_axi_awaddr; aw_pending <= 1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                wdata <= s_axi_wdata; wstrb <= s_axi_wstrb; w_pending <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (active) begin
                cycles <= cycles + 1;
                if (gpu_busy) seen_busy <= 1;
                if (seen_busy && !gpu_busy && !req_valid[0] && !rsp_valid[0]) begin
                    if (idle_count == 15) begin
                        done <= 1; active <= 0; run_enable <= 0;
                    end else idle_count <= idle_count + 1;
                end else idle_count <= 0;
            end
            if (address_fault) begin
                error_code <= 32'h00000002;
                active <= 0; run_enable <= 0;
            end
            if (write_fire) begin
                aw_pending <= 0; w_pending <= 0; s_axi_bvalid <= 1; s_axi_bresp <= 0;
                if (awaddr[1:0] != 0) s_axi_bresp <= 2'b10;
                else if (awaddr[18]) begin
                    if (!host_allowed) s_axi_bresp <= 2'b10;
                end else begin
                    case (awaddr[15:0])
                        16'h0008: if (wstrb[0]) begin
                            if (wdata[1]) begin
                                run_enable <= 0; active <= 0; done <= 0;
                                seen_busy <= 0; idle_count <= 0; error_code <= 0;
                            end else if (wdata[0] && !active) begin
                                run_enable <= 1; active <= 1; done <= 0;
                                seen_busy <= 0; idle_count <= 0; cycles <= 0;
                                error_code <= 0;
                            end else if (wdata[0]) s_axi_bresp <= 2'b10;
                        end
                        16'h0020: if (!active) begin
                            if (wstrb[0]) dcr_addr[7:0] <= wdata[7:0];
                            if (wstrb[1]) dcr_addr[11:8] <= wdata[11:8];
                        end else s_axi_bresp <= 2'b10;
                        16'h0024: if (!active && wstrb == 4'hf) begin
                            dcr_data <= wdata; dcr_valid <= 1;
                        end else s_axi_bresp <= 2'b10;
                        default: s_axi_bresp <= 2'b10;
                    endcase
                end
            end
            if (read_pending) begin
                s_axi_rdata <= host_rdata; s_axi_rvalid <= 1; s_axi_rresp <= 0;
                read_pending <= 0;
            end
            if (read_fire) begin
                s_axi_rresp <= 0;
                if (s_axi_araddr[1:0] != 0 || (s_axi_araddr[18] && !host_allowed)) begin
                    s_axi_rvalid <= 1; s_axi_rresp <= 2'b10; s_axi_rdata <= 0;
                end else if (s_axi_araddr[18]) read_pending <= 1;
                else begin
                    s_axi_rdata <= (s_axi_araddr[17:8] == 10'h010) ? perf_rdata : read_register(s_axi_araddr[15:0]); s_axi_rvalid <= 1;
                end
            end
        end
    end
    // AWPROT/ARPROT are carried by AXI-Lite but do not affect this lab peripheral.
endmodule
