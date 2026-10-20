"""Keep one Bluetooth/COM connection open, inspect bytes, relay mouse packets locally."""

import argparse
import socket
import time

if __package__:
    from . import air_mouse_host as driver
else:
    import air_mouse_host as driver

PROBE = b"\nHC06_LOOPBACK\n"


def monitor_stream(read_chunk, forward, raw=False, probe=False):
    """Silence never closes the transport. Only complete valid packets are forwarded."""
    pending = b""
    discarding = False
    total_bytes = packets = invalid = 0
    last_report = time.monotonic()
    latest = b""
    print("[RX] Holding this connection through silence. Ctrl+C to stop.", flush=True)
    while True:
        chunk = read_chunk()
        if chunk:
            if raw or total_bytes == 0:
                print(f"[RAW] {len(chunk)} bytes: {chunk!r}", flush=True)
            total_bytes += len(chunk)
            latest = chunk
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if discarding or len(line) > 128:
                    discarding = False
                    invalid += 1
                    continue
                if probe and line.strip() == PROBE.strip():
                    print("[LOOPBACK PASS] HC-06 echoed HC06_LOOPBACK over this connection.", flush=True)
                    continue
                try:
                    packet = driver.parse_packet(line.decode("ascii").strip())
                except UnicodeDecodeError:
                    packet = None
                if packet is None:
                    invalid += 1
                    if line.startswith(b"HELLO:"):
                        print(f"[TEST] {line!r}: radio data received; upload haath for mouse control.", flush=True)
                    continue
                packets += 1
                if packets == 1:
                    print(f"[VALID] {packet}; start the cursor script with --listen.", flush=True)
                try:
                    forward((",".join(map(str, packet)) + "\n").encode("ascii"))
                except OSError as error:
                    # A stopped local consumer must not tear down the radio connection.
                    print(f"[RELAY] {error}; Bluetooth connection remains open.", flush=True)
            if len(pending) > 128:
                pending = b""
                discarding = True
        now = time.monotonic()
        if now - last_report >= 2:
            print(f"[RX] bytes={total_bytes} mouse_packets={packets} other_lines={invalid} "
                  f"latest={latest[:80]!r}", flush=True)
            if total_bytes == 0:
                print("[RX] Still zero bytes; connection remains open.", flush=True)
            last_report = now


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Hold a specific COM port instead of native Bluetooth")
    parser.add_argument("--baud", type=int, default=driver.BAUD,
                        help="Serial baud (default 115200, matching this HC-06 and ESP USB)")
    parser.add_argument("--mac", default=driver.HC06_MAC)
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--udp-port", type=int, default=driver.RELAY_PORT)
    parser.add_argument("--raw", action="store_true", help="Show every received chunk, including malformed data")
    parser.add_argument("--probe", action="store_true",
                        help="Send a loopback test message; requires HC-06 TXD jumpered to its RXD")
    args = parser.parse_args(argv)
    if args.baud <= 0 or not 1 <= args.channel <= 30 or not 1 <= args.udp_port <= 65535:
        parser.error("baud must be positive; channel 1-30; UDP port 1-65535")
    parts = args.mac.split(":")
    if len(parts) != 6 or any(len(part) != 2 or any(char not in "0123456789abcdefABCDEF"
            for char in part) for part in parts):
        parser.error("MAC must contain six hexadecimal byte pairs")
    initialized = False
    try:
        driver.initialize_windows()
        initialized = True
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay:
            destination = ("127.0.0.1", args.udp_port)
            print(f"[RELAY] Mouse packets go to {destination[0]}:{destination[1]}.", flush=True)
            print("[RX] This terminal never moves the cursor. Stop other Bluetooth/COM readers.", flush=True)
            while True:
                try:
                    if args.port:
                        import serial
                        print(f"[COM] Holding {args.port} at {args.baud} baud", flush=True)
                        with serial.Serial(args.port, args.baud, timeout=0.25) as connection:
                            if args.probe:
                                connection.write_timeout = 1
                                if connection.write(PROBE) != len(PROBE):
                                    raise OSError("Incomplete serial probe write")
                                print("[PROBE] Sent HC06_LOOPBACK; waiting for echo.", flush=True)
                            monitor_stream(lambda: connection.read(min(256, max(1, connection.in_waiting))),
                                           lambda packet: relay.sendto(packet, destination), args.raw, args.probe)
                    else:
                        sock = driver.connect_rfcomm(args.mac, args.channel)
                        try:
                            if args.probe:
                                driver.send_rfcomm(sock, PROBE)
                                print("[PROBE] Sent HC06_LOOPBACK; waiting for echo.", flush=True)
                            monitor_stream(lambda: driver.read_rfcomm_chunk(sock),
                                           lambda packet: relay.sendto(packet, destination), args.raw, args.probe)
                        finally:
                            driver.ws2.closesocket(sock)
                except (OSError, ValueError) as error:
                    print(f"[RX] Transport error: {error}; reconnecting in 2 seconds.", flush=True)
                    time.sleep(2)
    except KeyboardInterrupt:
        print("\n[RX] Stopped; connection closed.")
    finally:
        if initialized:
            driver.ws2.WSACleanup()


if __name__ == "__main__":
    main()
