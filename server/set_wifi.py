# Part of Chitti by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
"""Give Chitti your Wi-Fi name and password over the USB cable (no phone needed).

Run:  .venv\\Scripts\\python.exe set_wifi.py                (finds Chitti's USB port by itself)
      .venv\\Scripts\\python.exe set_wifi.py --port COM5    (or pick the port)
The password is typed here, hidden, and only sent to the board through the cable.
"""
import getpass
import json
import subprocess
import sys
import time

import serial
from serial.tools import list_ports


def arg(name: str) -> str | None:
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    return None


def find_port() -> str:
    if arg("--port"):
        return arg("--port")
    for p in list_ports.comports():
        if p.vid == 0x303A:  # Espressif USB = Chitti's board
            return p.device
    others = [p.device for p in list_ports.comports()]
    print("Chitti's board wasn't found on USB. Plug it in with a USB-C DATA cable (some cables only charge).")
    if others:
        print("Other USB ports on this PC:", ", ".join(others))
        typed = input("Type the board's port (e.g. COM5), or press Enter to quit: ").strip()
        if typed:
            return typed
    sys.exit(1)


def nearby_networks() -> list[str]:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "networks"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = [line.split(":", 1)[1].strip() for line in out.splitlines() if line.strip().startswith("SSID")]
    return [n for n in names if n]


def open_port(name: str) -> serial.Serial:
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = name, 115200, 0.2
    s.dtr = False  # don't reset the board when the port opens
    s.rts = False
    s.open()
    return s


def main():
    port = find_port()
    print(f"Chitti board on {port}")
    ssid = arg("--ssid")
    if ssid:
        print(f"Wi-Fi name: {ssid}")
    else:
        nets = nearby_networks()
        if nets:
            print("Wi-Fi networks this PC can see:")
            for i, n in enumerate(nets, 1):
                print(f"  {i}. {n}")
        choice = input("Wi-Fi name (or its number from the list): ").strip()
        if choice.isdigit() and 0 < int(choice) <= len(nets):
            ssid = nets[int(choice) - 1]
        else:
            ssid = choice
    if not ssid:
        sys.exit("No Wi-Fi name given.")
    if ssid == "Chitti-Setup":
        sys.exit("'Chitti-Setup' is Chitti's own hotspot. Use your home Wi-Fi name instead.")
    password = getpass.getpass("Wi-Fi password (hidden, press Enter if none): ")

    with open_port(port) as s:
        s.write(b"\nCHITTI-PING\n")
        if b"CHITTI-PONG" not in s.read(4096) + s.read(4096):
            print("Board did not answer yet, sending anyway...")
        line = "CHITTI-WIFI " + json.dumps({"ssid": ssid, "pass": password, "server": ""}) + "\n"
        s.write(line.encode())
        deadline = time.time() + 5
        buf = b""
        while time.time() < deadline:
            buf += s.read(4096)
            if b"CHITTI-WIFI-OK" in buf:
                print(f"Saved! Chitti is restarting and joining '{ssid}'.")
                return
            if b"CHITTI-WIFI-ERROR" in buf:
                sys.exit("Chitti rejected the settings (name too long or empty).")
    sys.exit("No answer from Chitti. Is the USB cable connected and the Chitti firmware running?")


if __name__ == "__main__":
    main()
