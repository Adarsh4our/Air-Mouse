"""Windows air mouse receiver: native RFCOMM with serial-port recovery."""

import argparse
import ctypes
import errno
import math
import socket
import struct
import sys
import time

HC06_MAC = "98:D3:A1:FD:41:DD"
BAUD = 115200
CONNECT_TIMEOUT_S = 15
DATA_TIMEOUT_S = 5
RELAY_PORT = 8765
SENSITIVITY = 0.25
AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM = 32, 1, 3
FIONBIO = 0x8004667E
SOL_SOCKET, SO_ERROR = 0xFFFF, 0x1007
SOCKET = ctypes.c_size_t
INVALID_SOCKET = SOCKET(-1).value
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
user32 = ws2 = None


class FDSet(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("sockets", SOCKET * 1)]


class Timeval(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_int32), ("microseconds", ctypes.c_int32)]


def initialize_windows():
    global user32, ws2
    if sys.platform != "win32":
        raise OSError("This mouse driver requires Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.mouse_event.argtypes = [ctypes.c_uint32] * 4 + [SOCKET]
    user32.mouse_event.restype = None
    ws2 = ctypes.WinDLL("ws2_32")
    signatures = {
        "WSAStartup": ([ctypes.c_uint16, ctypes.c_void_p], ctypes.c_int),
        "WSACleanup": ([], ctypes.c_int),
        "WSAGetLastError": ([], ctypes.c_int),
        "socket": ([ctypes.c_int] * 3, SOCKET),
        "closesocket": ([SOCKET], ctypes.c_int),
        "ioctlsocket": ([SOCKET, ctypes.c_int32, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int),
        "connect": ([SOCKET, ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
        "recv": ([SOCKET, ctypes.c_void_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
        "send": ([SOCKET, ctypes.c_void_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
        "getsockopt": ([SOCKET, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
        "select": ([ctypes.c_int, ctypes.POINTER(FDSet), ctypes.POINTER(FDSet),
                    ctypes.POINTER(FDSet), ctypes.POINTER(Timeval)], ctypes.c_int),
    }
    for name, (args, result) in signatures.items():
        function = getattr(ws2, name)
        function.argtypes, function.restype = args, result
    # This buffer accommodates WSADATA on both 32-bit and 64-bit Windows.
    result = ws2.WSAStartup(0x0202, ctypes.create_string_buffer(408))
    if result:
        raise OSError(result, "WSAStartup failed")


def move_mouse(dx, dy):
    user32.mouse_event(MOUSEEVENTF_MOVE, ctypes.c_uint32(dx).value,
                       ctypes.c_uint32(dy).value, 0, 0)


def scaled_motion(sensitivity):
    remainder_x = remainder_y = 0.0

    def move(dx, dy):
        nonlocal remainder_x, remainder_y
        scaled_x = dx * sensitivity + remainder_x
        scaled_y = dy * sensitivity + remainder_y
        pixels_x, pixels_y = int(scaled_x), int(scaled_y)
        remainder_x, remainder_y = scaled_x - pixels_x, scaled_y - pixels_y
        if pixels_x or pixels_y:
            move_mouse(pixels_x, pixels_y)

    return move


def handle_buttons(left, right, prev_left, prev_right):
    if left != prev_left:
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN if left else MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if right != prev_right:
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN if right else MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def parse_packet(line):
    try:
        dx, dy, left, right = map(int, line.split(","))
        if not (-32768 <= dx <= 32767 and -32768 <= dy <= 32767
                and left in (0, 1) and right in (0, 1)):
            raise ValueError("out-of-range mouse packet")
    except ValueError:
        return None
    return dx, dy, left, right


def process_packet(line, count, prev_left, prev_right, motion=None):
    packet = parse_packet(line)
    if packet is None:
        return count, prev_left, prev_right
    dx, dy, left, right = packet
    if dx or dy:
        (motion if motion is not None else move_mouse)(dx, dy)
    handle_buttons(bool(left), bool(right), prev_left, prev_right)
    return count + 1, bool(left), bool(right)


def make_sockaddr_bth(mac, channel):
    raw = struct.pack("<HQ16sI", AF_BTH, int(mac.replace(":", ""), 16), b"\0" * 16, channel)
    return ctypes.create_string_buffer(raw), len(raw)


def wait_socket(sock, timeout, connecting=False):
    ready = FDSet(1, (SOCKET * 1)(sock))
    errors = FDSet(1, (SOCKET * 1)(sock))
    seconds = max(0, timeout)
    tv = Timeval(int(seconds), int(seconds % 1 * 1_000_000))
    result = ws2.select(0, None if connecting else ctypes.byref(ready),
                        ctypes.byref(ready) if connecting else None,
                        ctypes.byref(errors) if connecting else None, ctypes.byref(tv))
    if result == -1:
        raise OSError(ws2.WSAGetLastError(), "select failed")
    if result == 0:
        return False
    if connecting:
        error, size = ctypes.c_int(), ctypes.c_int(ctypes.sizeof(ctypes.c_int))
        if ws2.getsockopt(sock, SOL_SOCKET, SO_ERROR, ctypes.byref(error), ctypes.byref(size)) == -1:
            raise OSError(ws2.WSAGetLastError(), "getsockopt failed")
        if error.value or errors.count:
            raise OSError(error.value, "Bluetooth connection failed")
    return ready.count == 1


def connect_rfcomm(mac=HC06_MAC, channel=1, timeout=CONNECT_TIMEOUT_S):
    sock = ws2.socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM)
    if sock == INVALID_SOCKET:
        raise OSError(ws2.WSAGetLastError(), "socket failed")
    try:
        mode = ctypes.c_uint32(1)
        if ws2.ioctlsocket(sock, ctypes.c_int32(FIONBIO).value, ctypes.byref(mode)) == -1:
            raise OSError(ws2.WSAGetLastError(), "nonblocking mode failed")
        address, size = make_sockaddr_bth(mac, channel)
        result = ws2.connect(sock, address, size)
        if result == -1:
            error = ws2.WSAGetLastError()
            if error not in (10035, 10036, 10037):
                raise OSError(error, "connect failed")
            if not wait_socket(sock, timeout, connecting=True):
                raise TimeoutError("Bluetooth connection timed out")
        print(f"[BT] Connected to {mac}; waiting for mouse packets.")
        return sock
    except BaseException:
        ws2.closesocket(sock)
        raise


def run_packet_loop(read_chunk, timeout=DATA_TIMEOUT_S):
    """Both transports share framing, the valid-packet watchdog and button cleanup."""
    count, left, right = 0, False, False
    motion = scaled_motion(SENSITIVITY)
    pending = b""
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("No valid mouse packets; releasing buttons and reconnecting")
            chunk = read_chunk(remaining)
            if not chunk:
                continue
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                if len(raw) > 128:
                    continue
                try:
                    line = raw.decode("ascii").strip()
                except UnicodeDecodeError:
                    continue
                previous_count = count
                count, left, right = process_packet(line, count, left, right, motion)
                if count != previous_count:
                    deadline = time.monotonic() + timeout
                    if count == 1:
                        print("[+] Receiving mouse packets. Ctrl+C to exit.")
            if len(pending) > 128:
                raise ValueError("Mouse packet exceeds 128 bytes")
    finally:
        handle_buttons(False, False, left, right)


def send_rfcomm(sock, data, timeout=1.0):
    buffer = ctypes.create_string_buffer(data)
    sent = 0
    deadline = time.monotonic() + timeout
    while sent < len(data):
        if time.monotonic() >= deadline:
            raise TimeoutError("Bluetooth probe send timed out")
        size = ws2.send(sock, ctypes.byref(buffer, sent), len(data) - sent, 0)
        if size == -1:
            error = ws2.WSAGetLastError()
            if error == 10035:
                time.sleep(0.01)
                continue
            raise OSError(error, "Bluetooth probe send failed")
        if size == 0:
            raise ConnectionError("Bluetooth link closed while sending probe")
        sent += size


def read_rfcomm_chunk(sock, timeout=0.25):
    buffer = ctypes.create_string_buffer(256)
    if not wait_socket(sock, timeout):
        return b""
    size = ws2.recv(sock, buffer, len(buffer), 0)
    if size == 0:
        raise ConnectionError("Bluetooth link closed")
    if size == -1:
        error = ws2.WSAGetLastError()
        if error == 10035:
            return b""
        raise OSError(error, "Bluetooth receive failed")
    return buffer.raw[:size]


def run_rfcomm_loop(sock, timeout=DATA_TIMEOUT_S):
    # Short waits let Ctrl+C interrupt an idle connection on Windows.
    run_packet_loop(lambda remaining: read_rfcomm_chunk(sock, min(remaining, 0.25)), timeout)


def run_com_loop(port, baud=BAUD, timeout=DATA_TIMEOUT_S):
    import serial
    print(f"[COM] Opening {port} at {baud} baud")
    with serial.Serial(port, baud, timeout=0.25) as connection:
        run_packet_loop(lambda remaining: connection.read(min(256, max(1, connection.in_waiting))), timeout)


def available_ports():
    from serial.tools import list_ports
    # Open USB adapters only when selected explicitly; probing may reset other devices.
    ports = [port for port in list_ports.comports() if getattr(port, "vid", None) is None]
    return [port.device for port in sorted(ports,
            key=lambda port: ("bluetooth" not in (port.description or "").lower(), port.device))]


def run_udp_loop(port=RELAY_PORT, timeout=DATA_TIMEOUT_S):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.bind(("127.0.0.1", port))
        connection.settimeout(0.25)
        print(f"[LOCAL] Listening on 127.0.0.1:{port}; Bluetooth is owned by bluetooth_receiver.py")

        def read_chunk(remaining):
            try:
                data, _ = connection.recvfrom(256)
                return data
            except socket.timeout:
                return b""

        while True:
            try:
                run_packet_loop(read_chunk, timeout)
            except TimeoutError:
                print("[LOCAL] No valid packets; waiting for the Bluetooth receiver.")


def main(argv=None):
    global SENSITIVITY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Use only this COM port, e.g. COM11 (skip RFCOMM)")
    parser.add_argument("--listen", type=int, nargs="?", const=RELAY_PORT,
                        help="Receive localhost packets from bluetooth_receiver.py (default port 8765)")
    parser.add_argument("--baud", type=int, default=BAUD, help="Serial baud (default 115200)")
    parser.add_argument("--mac", default=HC06_MAC, help="HC-06 Bluetooth MAC address")
    parser.add_argument("--channel", type=int, default=1, help="RFCOMM channel")
    parser.add_argument("--timeout", type=float, default=DATA_TIMEOUT_S, help="Seconds without valid packets before recovery")
    parser.add_argument("--sensitivity", type=float, default=0.25,
                        help="Cursor speed multiplier (default 0.25; 1 restores original speed)")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.baud <= 0 or not 1 <= args.channel <= 30:
        parser.error("timeout and baud must be positive; channel must be 1-30")
    if args.listen is not None and (args.port or not 1 <= args.listen <= 65535):
        parser.error("--listen requires a UDP port from 1-65535 and cannot be combined with --port")
    if not math.isfinite(args.sensitivity) or not 0 < args.sensitivity <= 4:
        parser.error("sensitivity must be greater than 0 and at most 4")
    SENSITIVITY = args.sensitivity
    try:
        if len(args.mac.split(":")) != 6 or any(len(part) != 2 for part in args.mac.split(":")):
            raise ValueError()
        int(args.mac.replace(":", ""), 16)
    except ValueError:
        parser.error("MAC must have six hexadecimal byte pairs, e.g. 98:D3:A1:FD:41:DD")
    initialized = False
    try:
        initialize_windows()
        initialized = True
        print(f"[MOUSE] Sensitivity {SENSITIVITY:g}x (1 = original speed)")
        if args.listen is not None:
            try:
                run_udp_loop(args.listen, args.timeout)
            except OSError as error:
                if getattr(error, "winerror", None) != 10048 and error.errno != errno.EADDRINUSE:
                    raise
                print(f"[LOCAL] Port {args.listen} is already in use. A cursor listener may already be running.")
                print("[LOCAL] Stop the existing cursor listener with Ctrl+C, then run this command again.")
                print("[LOCAL] For another port, change BOTH receiver --udp-port and cursor --listen to the same number.")
                return 1
            return
        while True:
            if not args.port:
                try:
                    sock = connect_rfcomm(args.mac, args.channel)
                    try:
                        run_rfcomm_loop(sock, args.timeout)
                    finally:
                        ws2.closesocket(sock)
                except (OSError, ValueError) as error:
                    print(f"[BT] {error}; trying serial ports.")
            ports = [args.port] if args.port else available_ports()
            if not ports:
                print("[COM] No serial ports found. Pair the HC-06 or specify --port.")
            for port in ports:
                try:
                    run_com_loop(port, args.baud, args.timeout)
                except (OSError, ValueError) as error:
                    print(f"[COM] {port}: {error}")
            print("[!] Retrying in 2 seconds. Ctrl+C to exit.")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[+] Exited; mouse buttons released.")
    finally:
        if initialized:
            ws2.WSACleanup()


if __name__ == "__main__":
    raise SystemExit(main())
