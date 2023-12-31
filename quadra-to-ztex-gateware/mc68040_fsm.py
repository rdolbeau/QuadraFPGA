from migen import *
from migen.genlib.fifo import *
from migen.genlib.cdc import *
from migen.fhdl.specials import Tristate

import litex
from litex.soc.interconnect import wishbone

class MC68040_FSM(Module):
    def __init__(self, soc, wb_read, wb_write, dram_native_r, dram_native_w, cd_cpu="cpu", trace_inst_fifo = None):

        platform = soc.platform

        sync_cpu = getattr(self.sync, cd_cpu)
        
        # 68040
        A = platform.request("A_3v3") # 32 # address, I[O]
        D = platform.request("D_3v3") # 32 # data, IO
        RW_n = platform.request("rw_3v3_n") #  direction of bus transfer with respect to the main processor, I [three-state, high read, write low]
        SIZ = platform.request("siz_3v3") # 2, I
        # CIOUT_n = platform.request("ciout_3v3_n") # cache inhibit out (from cpu), I
        TBI_n = platform.request("tbi_3v3_n") # Transfer Burst Inhibit, O
        TIP_CPU_n = platform.request("tip_cpu_3v3_n") #  I
        TA_n = platform.request("ta_3v3_n") # Transfer Acknowledge, O
        TEA_n = platform.request("tea_3v3_n") # Transfer Error Acknowledge, O
        TS_n = platform.request("ts_3v3_n") # Transfer Start, I
        TT = platform.request("tt_3v3") # 2, I
        TM = platform.request("tm_3v3") # 3 Transfer Modifier , I
        MI_n = platform.request("mi_3v3_n") # Memory Inhibit, I

        A_i = Signal(32)
        #A_latch = Signal(32)
        self.comb += [ A_i.eq(A) ]
        
        D_i = Signal(32)
        D_o = Signal(32)
        D_oe = Signal(reset = 0)
        self.specials += Tristate(D, D_o, D_oe, D_i)

        D_rev_i = Signal(32)
        D_rev_o = Signal(32)

        # ugly byte reversal, invert endianess to match NuBusFPGA ...
        self.comb += [
            D_rev_i[ 0: 8].eq(D_i[24:32]),
            D_rev_i[ 8:16].eq(D_i[16:24]),
            D_rev_i[16:24].eq(D_i[ 8:16]),
            D_rev_i[24:32].eq(D_i[ 0: 8]),
            
            D_o[ 0: 8].eq(D_rev_o[24:32]),
            D_o[ 8:16].eq(D_rev_o[16:24]),
            D_o[16:24].eq(D_rev_o[ 8:16]),
            D_o[24:32].eq(D_rev_o[ 0: 8]),
        ]
        
        RW_i_n = Signal(1)
        self.comb += [ RW_i_n.eq(RW_n) ]
        
        SIZ_i = Signal(2)
        self.comb += [ SIZ_i.eq(SIZ) ]
        
        TM_i = Signal(3)
        self.comb += [ TM_i.eq(TM) ]
        
        TT_i = Signal(2)
        self.comb += [ TT_i.eq(TT) ]
        
        TS_i_n = Signal()
        self.comb += [ TS_i_n.eq(TS_n) ]
        
        TIP_CPU_i_n = Signal()
        self.comb += [ TIP_CPU_i_n.eq(TIP_CPU_n) ]

        MI_i_n = Signal()
        self.comb += [ MI_i_n.eq(MI_n) ]
        
        
        #CIOUT_i_n = Signal(1)
        #self.comb += [ CIOUT_i_n.eq(CIOUT_n) ]

        # force tristate
        TEA_i_n = Signal(1)
        TEA_o_n = Signal(1, reset = 1)
        TEA_oe = Signal(reset = 0)
        self.specials += Tristate(TEA_n, TEA_o_n, TEA_oe, TEA_i_n)

        # force tristate
        TA_i_n = Signal(1)
        TA_o_n = Signal(1, reset = 1)
        TA_oe = Signal(reset = 0)
        self.specials += Tristate(TA_n, TA_o_n, TA_oe, TA_i_n)

        # force tristate
        TBI_i_n = Signal(1)
        TBI_o_n = Signal(1, reset = 1)
        TBI_oe = Signal(reset = 0)
        self.specials += Tristate(TBI_n, TBI_o_n, TBI_oe, TBI_i_n)

        # 23 first bits not rewritten (8 MiB)
        # address rewriting (slot)
        slot_processed_ad = Signal(32)
        self.comb += [
            If(~A_i[23], # first 8 MiB of slot space: remap to last 8 Mib of SDRAM
               slot_processed_ad[23:32].eq(Cat(Signal(1, reset=1), Signal(8, reset = 0x8f))), # 0x8f8...
            ).Else( # second 8 MiB: direct access
                slot_processed_ad[23:32].eq((Cat(Signal(1, reset=1), Signal(8, reset = 0xf0)))), # 24 bits, a.k.a 22 bits of words
            )
        ]

        # address rewriting (mem)
        mem_processed_ad = Signal(32)
        self.comb += [
            #mem_processed_ad[23:27].eq(A_i[23:27]),
            #mem_processed_ad[27:32].eq(Signal(5, reset=0x10)), # 0x80 >> 3 == 0x10
            mem_processed_ad[23:28].eq(A_i[23:28]),
            mem_processed_ad[28:32].eq(Signal(4, reset=0x8)), # 0x80 >> 4 == 0x8
            ##mem_processed_ad[23:26].eq(A_i[23:26]),
            ##mem_processed_ad[26:32].eq(Signal(6, reset=0x20)), # 0x80 >> 2 == 0x20
        ]

        # address rewriting (superslot)
        superslot_processed_ad = Signal(32)
        self.comb += [
            superslot_processed_ad[23:28].eq(A_i[23:28]),
            superslot_processed_ad[28:32].eq(Signal(4, reset=0x8)), # 0x80 >> 4 == 0x8
        ]

        # selection logic
        my_slot_space = Signal()
        self.comb += [ my_slot_space.eq((A_i[24:32] == 0xFE)) ] # fixme: abstract slot $E
        
        my_mem_space = Signal()
        # As soons as I enable this at $2000_0000 to $2FFF_FFFF, some "chimes of death" occur...
        # So djMEMC basically has 10 banks of up to 64 MiB, and checks for all of them
        # on every systems, so from $0000_0000 to $27FF_FFFF
        # So presumably we can live at $3000_0000
        # However, the ROM code hardwires the 10 banks, and there's some configuration done to djMEMC
        # So adding extra banks isn't going to be obvious...
        # Also are we ASC-based ? That would mean SoundBuffer in high RAM, which we may interfere with due to higher read latency...
        #self.comb += [ my_mem_space.eq(MI_i_n & (A_i[28:32] == 0x3)) ] # 0x30 >> 4 == 0x3 # only 256 MiB
        self.comb += [ my_mem_space.eq(MI_i_n & 0), ]
        
        my_superslot_space = Signal()
        self.comb += [ my_superslot_space.eq((A_i[28:32] == 0xE)) ] # 0xE0 >> 4 == 0xE # fixme: abstract slot $E
        
        my_device_space = Signal() # all three above

        # more selection logic
        processed_ad = Signal(32)
        self.comb += [
            processed_ad[ 0:23].eq(A_i[ 0:23]),
            If(my_slot_space,
               processed_ad[23:32].eq(slot_processed_ad[23:32]),
            ).Elif(my_mem_space,
                   processed_ad[23:32].eq(mem_processed_ad[23:32]),
            ).Elif(my_superslot_space,
                   processed_ad[23:32].eq(superslot_processed_ad[23:32]),
            ).Else(
                processed_ad[23:32].eq(A_i[23:32]),
            ),
            my_device_space.eq(my_slot_space | my_mem_space | my_superslot_space),
        ]

        # write FIFO to speed up bus turnaround on CPU side
        write_fifo_layout = [
            ("adr", 32),
            ("data", 32),
            ("sel", 4),
        ]
        #self.submodules.write_fifo = ClockDomainsRenamer({"read": "sys", "write": cd_cpu})(AsyncFIFOBuffered(width=layout_len(write_fifo_layout), depth=16))
        #write_fifo_front = self.write_fifo
        #write_fifo_back = self.write_fifo
        front_fifo_depth = 8
        front_fifo_level_check = (front_fifo_depth - 4) # will be compared to 'level', "Number of unread entries", we need at least 4 free slots for a burst
        self.submodules.write_fifo_front = write_fifo_front = ClockDomainsRenamer(cd_cpu)(SyncFIFOBuffered(width=layout_len(write_fifo_layout), depth=front_fifo_depth))
        self.submodules.write_fifo_back  = write_fifo_back =  ClockDomainsRenamer({"read": "sys",  "write": cd_cpu})(AsyncFIFOBuffered(width=layout_len(write_fifo_layout), depth=32))
        
        write_fifo_back_dout = Record(write_fifo_layout)
        self.comb += write_fifo_back_dout.raw_bits().eq(write_fifo_back.dout)
        write_fifo_front_din = Record(write_fifo_layout)
        self.comb += write_fifo_front.din.eq(write_fifo_front_din.raw_bits())

        # back-to-back FIFO
        self.comb += [
            write_fifo_front.re.eq(write_fifo_back.writable),
            write_fifo_back.we.eq(write_fifo_front.readable),
            # The XOR with 0xFFFFFFFF here and in the FIFO output serves not logical purpose, other than it doesn't work without it!!!
            write_fifo_back.din.eq(write_fifo_front.dout ^ Cat(Signal(32, reset = 0), Signal(32, reset = 0xFFFFFFFF), Signal(4, reset = 0))),
        ]

        
        # and now for burst
        write_fifo_burst_layout = [
            ("adr", 32),
            ("data", 128),
        ]
        self.submodules.write_fifo_burst  = write_fifo_burst =  ClockDomainsRenamer(cd_cpu)(SyncFIFOBuffered(width=layout_len(write_fifo_burst_layout), depth=8))
        
        write_fifo_burst_dout = Record(write_fifo_burst_layout)
        self.comb += write_fifo_burst_dout.raw_bits().eq(write_fifo_burst.dout)
        write_fifo_burst_din = Record(write_fifo_burst_layout)
        self.comb += write_fifo_burst.din.eq(write_fifo_burst_din.raw_bits())
        

        # back-pressure from sys to cpu clock domain for RAW hazards
        self.submodules.write_fifo_back_readable_sync = BusSynchronizer(width = 1, idomain = "sys", odomain = cd_cpu)
        write_fifo_back_readable_in_cpu = Signal()
        self.comb += self.write_fifo_back_readable_sync.i.eq(write_fifo_back.readable)
        self.comb += write_fifo_back_readable_in_cpu.eq(self.write_fifo_back_readable_sync.o)

        self.submodules.slave_fsm = slave_fsm = ClockDomainsRenamer(cd_cpu)(FSM(reset_state="Reset"))

        ### dram_native_r
        self.comb += [
            dram_native_r.cmd.we.eq(0),
            dram_native_r.cmd.addr.eq(processed_ad[4:]), # assume 128 bits (16 bytes)
        ]
        ## dram_native_r.cmd.valid ->
        ## dram_native_r.cmd.we ->
        ## dram_native_r.cmd.ready <-
        ## dram_native_r.rdata.valid <-
        ## dram_native_r.rdata.data <-
        burst_counter = Signal(2)
        burst_buffer = Signal(128)

        finishing = Signal()
        
        slave_fsm.act("Reset",
                      NextState("Idle")
        )
        slave_fsm.act("Idle",
                      NextValue(finishing, 0), # technically we should only drive for one-half cycle... use clock signal?
                      D_oe.eq(0),
                      TA_oe.eq(finishing & ClockSignal(cd_cpu)),
                      TA_o_n.eq(1),
                      TEA_oe.eq(finishing & ClockSignal(cd_cpu)),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(finishing & ClockSignal(cd_cpu)),
                      TBI_o_n.eq(1),
                      If(my_slot_space & ~A_i[23] & ~TS_i_n & ~RW_i_n & SIZ_i[0] & SIZ_i[1], # Burst write to FB memory
                             TA_oe.eq(1),
                             TA_o_n.eq(1),
                             TEA_oe.eq(1),
                             TEA_o_n.eq(1),
                             TBI_oe.eq(1),
                             TBI_o_n.eq(1),
                             NextValue(burst_counter, 0), # '040 burst are aligned
                             #NextValue(A_latch, processed_ad),
                             If(write_fifo_burst.writable,
                                NextState("FBMemBurstWrite"),
                             ).Else(
                                NextState("DelayFBMemBurstWrite"),
                             )
                      ).Elif((my_superslot_space | (my_slot_space & ~A_i[23])) & ~TS_i_n & RW_i_n & SIZ_i[0] & SIZ_i[1], # Burst read to (FB) memory
                             TA_oe.eq(1),
                             TA_o_n.eq(1),
                             TEA_oe.eq(1),
                             TEA_o_n.eq(1),
                             TBI_oe.eq(1),
                             TBI_o_n.eq(1),
                             NextValue(burst_counter, 0), # '040 burst are aligned
                             #dram_native_r.cmd.we.eq(0),
                             If(~write_fifo_back_readable_in_cpu & ~write_fifo_front.readable & ~write_fifo_burst.readable, # previous write(s) done
                                dram_native_r.cmd.valid.eq(1),
                                If(dram_native_r.cmd.ready, # interface available
                                   NextState("FBMemBurstReadWait"),
                                ).Else(
                                    NextState("DelayFBMemBurstReadWait"),
                                ),
                             ).Else(
                                 NextState("DelayFBMemBurstReadWait"),
                             )
                      ).Elif((my_device_space & ~TS_i_n & ~RW_i_n & SIZ_i[0] & SIZ_i[1]), # burst Write through FIFO
                             TA_oe.eq(1),
                             TA_o_n.eq(1),
                             TEA_oe.eq(1),
                             TEA_o_n.eq(1),
                             TBI_oe.eq(1),
                             TBI_o_n.eq(1),
                             #NextValue(A_latch, processed_ad),
                             NextValue(burst_counter, 0), # '040 burst are aligned
                             If(write_fifo_front.level < front_fifo_level_check, #~write_fifo_front.readable, # FIXME # the front FIFO is empty, we have enough space ; should use level instead ?
                                NextState("BurstWrite"),
                             ).Else(
                                 NextState("DelayBurstWrite"),
                             )
                      ).Elif((my_device_space & ~TS_i_n & RW_i_n), # non-burst or non-memory Read  & (~SIZ_i[0] | ~SIZ_i[1])
                             ###
                             #trace_inst_fifo.we.eq(1),
                             #trace_inst_fifo.din.eq(A_i),
                             ###
                             TA_oe.eq(1),
                             TA_o_n.eq(1),
                             TEA_oe.eq(1),
                             TEA_o_n.eq(1),
                             TBI_oe.eq(1),
                             TBI_o_n.eq(1),
                             NextValue(burst_counter, 0),
                             #NextValue(A_latch, processed_ad),
                             If(~write_fifo_back_readable_in_cpu & ~write_fifo_front.readable & ~write_fifo_burst.readable, # previous write(s) done
                                wb_read.cyc.eq(1),
                                wb_read.stb.eq(1),
                                wb_read.we.eq(0),
                                wb_read.sel.eq(0xf), # always read 32-bits for cache
                                wb_read.adr.eq(processed_ad[2:32]),
                                NextState("Read"),
                             ).Else( # TS_i_n is asserted for only 1 cycle, so need to remember
                                 NextState("DelayRead"),
                             ),
                      ).Elif((my_device_space & ~TS_i_n & ~RW_i_n), # non-burst or non-memory Write & (~SIZ_i[0] | ~SIZ_i[1])
                             ###
                             #trace_inst_fifo.we.eq(1),
                             #trace_inst_fifo.din.eq(A_i),
                             ###
                             TA_oe.eq(1),
                             TA_o_n.eq(1),
                             TEA_oe.eq(1),
                             TEA_o_n.eq(1),
                             TBI_oe.eq(1),
                             TBI_o_n.eq(1),
                             NextValue(burst_counter, 0),
                             NextState("DelayWrite"),
                      )
        )
        slave_fsm.act("DelayRead",
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      If(~write_fifo_back_readable_in_cpu & ~write_fifo_front.readable & ~write_fifo_burst.readable, # previous write(s) done
                         wb_read.cyc.eq(1),
                         wb_read.stb.eq(1),
                         wb_read.we.eq(0),
                         wb_read.sel.eq(0xf), # always read 32-bits for cache
                         wb_read.adr.eq(processed_ad[2:32]),
                         NextState("Read"),
                      )
        )
        slave_fsm.act("Read",
                      wb_read.cyc.eq(1),
                      wb_read.stb.eq(1),
                      wb_read.we.eq(0),
                      wb_read.sel.eq(0xf),
                      wb_read.adr.eq(processed_ad[2:32]),
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(1),
                      D_rev_o.eq(wb_read.dat_r),
                      If(wb_read.ack,
                         ####
                         #trace_inst_fifo.we.eq(1),
                         #trace_inst_fifo.din.eq(wb_read.dat_r),
                         ####
                         TA_o_n.eq(0), # ACK
                         If (SIZ_i == 0x3, # line
                             TBI_o_n.eq(0), # do not burst here
                         ),
                         NextValue(finishing, 1),
                         NextState("Idle"),
                      )
        )
        slave_fsm.act("DelayWrite",
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      If(write_fifo_front.writable,
                         write_fifo_front.we.eq(1), # write
                         If(SIZ_i == 0x3,
                            TBI_o_n.eq(0), # don't burst write here
                         ),
                         TA_o_n.eq(0),
                         NextValue(finishing, 1),
                         NextState("Idle"),
                      ),
        )
        slave_fsm.act("DelayBurstWrite",
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      If(write_fifo_front.level < front_fifo_level_check, #~write_fifo_front.readable, # FIXME # the front FIFO is empty, we have enough space ; should use level instead ?
                         #TA_o_n.eq(0), # accept first data
                         NextState("BurstWrite"),
                      ),
        )
        slave_fsm.act("BurstWrite",
                      TA_oe.eq(1),
                      TA_o_n.eq(0), # always TA here
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      NextValue(burst_counter, burst_counter + 1),
                      write_fifo_front.we.eq(1), # we have space
                      If(burst_counter == 0x3,
                         NextValue(finishing, 1),
                         NextState("Idle"),
                      )
        )

        slave_fsm.act("DelayFBMemBurstWrite",
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      If(write_fifo_burst.writable,
                         NextState("FBMemBurstWrite"),
                      ),
        )
        slave_fsm.act("FBMemBurstWrite",
                      TA_oe.eq(1),
                      TA_o_n.eq(0), # always TA here
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      NextValue(burst_counter, burst_counter + 1),
                      Case(burst_counter, {
                          0x0: [ NextValue(burst_buffer[ 0: 32], D_rev_i), ],
                          0x1: [ NextValue(burst_buffer[32: 64], D_rev_i), ],
                          0x2: [ NextValue(burst_buffer[64: 96], D_rev_i), ],
                          0x3: [ ], #NextValue(burst_buffer[96:128], D_rev_i), ],
                      }),
                      If(burst_counter == 0x3,
                         NextValue(finishing, 1),
                         #dram_native_r.cmd.valid.eq(1),
                         #dram_native_r.cmd.we.eq(1),
                         #dram_native_r.wdata.data.eq(Cat(burst_buffer[ 0: 96], D_rev_i)),
                         #dram_native_r.wdata.we.eq(2**len(dram_native_r.wdata.we)-1),
                         #dram_native_r.wdata.valid.eq(1),
                         write_fifo_burst.we.eq(1),
                         NextState("Idle"),
                      )
        )
        
        slave_fsm.act("DelayFBMemBurstReadWait",
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(0),
                      #dram_native_r.cmd.we.eq(0),
                      If(~write_fifo_back_readable_in_cpu & ~write_fifo_front.readable & ~write_fifo_burst.readable, # previous write(s) done
                         dram_native_r.cmd.valid.eq(1),
                         If(dram_native_r.cmd.ready, # interface available
                            NextState("FBMemBurstReadWait"),
                         ),
                      ),
        )
        slave_fsm.act("FBMemBurstReadWait",
                      TA_oe.eq(1),
                      TA_o_n.eq(1),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(1),
                      dram_native_r.rdata.ready.eq(1),
                      D_rev_o.eq(dram_native_r.rdata.data[  0: 32]),
                      If(dram_native_r.rdata.valid,
                         NextValue(burst_buffer, dram_native_r.rdata.data),
                         TA_o_n.eq(0),
                         NextValue(burst_counter, 1), 
                         NextState("FBMemBurstRead"),
                      ),
        )
        slave_fsm.act("FBMemBurstRead",
                      TA_oe.eq(1),
                      TA_o_n.eq(0),
                      TEA_oe.eq(1),
                      TEA_o_n.eq(1),
                      TBI_oe.eq(1),
                      TBI_o_n.eq(1),
                      D_oe.eq(1),
                      Case(burst_counter, {
                          0x0: D_rev_o.eq(burst_buffer[  0: 32]),
                          0x1: D_rev_o.eq(burst_buffer[ 32: 64]),
                          0x2: D_rev_o.eq(burst_buffer[ 64: 96]),
                          0x3: D_rev_o.eq(burst_buffer[ 96:128]),
                      }),
                      NextValue(burst_counter, burst_counter + 1),
                      If(burst_counter == 0x3,
                         NextValue(finishing, 1),
                         NextState("Idle"),
                      ),
        )
        
        # connect the write FIFO inputs
        # The XOR with 0xFFFFFFFF here and in the FIFO transition serves not logical purpose, other than it doesn't work without it!!!
        self.comb += [ write_fifo_front_din.data.eq(D_rev_i ^ Signal(32, reset = 0xFFFFFFFF)),
                       write_fifo_front_din.adr.eq(processed_ad + Cat(Signal(2,reset = 0), burst_counter)),
                       Case(SIZ_i, {
                           0x0: [ # long word
                              write_fifo_front_din.sel.eq(0xF),
                           ],
                           0x1: [ # byte
                               Case(processed_ad[0:2], {
                                   0x0: [
                                       write_fifo_front_din.sel.eq(0x1),
                                   ],
                                   0x1: [
                                       write_fifo_front_din.sel.eq(0x2),
                                   ],
                                   0x2: [
                                       write_fifo_front_din.sel.eq(0x4),
                                   ],
                                   0x3: [
                                       write_fifo_front_din.sel.eq(0x8),
                                   ],
                               }),
                           ],
                           0x2: [ # word
                               Case(processed_ad[1:2], {
                                   0x0: [
                                       write_fifo_front_din.sel.eq(0x3),
                                   ],
                                   0x1: [
                                       write_fifo_front_din.sel.eq(0xC),
                                   ],
                               }),
                           ],
                           0x3: [ # line
                               write_fifo_front_din.sel.eq(0xF),
                           ],
                       }),
        ]
        # deal with emptying the Write FIFO to the write WB
        self.comb += [ wb_write.cyc.eq(write_fifo_back.readable),
                       wb_write.stb.eq(write_fifo_back.readable),
                       wb_write.we.eq(1),
                       wb_write.adr.eq(write_fifo_back_dout.adr[2:32]),
                       wb_write.dat_w.eq(write_fifo_back_dout.data),
                       wb_write.sel.eq(write_fifo_back_dout.sel),
                       write_fifo_back.re.eq(wb_write.ack),
        ]

        ## BURST
        self.submodules.burst_write_fsm = burst_write_fsm = ClockDomainsRenamer(cd_cpu)(FSM(reset_state="Reset"))
        # connect the burst FIFO input
        self.comb += [
            write_fifo_burst_din.adr.eq(processed_ad),
            write_fifo_burst_din.data.eq(Cat(burst_buffer[0:96], D_rev_i)),
        ]
        # connect the memory port to the FIFO output
        self.comb += [
            dram_native_w.cmd.we.eq(1),
            dram_native_w.cmd.addr.eq(write_fifo_burst_dout.adr[4:]),
            dram_native_w.wdata.data.eq(write_fifo_burst_dout.data),
            dram_native_w.wdata.we.eq(2**len(dram_native_w.wdata.we)-1),
        ]
        # FIFO to mem port ctrl
        burst_write_fsm.act("Reset",
                            NextState("Idle")
        )
        burst_write_fsm.act("Idle",
                            If(write_fifo_burst.readable,
                               dram_native_w.cmd.valid.eq(1),
                               If(dram_native_w.cmd.ready,
                                  NextState("Data"),
                               ),
                            ),
        )
        burst_write_fsm.act("Data",
                            dram_native_w.wdata.valid.eq(1),
                            If(dram_native_w.wdata.ready,
                               write_fifo_burst.re.eq(1),
                               NextState("Idle"),
                            ),
        )
        
        


        ############## DEBUG DEBUG DEBUG

        led0 = platform.request("user_led", 0)
        led1 = platform.request("user_led", 1)
        led2 = platform.request("user_led", 2)
        led3 = platform.request("user_led", 3)
        led4 = platform.request("user_led", 4)
        led5 = platform.request("user_led", 5)
        led6 = platform.request("user_led", 6)
        led7 = platform.request("user_led", 7)
        
        self.comb += [
            led0.eq(~slave_fsm.ongoing("Idle")),
            led1.eq(0),
            led2.eq(0),
            led3.eq(0),
            led4.eq(0),
            led5.eq(0),
            led6.eq(0),
            led7.eq(0),
            #led1.eq(slave_fsm.ongoing("DelayRead")),
            #led2.eq(slave_fsm.ongoing("Read")),
            #led3.eq(slave_fsm.ongoing("DelayWrite")),
            #led4.eq(slave_fsm.ongoing("DelayBurstWrite")),
            #led5.eq(slave_fsm.ongoing("BurstWrite")),
            #led6.eq(slave_fsm.ongoing("DelayFBMemBurstWrite") | slave_fsm.ongoing("FBMemBurstWrite")),
            #led7.eq(slave_fsm.ongoing("DelayFBMemBurstReadWait") | slave_fsm.ongoing("FBMemBurstReadWait") | slave_fsm.ongoing("FBMemBurstRead")),
        ]

        # cycle time logic analyzer
        if (False):
            buffer_addr_bits = 25 # 32 MiWords or 128 MiB, 'cause we can!
            buffer_data_bits = 16 # probably overkill ?
            
            check_adr_ctr = Signal(buffer_addr_bits)
            latency = Signal(buffer_data_bits)
            read_or_write = Signal()
            
            self.submodules.write_fifo_check_latency  = write_fifo_check_latency =  ClockDomainsRenamer({"read": "sys",  "write": cd_cpu})(AsyncFIFOBuffered(width=(buffer_data_bits+1+buffer_addr_bits), depth=8))
            from litex.soc.interconnect import wishbone
            wishbone_check = wishbone.Interface(data_width=soc.bus.data_width)
            soc.bus.add_master(name="PDS040BridgeToWishbone_Check_Write", master=wishbone_check)
            # deal with emptying the Write FIFO to the write WB
            self.comb += [ wishbone_check.cyc.eq(write_fifo_check_latency.readable),
                           wishbone_check.stb.eq(write_fifo_check_latency.readable),
                           wishbone_check.we.eq(1),
                           wishbone_check.adr.eq(Signal(30, reset = 0x20000000) | write_fifo_check_latency.dout[buffer_data_bits+1:buffer_data_bits+1+buffer_addr_bits]),
                           wishbone_check.dat_w.eq(write_fifo_check_latency.dout[0:buffer_data_bits] | Cat(Signal(31, reset = 0), write_fifo_check_latency.dout[buffer_data_bits:buffer_data_bits+1])),
                           wishbone_check.sel.eq(0xF),
                           write_fifo_check_latency.re.eq(wishbone_check.ack),
                           write_fifo_check_latency.din.eq(Cat(latency, read_or_write, check_adr_ctr)),
            ]
            record = Signal()
            do_write = Signal()
            timeout = Signal(10) # so we don't overload the wishbone
            sync_cpu += [
                If(timeout,
                   timeout.eq(timeout - 1),
                ),
                If(do_write,
                   do_write.eq(0),
                ),
                If(~TS_i_n & (A_i[30:32] == 0) & (timeout == 0), # start with address in memory range
                   latency.eq(0),
                   record.eq(1),
                   read_or_write.eq(RW_i_n),
                ).Else(
                    latency.eq(latency + 1),
                ),
                If((~TA_i_n | ~TEA_i_n) & record,
                   record.eq(0),
                   do_write.eq(1),
                   check_adr_ctr.eq(check_adr_ctr + 1),
                   timeout.eq(1023),
                ),
            ]
            self.comb += [
                write_fifo_check_latency.we.eq(do_write),
                led7.eq(record),
            ]
            

        if (False and (trace_inst_fifo != None)):
            self.comb += [
                trace_inst_fifo.din.eq(Cat(A_i[24:32], A_i[16:24], A_i[8:16], A_i[0:8])),
                trace_inst_fifo.we.eq(dram_native_r.rdata.valid),
            ]
        
        if (False and (trace_inst_fifo != None)):
            self.submodules.trace_fsm_1 = trace_fsm_1 = ClockDomainsRenamer(cd_cpu)(FSM(reset_state="Reset"))
            self.submodules.trace_inst_fifo_front = trace_inst_fifo_front = ClockDomainsRenamer(cd_cpu)(SyncFIFOBuffered(width=32, depth=8))

            self.comb += [
                trace_inst_fifo_front.re.eq(trace_inst_fifo.writable),
                trace_inst_fifo.we.eq(trace_inst_fifo_front.readable),
                trace_inst_fifo.din.eq(trace_inst_fifo_front.dout),
            ]
            
            trace_fsm_1.act("Reset",
                          trace_inst_fifo_front.we.eq(0),
                          NextState("Idle")
            )
            trace_fsm_1.act("Idle",
                          trace_inst_fifo_front.we.eq(0),
                          If(~TS_i_n & (A_i[24:32] == 0xfe) & trace_inst_fifo_front.writable & ~RW_i_n,
                          #If(~TIP_CPU_i_n & (A_i[24:32] == 0xfe) & trace_inst_fifo_front.writable & ~RW_i_n,
                             trace_inst_fifo_front.we.eq(1),
                             #trace_inst_fifo_front.din[ 0: 8].eq(Cat(Signal(7, reset = 0), RW_i_n)),  #(A_i[24:32]),
                             #trace_inst_fifo_front.din[ 8:16].eq(A_i[16:24]),
                             #trace_inst_fifo_front.din[16:24].eq(A_i[ 8:16]),
                             #trace_inst_fifo_front.din[24:32].eq(A_i[ 0: 8]),
                             #trace_inst_fifo_front.din.eq(D_rev_i),
                             #NextState("Wait"),
                             #trace_inst_fifo_front.din.eq(D_i),
                             trace_inst_fifo_front.din.eq(A_i),
                             NextState("Data"),
                          )
            )
            trace_fsm_1.act("Data",
                            trace_inst_fifo_front.we.eq(1),
                            trace_inst_fifo_front.din.eq(D_rev_i),
                            NextState("Idle"),
            )
            trace_fsm_1.act("Wait",
                          trace_inst_fifo_front.we.eq(0),
                          If(TS_i_n,
                             NextState("Idle"),
                          )
            )
            
        if (False and (trace_inst_fifo != None)):
            self.submodules.trace_fsm_2 = trace_fsm_2 = ClockDomainsRenamer(cd_cpu)(FSM(reset_state="Reset"))
            
            timeout = Signal(8)
            last = Signal(32)
            
            trace_fsm_2.act("Reset",
                          trace_inst_fifo.we.eq(0),
                          NextState("Idle")
            )
            trace_fsm_2.act("Idle",
                          trace_inst_fifo.we.eq(0),
                          If(slave_fsm.ongoing("Idle"),
                             NextValue(timeout, 255),
                          ).Else(
                              NextValue(timeout, timeout - 1),
                          ),
                          If((timeout == 0) & (processed_ad != last) & trace_inst_fifo.writable,
                             NextValue(last, processed_ad),
                             trace_inst_fifo.we.eq(1),
                             trace_inst_fifo.din[ 0: 8].eq(processed_ad[24:32]),
                             trace_inst_fifo.din[ 8:16].eq(processed_ad[16:24]),
                             trace_inst_fifo.din[16:24].eq(processed_ad[ 8:16]),
                             trace_inst_fifo.din[24:32].eq(processed_ad[ 0: 8]),
                          )
            )
            
