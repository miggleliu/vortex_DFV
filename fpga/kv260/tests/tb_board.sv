module tb_kv260_vortex_bram;
    logic aclk = 0;
    always #5 aclk = !aclk;
    logic aresetn = 0;
    logic [18:0] s_axi_awaddr = 0, s_axi_araddr = 0;
    logic [2:0] s_axi_awprot = 0, s_axi_arprot = 0;
    logic s_axi_awvalid=0, s_axi_wvalid=0, s_axi_bready=0;
    logic s_axi_arvalid=0, s_axi_rready=0;
    wire s_axi_awready,s_axi_wready,s_axi_bvalid,s_axi_arready,s_axi_rvalid;
    logic [31:0] s_axi_wdata=0;
    logic [3:0] s_axi_wstrb=0;
    wire [1:0] s_axi_bresp,s_axi_rresp;
    wire [31:0] s_axi_rdata;
    kv260_vortex_bd dut(.*);
    always @(posedge aclk) begin
        if ($test$plusargs("trace") && dut.core.req_valid[0] && dut.core.req_ready[0])
            $display("MEM req: byte_addr=%h write=%b mask=%h data=%h", {dut.core.req_addr[0],6'b0},dut.core.req_rw[0],dut.core.req_byteen[0],dut.core.req_data[0][127:0]);
        if ($test$plusargs("trace") && dut.core.dcr_valid) $display("DCR addr=%h data=%h",dut.core.dcr_addr,dut.core.dcr_data);
    end
    integer transaction=0;
    task automatic axi_write(input [18:0] addr, input [31:0] data,
                             input [3:0] strobe=4'hf, input [1:0] expected_resp=0);
        integer delay_aw,delay_w;
        delay_aw=transaction%3;
        delay_w=(transaction+1)%4;
        transaction++;
        fork
            begin
                repeat(delay_aw) @(negedge aclk);
                @(negedge aclk); s_axi_awaddr=addr; s_axi_awvalid=1;
                do @(posedge aclk); while (!s_axi_awready);
                @(negedge aclk); s_axi_awvalid=0;
            end
            begin
                repeat(delay_w) @(negedge aclk);
                @(negedge aclk); s_axi_wdata=data; s_axi_wstrb=strobe; s_axi_wvalid=1;
                do @(posedge aclk); while (!s_axi_wready);
                @(negedge aclk); s_axi_wvalid=0;
            end
        join
        while(!s_axi_bvalid) @(negedge aclk);
        if(s_axi_bresp != expected_resp) $fatal(1,"Unexpected write response addr=%h resp=%h",addr,s_axi_bresp);
        repeat(3) begin
            @(negedge aclk);
            if(!s_axi_bvalid || s_axi_bresp != expected_resp) $fatal(1,"B channel unstable under backpressure");
        end
        s_axi_bready=1; @(posedge aclk); @(negedge aclk); s_axi_bready=0;
    endtask
    task automatic axi_read(input [18:0] addr, output [31:0] data);
        @(negedge aclk); s_axi_araddr=addr; s_axi_arvalid=1;
        do @(posedge aclk); while(!s_axi_arready);
        @(negedge aclk); s_axi_arvalid=0;
        while(!s_axi_rvalid) @(negedge aclk);
        if(s_axi_rresp != 0) $fatal(1,"Read response error addr=%h",addr);
        data=s_axi_rdata;
        repeat(3) begin
            @(negedge aclk);
            if(!s_axi_rvalid || s_axi_rdata != data) $fatal(1,"R channel unstable under backpressure");
        end
        s_axi_rready=1; @(posedge aclk); @(negedge aclk); s_axi_rready=0;
    endtask
    task automatic dcr(input [11:0] addr, input [31:0] data);
        axi_write(19'h20,{20'b0,addr}); axi_write(19'h24,data);
    endtask
    task automatic run_program(input [31:0] first_operand, input bit bad_address=0);
        reg [31:0] program_words[13];
        reg [31:0] value,status;
        bit finished;
        // run_sim.py emits the exact words used by the board's Python loader.
        if(first_operand==19) $readmemh("kernel_19.hex",program_words);
        else $readmemh("kernel_20.hex",program_words);
        if(bad_address) program_words[0]=32'h900012b7;
        axi_write(19'h8,2);
        for(integer i=0;i<32;i++) axi_write(19'h40000+19'(i*4),32'h00000013);
        for(integer i=0;i<13;i++) axi_write(19'h40000+19'(i*4),program_words[i]);
        for(integer i=0;i<16;i++) axi_write(19'h41000+19'(i*4),0);
        dcr(12'h001,32'h80000000); dcr(12'h003,0); dcr(12'h005,0);
        repeat(20) @(posedge aclk);
        axi_write(19'h8,1);
        finished=0;
        for(integer i=0;i<10000;i++) begin
            axi_read(19'hc,status);
            if(status[3] || status[2]) begin finished=1;break;end
        end
        if(!finished) $fatal(1,"GPU timeout, status=%h",status);
        if(bad_address) begin
            if(!status[3]) $fatal(1,"Out-of-range GPU address not reported");
            axi_read(19'h38,value);
            if(value != 2) $fatal(1,"Incorrect address-fault code");
            $display("PASS: out-of-range GPU memory access stops the GPU");
        end else begin
            if(status[3] || !status[2]) $fatal(1,"Unexpected GPU fault/status=%h",status);
            axi_read(19'h41000,value);
            if(value != first_operand+23) $fatal(1,"GPU ADD/store wrong: %h",value);
            axi_read(19'h41004,value);
            if(value != first_operand+24) $fatal(1,"GPU load/add/store wrong: %h",value);
            axi_read(19'h41008,value);
            if(value != 32'h12345678) $fatal(1,"GPU signature pattern wrong: %h",value);
            $display("PASS: GPU kernel operand=%0d, sum=%0d; reprogram/reset path checked",first_operand,first_operand+23);
        end
    endtask
    reg [31:0] value;
    initial begin
        repeat(15) @(posedge aclk);
        @(negedge aclk); aresetn=1;
        repeat(15) @(posedge aclk);
        axi_read(0,value); if(value!=32'h56584446) $fatal(1,"Peripheral ID wrong");
        axi_write(19'h40100,0);
        axi_write(19'h40100,32'haabbccdd,4'b0101);
        axi_read(19'h40100,value); if(value!=32'h00bb00dd) $fatal(1,"Host byte-write strobes wrong");
        axi_write(19'h40101,32'hffffffff,4'hf,2'b10);
        axi_read(19'h40100,value); if(value!=32'h00bb00dd) $fatal(1,"Misaligned write changed RAM");
        axi_write(19'h7fffc,32'hcafebabe);
        axi_read(19'h7fffc,value); if(value!=32'hcafebabe) $fatal(1,"Last shared-RAM word is inaccessible");
        $display("PASS: AXI-Lite independent AW/W, response backpressure, byte enables, alignment");
        run_program(19);
        run_program(20);
        run_program(19,1);
        $display("PASS: KV260 wrapper functional simulation complete");
        $finish;
    end
    initial begin
        #20000000;
        $fatal(1,"Global testbench timeout");
    end
endmodule
