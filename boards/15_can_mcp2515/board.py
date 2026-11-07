"""CAN bus node: MCP2515 stand-alone CAN controller (SPI, SOIC-18W) + NXP TJA1051T/3 high-speed CAN transceiver
(SOIC-8, separate VIO), 8 MHz crystal with 22 pF load caps, 120 ohm bus termination enabled by a 2-pin jumper JP1
(fit the shunt only at the two ends of the bus), a 3-pin 5.08 mm screw terminal J1 and a 1x08 2.54 mm host header J2.
Difficulty: 3/5. 2 layers, 1 oz, 44 x 26 mm, JLCPCB, GND poured on both sides.

Host header J2 (pin 1 at the top, order chosen to follow the MCP2515's own pin order so the SPI lines do not cross):
1 INT (active low), 2 SCK, 3 MOSI (MCP2515 SI), 4 MISO (SO), 5 CS (active low), 6 VIO, 7 GND, 8 5V.
Screw terminal J1 (pin 1 at the bottom): 1 GND, 2 CANL, 3 CANH (order follows the TJA1051's CANL / CANH pins).

3.3 V / 5 V: the TJA1051's bus side (VCC) needs 4.5 - 5.5 V, so 5 V must always be supplied on J2.8. VIO (J2.6)
is the host's logic level, 2.8 - 5.5 V (TJA1051 VIO range): it powers the MCP2515 (2.7 - 5.5 V) and the
transceiver's TXD / RXD level adapter, so a 3.3 V MCU connects directly with VIO = 3.3 V, a 5 V MCU with VIO = 5 V.

Choices and assumptions:
- Pin maps from KiCad's Interface_CAN_LIN:MCP2515-xSO and :TJA1051T-3 symbols, checked against the datasheets'
  pin tables (MCP2515 DS20001801J table 1-1 p.4; TJA1051 rev 9 table 3 p.4).
- 8 MHz crystal (YXC X322508MSB4SI, 3225, 20 pF CL) with 22 pF C0G load caps: Microchip's tested value for 8 MHz
  (DS20001801J table 8-2, p.56, cited). 8 MHz gives 500 kbit/s at 8 TQ per bit; fit a 16 MHz crystal for 1 Mbit/s.
- TJA1051 S (silent) tied to GND: always normal mode (S has an internal pull-down, p.6). TXnRTS inputs left open:
  100 k internal pull-ups (p.4). RX0BF / RX1BF / CLKOUT unused.
- RESET: 10 k pull-up to VIO (no RC: the host can also reset over SPI).
- Decoupling: 100 nF at MCP2515 VDD, 100 nF at TJA1051 VCC and VIO (NXP's test circuit shows 100 nF at VCC, p.13),
  10 uF bulk on 5 V (the test circuit's 47 uF is the regulator's; a board fed from a host needs less). Values are
  usual practice, not a datasheet requirement.
- CANH / CANL are a length-matched pair (b.pair, req.json pairs, 2 mm: at 1 Mbit/s the bit is 1 us, skew of mm
  is irrelevant electrically; matched for symmetry / EMC).
- Y1 has no LCSC number: the run's search suggests C57131 (TXC 1C208000BC0R, 8 MHz 3225): check its load
  capacitance (the 22 pF caps assume ~18-20 pF CL) and add lcsc= if confirmed.
- No bus TVS or common-mode choke fitted (a bench node); add a PESD2CAN + choke for a vehicle.

    prometheus boards/15_can_mcp2515/board.py out/ --spec boards/15_can_mcp2515/req.json
"""
from pcb import Board

R06, R08, C06, C08 = ("Resistor_SMD:R_0603_1608Metric", "Resistor_SMD:R_0805_2012Metric",
                      "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric")
TB3 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal"
DS_MCP = "https://ww1.microchip.com/downloads/en/DeviceDoc/MCP2515-Stand-Alone-CAN-Controller-with-SPI-20001801J.pdf"
DS_TJA = "https://www.nxp.com/docs/en/data-sheet/TJA1051.pdf"
W, H = 44.0, 26.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
FIXED = {"C1": ((11.0, 5.4), 0),               # 100n by U1.18 (VDD)
         "C4": ((25.2, 3.6), 0),               # 100n by U2.3 (VCC)
         "C5": ((31.0, 2.9), 0)}               # 100n by U2.5 (VIO)


def build():
    b = Board("can_mcp2515", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=1.0)
    b.place("J1", TB3, (W - 5.2, 4.0), 90, "GND CANL CANH", "Phoenix 1729131")
    b.place("J2", "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical", (2.5, 22.0), 0,
            "INT SCK MOSI MISO CS VIO GND 5V", "XFCN PZ254V-11-08P", lcsc="C2829863")
    b.place("U1", None, (15.0, 13.0), 180, "MCP2515", "Microchip MCP2515-I/SO", symbol="Interface_CAN_LIN:MCP2515-xSO",
            lcsc="C12368")
    b.place("U2", None, (29.0, 7.0), 0, "TJA1051T/3", "NXP TJA1051T/3,118", symbol="Interface_CAN_LIN:TJA1051T-3")
    b.place("Y1", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", (24.0, 17.0), 0, "8MHz", "YXC X322508MSB4SI",
            symbol="Device:Crystal_GND24")
    b.place("JP1", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (33.0, 22.5), 90, "TERM",
            "XFCN PZ254V-11-02P", note="fit the shunt for 120R termination", lcsc="C492401")
    for ref, fp, val, note in (("C1", C06, "100n", "MCP2515 VDD"), ("C2", C06, "22p", "OSC1 load"),
                               ("C3", C06, "22p", "OSC2 load"), ("C4", C06, "100n", "TJA1051 VCC"),
                               ("C5", C06, "100n", "TJA1051 VIO"), ("C6", C08, "10u", "5V bulk"),
                               ("R1", R08, "120R", "CAN termination"), ("R2", R06, "10k", "RESET pull-up")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    for c in ("C2", "C3"):
        b.cite(c, "crystal load capacitor (8 MHz)", "22p", 56, "8 MHz          22 pF         22 pF", url=DS_MCP)
    b.cite("C4", "VCC decoupling", "100n", 13, "100 nF", url=DS_TJA)
    b.cite("U2", "S pin left low = normal mode", "GND", 6, "pins S and EN (TJA1051T/E) have internal", url=DS_TJA)

    u, t = (lambda n: ("U1", n)), (lambda n: ("U2", n))
    b.net("5V", [("J2", "8"), t("VCC"), ("C4", "1"), ("C6", "1")], amps=0.1, volts=5.0)
    b.net("VIO", [("J2", "6"), u("VDD"), t("VIO"), ("C1", "1"), ("C5", "1"), ("R2", "1")], amps=0.05, volts=5.0)
    b.net("GND", [("J2", "7"), ("J1", "1"), u("VSS"), t("GND"), t("S"), ("C1", "2"), ("C2", "2"), ("C3", "2"),
                  ("C4", "2"), ("C5", "2"), ("C6", "2"), ("Y1", "2"), ("Y1", "4")], amps=0.1)
    b.net("INT", [u("nINT"), ("J2", "1")])
    b.net("SCK", [u("SCK"), ("J2", "2")])
    b.net("MOSI", [u("SI"), ("J2", "3")])
    b.net("MISO", [u("SO"), ("J2", "4")])
    b.net("CS", [u("nCS"), ("J2", "5")])
    b.net("RESET", [u("nRESET"), ("R2", "2")])
    b.net("OSC1", [u("OSC1"), ("Y1", "1"), ("C2", "1")])
    b.net("OSC2", [u("OSC2"), ("Y1", "3"), ("C3", "1")])
    b.net("TXCAN", [u("TXCAN"), t("TXD")])
    b.net("RXCAN", [u("RXCAN"), t("RXD")])
    b.net("CANH", [t("CANH"), ("J1", "3"), ("R1", "1")], volts=12.0)
    b.net("CANL", [t("CANL"), ("J1", "2"), ("JP1", "2")], volts=12.0)
    b.net("TERM", [("R1", "2"), ("JP1", "1")], volts=12.0)
    b.pair("CANH", "CANL", max_skew=2.0)
    b.nc("U1", "nTX0RTS", "nTX1RTS", "nTX2RTS", "nRX0BF", "nRX1BF", "CLKOUT/SOF")
    b.pour("GND", "B", OUT)
    b.pour("GND", "F", OUT)
    return b
