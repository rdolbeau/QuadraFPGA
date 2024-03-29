# A FPGA on a Quadra PDS card...

## Goal

The goal of this repository is to be able to interface a modern (2023 era) [FPGA](https://en.wikipedia.org/wiki/Field-programmable_gate_array) with a [Macintosh Quadra](https://en.wikipedia.org/wiki/Macintosh_Quadra) using its PDS (Processor Direct Slot) connector. The PDS is basically just a physical connector to the MC68040 memory bus.

So unless you're a retrocomputing enthusiast with such a machine, this is useless. If you are such an enthusiast, then maybe the ability to connect a modern LCD monitor using a digital interface to an old Macintosh might be of interest to you.

This project was 'spun off' the [NuBusFPGA](https://github.com/rdolbeau/NuBusFPGA) and [IIsiFPGA](https://github.com/rdolbeau/IIsiFPGA), similar project for the NuBus used in several models of Macintosh of the era and the PDS in the Macintosh IIsi. NuBusFPGA itself is a spin-off of the [SBusFPGA](https://github.com/rdolbeau/SBusFPGA), a similar project for the SBus used in Sun's SPARCstation.

## Current status

The HDMI (Highly Desirable Macintosh Interface) is working, with both video (single-hardware-res, multi-depth, windowboxed lower res) & audio working. More details are available in the readme of the NuBusFPGA repository.
