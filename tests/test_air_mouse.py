import contextlib
import ctypes
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from diagnostics import com_port_scanner as scanner
from host import air_mouse_host as host


class HostTests(unittest.TestCase):
    def setUp(self):
        self.mouse = Mock()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.native = patch.object(host, "user32", SimpleNamespace(mouse_event=self.mouse))
        self.native.start()

    def tearDown(self):
        self.native.stop()
        self.output.__exit__(None, None, None)

    def flags(self):
        return [call.args[0] for call in self.mouse.call_args_list]

    def test_stationary_release_for_both_buttons(self):
        state = host.process_packet("0,0,1,1", 0, False, False)
        self.assertEqual(host.process_packet("0,0,0,0", *state), (2, False, False))
        self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_invalid_packet_preserves_button_state(self):
        for line in ("garbage", "0,0,2,0", "999999,0,0,0", "0,0,0", "0,0,0,0,1"):
            self.assertEqual(host.process_packet(line, 1, True, False), (1, True, False))
        self.mouse.assert_not_called()

    def test_negative_movement_uses_windows_unsigned_arguments(self):
        host.move_mouse(-2, -3)
        self.mouse.assert_called_once_with(1, 0xFFFFFFFE, 0xFFFFFFFD, 0, 0)

    def test_quarter_speed_preserves_small_movements(self):
        move = host.scaled_motion(0.25)
        for _ in range(3):
            move(1, -1)
        self.mouse.assert_not_called()
        move(1, -1)
        self.mouse.assert_called_once_with(1, 1, 0xFFFFFFFF, 0, 0)

    def test_sensitivity_reduces_cursor_distance(self):
        host.scaled_motion(0.25)(400, -200)
        self.mouse.assert_called_once_with(1, 100, ctypes.c_uint32(-50).value, 0, 0)

    def test_scaled_reversal_cancels_and_idle_does_not_drift(self):
        move = host.scaled_motion(0.25)
        move(1, -1)
        move(-1, 1)
        for _ in range(10):
            move(0, 0)
        self.mouse.assert_not_called()

    def test_receive_loop_scales_motion_and_resets_fractional_state(self):
        with patch.object(host, "SENSITIVITY", 0.25):
            with self.assertRaises(KeyboardInterrupt):
                host.run_packet_loop(Mock(side_effect=[b"1,-1,0,0\n" * 3, KeyboardInterrupt()]))
            self.mouse.assert_not_called()
            with self.assertRaises(KeyboardInterrupt):
                host.run_packet_loop(Mock(side_effect=[b"1,-1,0,0\n" * 4, KeyboardInterrupt()]))
        self.mouse.assert_called_once_with(1, 1, 0xFFFFFFFF, 0, 0)

    def test_cli_sensitivity_applies_to_listener(self):
        with patch.object(host, "SENSITIVITY", 0.25), patch.object(host, "initialize_windows"), \
             patch.object(host, "ws2", Mock()), \
             patch.object(host, "run_udp_loop", side_effect=KeyboardInterrupt()):
            host.main(["--listen", "--sensitivity", "0.15"])
            self.assertEqual(host.SENSITIVITY, 0.15)

    def test_invalid_sensitivity_is_rejected_before_opening_devices(self):
        for value in ("0", "-1", "nan", "inf", "5"):
            with patch.object(host, "initialize_windows") as initialize, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                host.main(["--sensitivity", value])
            initialize.assert_not_called()

    def test_fragmented_packets_and_multiple_lines(self):
        chunks = iter([b"0,0,1", b",1\n0,0,0,0\n", KeyboardInterrupt()])
        def read(_):
            item = next(chunks)
            if isinstance(item, BaseException):
                raise item
            return item
        with self.assertRaises(KeyboardInterrupt):
            host.run_packet_loop(read)
        self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_disconnect_and_interrupt_release_both_buttons(self):
        for failure in (ConnectionError("EOF"), KeyboardInterrupt()):
            self.mouse.reset_mock()
            read = Mock(side_effect=[b"0,0,1,1\n", failure])
            with self.assertRaises(type(failure)):
                host.run_packet_loop(read)
            self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_missing_valid_packets_times_out_and_releases_buttons(self):
        with patch.object(host.time, "monotonic", side_effect=[0, 0, 0, 6]):
            with self.assertRaises(TimeoutError):
                host.run_packet_loop(Mock(return_value=b"0,0,1,1\n"), timeout=5)
        self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_garbage_does_not_reset_watchdog(self):
        with patch.object(host.time, "monotonic", side_effect=[0, 0, 6]):
            with self.assertRaises(TimeoutError):
                host.run_packet_loop(Mock(return_value=b"HELLO:1\n"), timeout=5)
        self.mouse.assert_not_called()

    def test_oversized_packet_releases_buttons(self):
        with self.assertRaises(ValueError):
            host.run_packet_loop(Mock(side_effect=[b"0,0,1,1\n", b"x" * 129]))
        self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_exception_only_select_is_not_connection_success(self):
        ws = Mock()
        def select(_, read, writable, errors, timeout):
            writable._obj.count = 0
            errors._obj.count = 1
            return 1
        def getsockopt(sock, level, option, error, size):
            error._obj.value = 10061
            return 0
        ws.select.side_effect = select
        ws.getsockopt.side_effect = getsockopt
        with patch.object(host, "ws2", ws), self.assertRaises(OSError):
            host.wait_socket(42, 1, connecting=True)

    def test_successful_select_checks_socket_error(self):
        ws = Mock()
        def select(_, read, writable, errors, timeout):
            errors._obj.count = 0
            return 1
        ws.select.side_effect = select
        ws.getsockopt.return_value = 0
        with patch.object(host, "ws2", ws):
            self.assertTrue(host.wait_socket(42, 1, connecting=True))
        ws.getsockopt.assert_called_once()

    def test_immediate_connect_success_ignores_stale_last_error(self):
        ws = Mock()
        ws.socket.return_value = 42
        ws.ioctlsocket.return_value = 0
        ws.connect.return_value = 0
        with patch.object(host, "ws2", ws):
            self.assertEqual(host.connect_rfcomm(), 42)
        ws.WSAGetLastError.assert_not_called()
        ws.select.assert_not_called()

    def test_invalid_socket_and_connect_timeout(self):
        ws = Mock()
        ws.socket.return_value = host.INVALID_SOCKET
        ws.WSAGetLastError.return_value = 10050
        with patch.object(host, "ws2", ws), self.assertRaises(OSError):
            host.connect_rfcomm()
        ws.connect.assert_not_called()
        ws.socket.return_value = 42
        ws.ioctlsocket.return_value = 0
        ws.connect.return_value = -1
        ws.WSAGetLastError.return_value = 10035
        with patch.object(host, "ws2", ws), patch.object(host, "wait_socket", return_value=False):
            with self.assertRaises(TimeoutError):
                host.connect_rfcomm()
        ws.closesocket.assert_called_once_with(42)

    def test_rfcomm_eof_releases_buttons(self):
        ws = Mock()
        def recv(sock, buffer, size, flags):
            packet = b"0,0,1,1\n"
            ctypes.memmove(buffer, packet, len(packet))
            ws.recv.side_effect = lambda *args: 0
            return len(packet)
        ws.recv.side_effect = recv
        with patch.object(host, "ws2", ws), patch.object(host, "wait_socket", return_value=True):
            with self.assertRaises(ConnectionError):
                host.run_rfcomm_loop(42)
        self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_com_read_failure_closes_port_and_releases_buttons(self):
        connection = Mock(in_waiting=8)
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.read.side_effect = [b"0,0,1,1\n", OSError("unplugged")]
        with patch("serial.Serial", return_value=connection):
            with self.assertRaises(OSError):
                host.run_com_loop("COM11")
        connection.__exit__.assert_called_once()
        self.assertEqual(self.flags(), [2, 8, 4, 16])

    def test_rfcomm_failure_falls_back_and_cleans_up_winsock(self):
        ws = Mock()
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", ws), \
             patch.object(host, "connect_rfcomm", return_value=42), \
             patch.object(host, "run_rfcomm_loop", side_effect=ConnectionError("EOF")), \
             patch.object(host, "available_ports", return_value=["COM11"]), \
             patch.object(host, "run_com_loop", side_effect=KeyboardInterrupt()) as serial_loop:
            host.main([])
        serial_loop.assert_called_once_with("COM11", 115200, 5)
        ws.closesocket.assert_called_once_with(42)
        ws.WSACleanup.assert_called_once()

    def test_explicit_com_port_skips_rfcomm_and_retries(self):
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", Mock()), \
             patch.object(host, "connect_rfcomm") as connect, \
             patch.object(host, "run_com_loop", side_effect=[OSError("offline"), KeyboardInterrupt()]) as run, \
             patch.object(host.time, "sleep"):
            host.main(["--port", "COM11", "--baud", "115200"])
        connect.assert_not_called()
        self.assertEqual(run.call_count, 2)
        run.assert_called_with("COM11", 115200, 5)


class ScannerTests(unittest.TestCase):
    def test_packet_recognition(self):
        for line in (b"0,0,0,0", b"-12,8,1,0\r", b"HELLO:3"):
            self.assertTrue(scanner.is_device_line(line))
        for line in (b"unrelated serial data", b"0,0,9,0", b"HELLO:bad", b"\xff0,0,1,1"):
            self.assertFalse(scanner.is_device_line(line))

    def test_enumerates_ports_above_com9(self):
        ports = [SimpleNamespace(device="COM11", description="Bluetooth serial")]
        with patch.object(scanner.list_ports, "comports", return_value=ports), \
             patch.object(scanner, "scan_port", return_value=True) as scan, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scanner.main([]), 0)
        scan.assert_called_once_with("COM11", 115200, 6)

    def test_fragmented_scanner_packets_and_close(self):
        connection = Mock(in_waiting=8)
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.read.side_effect = [b"0,0,", b"0,0\n"]
        with patch.object(scanner.serial, "Serial", return_value=connection), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.scan_port("COM11", 9600, 6))
        connection.__exit__.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "Windows ABI smoke test")
class WindowsBindingTests(unittest.TestCase):
    def test_native_signatures_and_real_local_socket_select(self):
        import socket
        try:
            host.initialize_windows()
            self.assertEqual(host.ws2.socket.restype, ctypes.c_size_t)
            self.assertEqual(host.ws2.recv.argtypes[0], ctypes.c_size_t)
            self.assertEqual(ctypes.sizeof(host.Timeval), 8)
            first, second = socket.socketpair()
            try:
                self.assertFalse(host.wait_socket(first.fileno(), 0))
                second.sendall(b"x")
                self.assertTrue(host.wait_socket(first.fileno(), 1))
                buffer = ctypes.create_string_buffer(1)
                self.assertEqual(host.ws2.recv(first.fileno(), buffer, 1, 0), 1)
                self.assertEqual(buffer.raw, b"x")
                host.send_rfcomm(first.fileno(), b"probe")
                self.assertEqual(second.recv(5), b"probe")
            finally:
                first.close()
                second.close()
        finally:
            if host.ws2 is not None:
                host.ws2.WSACleanup()


if __name__ == "__main__":
    unittest.main()
