"""Find an enumerated serial port carrying mouse packets or HC-06 HELLO messages."""

import argparse
import time

import serial
from serial.tools import list_ports


def is_device_line(raw):
    try:
        line = raw.decode("ascii").strip()
        if line.startswith("HELLO:"):
            return int(line[6:]) >= 0
        dx, dy, left, right = map(int, line.split(","))
        return (-32768 <= dx <= 32767 and -32768 <= dy <= 32767
                and left in (0, 1) and right in (0, 1))
    except (UnicodeDecodeError, ValueError):
        return False


def scan_port(port, baud, seconds):
    with serial.Serial(port, baud, timeout=0.25) as connection:
        deadline = time.monotonic() + seconds
        pending = b""
        while time.monotonic() < deadline:
            pending += connection.read(min(256, max(1, connection.in_waiting)))
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if len(line) <= 128 and is_device_line(line):
                    print(f"[+] {port}: {line!r}")
                    return True
            if len(pending) > 128:
                return False
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Check only this port, e.g. COM11")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud (default 115200)")
    parser.add_argument("--seconds", type=float, default=6)
    args = parser.parse_args(argv)
    if args.baud <= 0 or args.seconds <= 0:
        parser.error("baud and seconds must be positive")
    ports = [args.port] if args.port else [port.device for port in list_ports.comports()]
    for port in ports:
        print(f"[?] Checking {port} at {args.baud} baud")
        try:
            if scan_port(port, args.baud, args.seconds):
                print(f"Run: python host/air_mouse_host.py --port {port} --baud {args.baud}")
                return 0
        except OSError as error:
            print(f"[-] {port}: {error}")
    print("[-] No matching packets found. Check power, pairing and firmware.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[+] Scan stopped.")
