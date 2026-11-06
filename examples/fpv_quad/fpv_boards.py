"""The 5-inch FPV quadcopter's flight controller: an STM32F405 on a 30.5 mm mount.

Taken from the quadcopter's design (its frame, motors and wiring are not part of this example); the board code is as
there. ASSUMED pinouts are from the makers' datasheets as remembered: verify before fab.

    python fpv_boards.py [out_dir]      # writes out/fpv_fc
"""
import os
import sys

sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src")]
from build123d import *
from pcb import Board, route, cached_route, route_jobs, repair
from vendor_parts import kicad

F4 = {"VBAT": "1", "PC14": "3", "PC15": "4", "OSC_IN": "5", "OSC_OUT": "6", "NRST": "7", "PC2": "10", "VSSA": "12",
      "VDDA": "13", "PA2": "16", "PA3": "17", "VSS1": "18", "VDD1": "19", "PA4": "20", "PA5": "21", "PA6": "22",
      "PA7": "23", "PC4": "24", "PB0": "26", "PB1": "27", "VCAP1": "31", "VDD2": "32", "PC6": "37", "PC7": "38",
      "PA9": "42", "PA10": "43", "PA11": "44", "PA12": "45", "VCAP2": "47", "VDD3": "48", "PA15": "50", "PC10": "51",
      "PC11": "52", "PC12": "53", "BOOT0": "60", "VSS2": "63", "VDD4": "64"}

def fc_board():
    b = Board("fc", [(-18.0, -18.0), (18.0, -18.0), (18.0, 18.0), (-18.0, 18.0)], thickness=1.6, copper_oz=1.0,
              clearance=0.15, max_rise_c=20.0, corner_r=3.0)
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.hole((sx * 15.25, sy * 15.25), 4.0, keepout=3.2)
    C04, c04m = "Capacitor_SMD:C_0402_1005Metric", "Capacitor_SMD.3dshapes/C_0402_1005Metric.step"
    R04, r04m = "Resistor_SMD:R_0402_1005Metric", "Resistor_SMD.3dshapes/R_0402_1005Metric.step"
    C06, c06m = "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD.3dshapes/C_0603_1608Metric.step"
    P = "Package_TO_SOT_SMD.3dshapes/"
    # MCU, gyro, crystal, flash
    b.place("U1", "Package_QFP:LQFP-64_10x10mm_P0.5mm", (0.0, 0.0), 180, "STM32F405RGT6", "ST STM32F405RGT6",
            model="Package_QFP.3dshapes/LQFP-64_10x10mm_P0.5mm.step", symbol="MCU_ST_STM32F4:STM32F405RGTx")
    b.place("U2", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", (1.5, 10.75), 180, "ICM-42688-P", "TDK ICM-42688-P",
            model="Package_LGA.3dshapes/LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y.step", side="B", symbol="Prometheus:ICM-42688-P")
    b.place("C20", C04, (-1.5, 11.5), 90, "100n", "X7R 0402", model=c04m, side="B")
    b.place("C21", C04, (4.0, 12.85), 0, "100n", "X7R 0402", model=c04m, side="B")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", (9.5, -1.5), 90, "8 MHz", "Abracon ABM8-8.000MHZ-B2-T",
            model="Crystal.3dshapes/Crystal_SMD_3225-4Pin_3.2x2.5mm.step")
    b.place("C22", C04, (9.5, 1.25), 0, "12p", "C0G 0402", model=c04m)
    b.place("C23", C04, (9.5, -4.25), 0, "12p", "C0G 0402", model=c04m)
    b.place("U3", "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm", (4.5, -11.0), 90, "W25Q128JV", "Winbond W25Q128JVSIQ",
            model="Package_SO.3dshapes/SOIC-8_5.3x5.3mm_P1.27mm.step", side="B", note="blackbox flash", symbol="Memory_Flash:W25Q128JVS")
    b.place("C24", C04, (8.25, -11.0), 90, "100n", "X7R 0402", model=c04m, side="B")
    # MCU decoupling under it (bottom), VCAP, NRST, BOOT0
    for ref, at, rot_, val in (("C1", (2.75, 4.2), 90, "100n"), ("C2", (-4.0, 4.2), 90, "100n"), ("C3", (-4.2, -3.75), 0, "100n"),
                               ("C4", (3.25, -4.2), 90, "100n"), ("C5", (4.2, 2.25), 0, "1u"), ("C6", (-2.5, 4.2), 90, "2u2"),
                               ("C7", (-4.2, -2.25), 0, "2u2"), ("C8", (4.75, -2.75), 0, "100n")):
        b.place(ref, C04, at, rot_, val, "X7R 0402", model=c04m, side="B")
    b.place("R1", R04, (0.0, -7.6), 0, "10k", "RC0402FR-0710KL", model=r04m)           # BOOT0 pull-down
    b.place("SW1", "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2", (4.5, -13.5), 0, "BOOT", "C&K KMR211NGLFS",
            model="Button_Switch_SMD.3dshapes/SW_Push_1P1T_NO_CK_KMR2.step")
    # status LEDs
    b.place("D1", "LED_SMD:LED_0603_1608Metric", (9.0, -8.5), 0, "blue", "Kingbright APT1608QBC/D",
            model="LED_SMD.3dshapes/LED_0603_1608Metric.step")
    b.place("D2", "LED_SMD:LED_0603_1608Metric", (9.0, -10.5), 0, "green", "Kingbright APT1608SGC",
            model="LED_SMD.3dshapes/LED_0603_1608Metric.step")
    b.place("R2", R04, (11.5, -8.5), 0, "1k", "RC0402FR-071KL", model=r04m)
    b.place("R3", R04, (11.5, -10.5), 0, "1k", "RC0402FR-071KL", model=r04m)
    # battery sense divider to PC2
    b.place("R4", R04, (12.75, -6.6), 0, "10k", "RC0402FR-0710KL", model=r04m)
    b.place("R5", R04, (12.75, -6.6), 0, "1k", "RC0402FR-071KL", model=r04m, side="B")
    # power: VBAT (from the ESC) -> 5 V buck (bottom) -> 3.3 V LDO; USB VBUS -> 5 V through a Schottky
    b.place("U4", "Package_TO_SOT_SMD:SOT-23-6", (8.5, 7.75), 90, "LMR51420", "TI LMR51420YDDCR",
            model=P + "SOT-23-6.step", side="B", symbol="Prometheus:LMR51420")
    b.place("L1", "Inductor_SMD:L_Coilcraft_XAL4020-XXX", (8.0, 12.5), 0, "6u8", "Coilcraft XAL4020-682",
            model="Inductor_SMD.3dshapes/L_Coilcraft_XAL4020-XXX.step", side="B")
    b.place("C9", "Capacitor_SMD:C_1206_3216Metric", (11.75, 8.75), 90, "4u7 50V", "GRM31CR71H475KA12L",
            model="Capacitor_SMD.3dshapes/C_1206_3216Metric.step", side="B")
    b.place("C10", C04, (6.25, 8.75), 90, "100n", "X7R 0402", model=c04m, side="B")
    b.place("C11", "Capacitor_SMD:C_0805_2012Metric", (6.5, 16.0), 0, "22u 10V", "GRM21BR61A226ME44L",
            model="Capacitor_SMD.3dshapes/C_0805_2012Metric.step", side="B")
    b.place("R6", R04, (5.5, 6.0), 0, "100k", "RC0402FR-07100KL", model=r04m, side="B")
    b.place("R7", R04, (5.5, 4.75), 0, "13k7", "RC0402FR-0713K7L", model=r04m, side="B")
    b.place("U5", "Package_TO_SOT_SMD:SOT-23-5", (-6.5, 11.0), 0, "AP2112K-3.3", "Diodes AP2112K-3.3TRG1",
            model=P + "SOT-23-5.step")
    b.place("C12", C06, (-9.5, 11.0), 90, "1u", "X7R 0603", model=c06m)
    b.place("C13", C06, (-3.5, 11.0), 90, "1u", "X7R 0603", model=c06m)
    b.place("D3", "Diode_SMD:D_SOD-323", (-6.0, -8.25), 0, "BAT60A", "Infineon BAT60A", model="Diode_SMD.3dshapes/D_SOD-323.step", side="B")
    # USB-C (data + power for setup), on the right-hand edge
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (-6.0, -14.6), 0, "USB-C",
            "GCT USB4105-GF-A", model="Connector_USB.3dshapes/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step",
            overhang=True)
    b.place("R8", R04, (-10.25, -9.25), 0, "5k1", "RC0402FR-075K1L", model=r04m)
    b.place("R9", R04, (0.25, -9.0), 0, "5k1", "RC0402FR-075K1L", model=r04m)
    # connectors: ESC (bottom, facing the 4-in-1 below), receiver and VTX
    b.place("J2", "Connector_JST:JST_SH_SM08B-SRSS-TB_1x08-1MP_P1.00mm_Horizontal", (14.6, 0.0), 90, "SH 8p",
            "JST SM08B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_SM08B-SRSS-TB_1x08-1MP_P1.00mm_Horizontal.step",
            note="to the 4-in-1 ESC (mouth forward, the harness loops down to the ESC)")
    b.place("J3", "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", (-14.7, -6.5), 270, "SH 4p",
            "JST SM04B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal.step",
            note="ELRS receiver")
    b.place("J4", "Connector_JST:JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal", (-14.6, 6.0), 270, "SH 6p",
            "JST SM06B-SRSS-TB", model="Connector_JST.3dshapes/JST_SH_SM06B-SRSS-TB_1x06-1MP_P1.00mm_Horizontal.step",
            note="HD VTX (power, MSP), mouth rearward")
    return b

def fc_nets(b):
    U = "U1"
    n = lambda name, pins, amps=0.01, volts=3.3: b.net(name, pins, amps=amps, volts=volts)
    n("GND", [(U, F4["VSS1"]), (U, F4["VSS2"]), (U, F4["VSSA"]), ("U2", "6"), ("C20", "2"), ("C21", "2"), ("C22", "2"),
              ("C23", "2"), ("U3", "4"), ("C24", "2")] + [(c, "2") for c in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")]
      + [("R1", "2"), ("R5", "2"), ("U4", "1"), ("C9", "2"), ("C11", "2"), ("R7", "2"), ("U5", "2"), ("C12", "2"),
         ("C13", "2"), ("J1", "A1"), ("J1", "B12"), ("J1", "A12"), ("J1", "B1"), ("R8", "2"), ("R9", "2"), ("J2", "1"),
         ("J3", "1"), ("J4", "2"), ("J4", "5"), ("D1", "1"), ("D2", "1")], amps=1.5, volts=25.0)
    n("3V3", [(U, F4["VBAT"]), (U, F4["VDD1"]), (U, F4["VDD2"]), (U, F4["VDD3"]), (U, F4["VDD4"]), (U, F4["VDDA"]),
              ("U2", "5"), ("U2", "8"), ("C20", "1"), ("C21", "1"), ("U3", "8"), ("U3", "3"), ("U3", "7"), ("C24", "1"),
              ("C1", "1"), ("C2", "1"), ("C3", "1"), ("C4", "1"), ("C5", "1"), ("C8", "1"), ("U5", "5"), ("C13", "1"),
              ("SW1", "1"), ("R3", "1")], amps=0.3)                # (D2 through R3: the 3.3 V power LED)
    n("VBAT", [("J2", "2"), ("U4", "3"), ("U4", "5"), ("C9", "1"), ("R4", "1"), ("J4", "1")], amps=1.5, volts=25.2)
    n("5V", [("L1", "2"), ("C11", "1"), ("R6", "1"), ("U5", "1"), ("U5", "3"), ("C12", "1"), ("J3", "2"), ("D3", "1")],
      amps=1.5, volts=5.0)
    n("VBUS", [("J1", "A4"), ("J1", "B9"), ("J1", "B4"), ("J1", "A9"), ("D3", "2")], amps=0.5, volts=5.0)
    n("SW", [("U4", "2"), ("L1", "1"), ("C10", "2")], amps=1.5, volts=25.2)
    n("CB", [("U4", "6"), ("C10", "1")])
    n("FB", [("U4", "4"), ("R6", "2"), ("R7", "1")])
    n("VCAP1", [(U, F4["VCAP1"]), ("C6", "1")])
    n("VCAP2", [(U, F4["VCAP2"]), ("C7", "1")])
    n("BOOT0", [(U, F4["BOOT0"]), ("R1", "1"), ("SW1", "2")])
    n("OSC_IN", [(U, F4["OSC_IN"]), ("Y1", "1"), ("C22", "1")])
    n("OSC_OUT", [(U, F4["OSC_OUT"]), ("Y1", "3"), ("C23", "1")])
    n("GYRO_SCK", [(U, F4["PA5"]), ("U2", "13")])
    n("GYRO_MISO", [(U, F4["PA6"]), ("U2", "1")])
    n("GYRO_MOSI", [(U, F4["PA7"]), ("U2", "14")])
    n("GYRO_CS", [(U, F4["PA4"]), ("U2", "12")])
    n("GYRO_INT", [(U, F4["PC4"]), ("U2", "4")])
    n("FL_SCK", [(U, F4["PC10"]), ("U3", "6")])
    n("FL_MISO", [(U, F4["PC11"]), ("U3", "2")])
    n("FL_MOSI", [(U, F4["PC12"]), ("U3", "5")])
    n("FL_CS", [(U, F4["PA15"]), ("U3", "1")])
    n("USB_DM", [(U, F4["PA11"]), ("J1", "A7"), ("J1", "B7")])
    n("USB_DP", [(U, F4["PA12"]), ("J1", "A6"), ("J1", "B6")])
    n("CC1", [("J1", "A5"), ("R8", "1")])
    n("CC2", [("J1", "B5"), ("R9", "1")])
    n("VSENSE", [(U, F4["PC2"]), ("R4", "2"), ("R5", "1")])
    n("LED1", [(U, F4["PC14"]), ("R2", "1")])
    n("LED1K", [("R2", "2"), ("D1", "2")])
    n("LED2K", [("R3", "2"), ("D2", "2")])
    n("M1", [(U, F4["PB0"]), ("J2", "5")])
    n("M2", [(U, F4["PB1"]), ("J2", "6")])
    n("M3", [(U, F4["PA3"]), ("J2", "7")])
    n("M4", [(U, F4["PA2"]), ("J2", "8")])
    n("RX_TX", [(U, F4["PA9"]), ("J3", "4")])        # UART1: the ELRS receiver (CRSF)
    n("RX_RX", [(U, F4["PA10"]), ("J3", "3")])
    n("VTX_TX", [(U, F4["PC6"]), ("J4", "4")])       # UART6: the HD VTX (MSP DisplayPort OSD)
    n("VTX_RX", [(U, F4["PC7"]), ("J4", "3")])

def fc_route(b):
    probs = []
    b.corridor = 0.4
    b.fanout(width=0.1)
    b.via("GND", (10.4, 1.25), 0.3, 0.6)                      # the crystal's load caps to the ground plane
    b.via("GND", (10.4, -4.25), 0.3, 0.6)
    order = ["GYRO_SCK", "GYRO_MISO", "GYRO_MOSI", "GYRO_CS", "GYRO_INT", "M1", "M2", "M3", "M4", "OSC_IN", "OSC_OUT", "USB_DM", "USB_DP",
             "FL_SCK", "FL_MISO", "FL_MOSI", "FL_CS", "VCAP1", "VCAP2", "RX_TX", "RX_RX",
             "VTX_TX", "VTX_RX", "VSENSE", "LED1", "LED1K", "LED2K", "BOOT0", "CC1", "CC2", "CB", "FB"]
    # negotiated passes (pcb.route_jobs): a net that fails is routed first on the next pass, the best pass kept
    jobs = [(nm, 0.1, None) for nm in order[:9]] + [("3V3", 0.2, None)] + [(nm, 0.1, None) for nm in order[9:]] + \
        [("SW", 0.5, None), ("VBUS", 0.3, None),
         ("VBAT", 0.4, [p for p in b.nets["VBAT"]["pins"][1:] if p[0] != "R4"]),
         ("VBAT", 0.2, [("R4", "1")], {"amps": 0.01}),               # (the battery sense divider: no current)
         ("5V", 0.4, None), ("GND", 0.2, None, {"amps": 0.3})]
    probs += route_jobs(b, jobs, passes=4)
    b.stitch("GND", [(-17.5, -17.5), (17.5, -17.5), (17.5, 17.5), (-17.5, 17.5)], pitch=1.5)
    return probs

def fc_build():
    b = fc_board()
    fc_nets(b)
    b.pour("GND", "B", [(-18.5, -18.5), (18.5, -18.5), (18.5, 18.5), (-18.5, 18.5)], amps=1.5)
    b.pour("GND", "F", [(-18.5, -18.5), (18.5, -18.5), (18.5, 18.5), (-18.5, 18.5)], amps=1.5)
    rp = fc_route(b)
    b.tidy()
    return b, rp



if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from export import export
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    b, probs = fc_build()
    export(b, probs, os.path.join(base, "fpv_fc"))
