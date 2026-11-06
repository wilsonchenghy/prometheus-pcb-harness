"""PAM8403 stereo class-D amplifier: 2 x 3 W into 4 ohm from 5 V. USB-C power in (5 V sink, 5.1k CC pull-downs),
3.5 mm stereo jack in (CUI SJ1-3523N), a dual 10k log pot (Alps RK097 dual, shaft through the top edge) as the
volume control, 0.47 uF input coupling caps, the PAM8403 (SOP-16), its 1 uF VDD and VREF caps and 10 uF + 22 uF
on PVDD, two JST-PH 2-pin speaker outputs (filterless: the speaker leads short), a power LED, a mute/standby slide
switch (MUTE low = muted). Difficulty 3/5. 2 layers, 50 x 40 mm, JLCPCB.

The PAM8403 symbol (Prometheus:PAM8403, symbols/ beside this script) is made from the Diodes PAM8403 datasheet's
pin descriptions (page 2), checked against it. Speaker nets 1.5 A (3 W into 4 ohm: 0.87 A rms, 1.2 A peak).
"""
from pcb import Board

R06, C06, C08 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
C12 = "Capacitor_SMD:C_1206_3216Metric"
PH2 = "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"
DS = "https://www.diodes.com/assets/Datasheets/PAM8403.pdf"
W, H = 50.0, 40.0
OUT = [(0, 0), (W, 0), (W, H), (0, H)]
FIXED = {"C3": ((23.1, 22.3), 90), "C4": ((20.5, 22.3), 90), "C5": ((25.6, 22.5), 90), "C8": ((25.6, 11.5), 90)}   # by their pins


def build():
    b = Board("pam8403_stereo_amp", OUT, thickness=1.6, copper_oz=1.0, rules="jlcpcb-2", corner_r=2.0)
    b.place("J1", "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", (W - 3.2, 8.0), 90,
            "USB-C 5V", "GCT USB4105-GF-A", overhang=True, symbol="Connector:USB_C_Receptacle_USB2.0_16P")
    b.place("J2", "Connector_Audio:Jack_3.5mm_CUI_SJ1-3523N_Horizontal", (6.5, 20.0), 270, "LINE IN",
            "CUI SJ1-3523N", overhang=True)
    b.place("RV1", "Potentiometer_THT:Potentiometer_Alps_RK097_Dual_Horizontal", (12.0, 32.0), 270, "10k log dual",
            "Alps RK09712200MC", overhang=True,
            symbol="Device:R_Potentiometer_Dual")
    b.place("U1", None, (25.0, 17.0), 270, "PAM8403", "Diodes PAM8403DR", symbol="Prometheus:PAM8403", lcsc="C112426")
    b.place("J3", PH2, (W - 7.0, H - 2.8), 0, "SPK L", "JST B2B-PH-K-S")
    b.place("J4", PH2, (W - 16.0, H - 2.8), 0, "SPK R", "JST B2B-PH-K-S")
    b.place("SW1", "Button_Switch_SMD:SW_SPDT_PCM12", (24.0, 2.6), 180, "PLAY/MUTE", "C&K PCM12SMTR", overhang=True)
    for ref, fp, val, note in (("C1", C06, "470n", "L in"), ("C2", C06, "470n", "R in"), ("C3", C06, "1u", "VDD"),
                               ("C4", C06, "1u", "VREF"), ("C5", C08, "10u", "PVDD"), ("C6", C12, "22u", "bulk"),
                               ("C8", C08, "10u", "PVDD (pin 13)"),
                               ("R1", R06, "5.1k", "CC1"), ("R2", R06, "5.1k", "CC2"), ("R3", R06, "1k", "PWR LED"),
                               ("R4", R06, "10k", "MUTE pull-up"), ("R5", R06, "10k", "SHDN pull-up")):
        b.place(ref, fp, *FIXED.get(ref, (None, 0)), val, note=note)
    b.place("D1", "LED_SMD:LED_0603_1608Metric", None, 0, "green")
    b.cite("C3", "VDD decoupling", "1u", 8, "placing it as close as possible to the device VDD terminal", url=DS)
    b.cite("C6", "bulk", "22u", 8, "or greater is recommended", url=DS)
    b.cite("C4", "VREF bypass", "1u", 8, "is recommended for the best THD and noise performance", url=DS)
    b.hole((3.5, 3.5), d=3.2)
    b.hole((W - 3.5, H - 3.5 - 14.0), d=3.2)

    u = lambda n: ("U1", n)
    b.net("VBUS", [("J1", p) for p in ("A4", "A9", "B4", "B9")] +
          [u("PVDD"), u("VDD"), ("C3", "1"), ("C5", "1"), ("C6", "1"), ("C8", "1"), ("R3", "1"), ("R4", "1"),
           ("R5", "1"), ("SW1", "1")], amps=1.5, volts=5.0)
    b.net("GND", [("J1", p) for p in ("A1", "A12", "B1", "B12", "SH")] +
          [u("PGND"), u("GND"), ("C3", "2"), ("C4", "2"), ("C5", "2"), ("C6", "2"), ("C8", "2"), ("D1", "1"),
           ("R1", "2"), ("R2", "2"), ("J2", "S"), ("RV1", "1"), ("RV1", "4"), ("SW1", "3")], amps=1.5)
    b.net("CC1", [("J1", "A5"), ("R1", "1")])
    b.net("CC2", [("J1", "B5"), ("R2", "1")])
    b.net("LED", [("R3", "2"), ("D1", "2")])
    b.net("AINL", [("J2", "T"), ("RV1", "3")])
    b.net("AINR", [("J2", "R"), ("RV1", "6")])
    b.net("WL", [("RV1", "2"), ("C1", "1")])
    b.net("WR", [("RV1", "5"), ("C2", "1")])
    b.net("INL", [("C1", "2"), u("INL")])
    b.net("INR", [("C2", "2"), u("INR")])
    b.net("VREF", [u("VREF"), ("C4", "1")])
    b.net("MUTE", [u("~{MUTE}"), ("R4", "2"), ("SW1", "2")])     # switch to GND mutes
    b.net("SHDN", [u("~{SHDN}"), ("R5", "2")])
    b.net("LP", [u("+OUT_L"), ("J3", "1")], amps=1.5)
    b.net("LN", [u("-OUT_L"), ("J3", "2")], amps=1.5)
    b.net("RP", [u("+OUT_R"), ("J4", "1")], amps=1.5)
    b.net("RN", [u("-OUT_R"), ("J4", "2")], amps=1.5)
    b.nc("J1", "SBU1", "SBU2", "D+", "D-")
    b.nc("U1", "NC")
    b.pour("GND", "B", OUT)
    # VBUS off the receptacle by hand: 0.52 mm from each VBUS pin pair to a 0.8 mm bar (the router would neck the
    # whole feed down to fit between the 0.5 mm pins)
    (xa, ya), (xb, yb) = b.pad("J1", "A4"), b.pad("J1", "A9")
    for y in (ya, yb):
        b.track("VBUS", "F", [(xa, y), (xa - 2.6, y)], 0.52)
    b.track("VBUS", "F", [(xa - 2.6, ya), (xa - 2.6, yb)], 0.8)
    return b
