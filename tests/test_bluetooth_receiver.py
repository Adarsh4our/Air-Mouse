import contextlib
import ctypes
import io
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from host import air_mouse_host as host
from host import bluetooth_receiver as receiver


class ReceiverTests(unittest.TestCase):
    def run_chunks(self, chunks, forward=None, raw=False, probe=False):
        read = Mock(side_effect=[*chunks, KeyboardInterrupt()])
        forward = forward if forward is not None else Mock()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(KeyboardInterrupt):
            receiver.monitor_stream(read, forward, raw, probe)
        return forward, output.getvalue()

    def test_silent_connection_stays_open_past_cursor_watchdog(self):
        with patch.object(receiver.time, "monotonic", side_effect=range(32)):
            forward, output = self.run_chunks([b""] * 30)
        forward.assert_not_called()
        self.assertIn("Still zero bytes; connection remains open", output)

    def test_fragmented_packets_forward_without_mouse_events(self):
        mouse = Mock()
        with patch.object(host, "user32", SimpleNamespace(mouse_event=mouse)):
            forward, output = self.run_chunks([b"12,-8,", b"0,0\n0,0,0,0\r\n"])
        self.assertEqual([call.args[0] for call in forward.call_args_list],
                         [b"12,-8,0,0\n", b"0,0,0,0\n"])
        self.assertIn("[VALID]", output)
        mouse.assert_not_called()

    def test_raw_invalid_and_hello_data_remain_visible(self):
        forward, output = self.run_chunks([b"\xff\x00\nHELLO:1\n0,0,2,0\n"], raw=True)
        forward.assert_not_called()
        self.assertIn("[RAW]", output)
        self.assertIn("\\xff", output)
        self.assertIn("[TEST]", output)

    def test_overlong_line_is_discarded_without_forwarding_its_suffix(self):
        forward, _ = self.run_chunks([b"x" * 256, b"12,8,0,0\n3,4,0,0\n"])
        forward.assert_called_once_with(b"3,4,0,0\n")

    def test_local_relay_error_does_not_close_bluetooth(self):
        forward = Mock(side_effect=[OSError("consumer stopped"), None])
        _, output = self.run_chunks([b"1,2,0,0\n3,4,0,0\n"], forward)
        self.assertEqual(forward.call_count, 2)
        self.assertIn("connection remains open", output)

    def test_real_local_udp_relay_receives_only_mouse_packets(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener, \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay:
            listener.bind(("127.0.0.1", 0))
            listener.settimeout(1)
            destination = listener.getsockname()
            self.run_chunks([b"HELLO:1\n9,-2,0,0\n"], lambda packet: relay.sendto(packet, destination))
            packet, address = listener.recvfrom(256)
            self.assertEqual(packet, b"9,-2,0,0\n")
            self.assertEqual(address[0], "127.0.0.1")

    def test_native_reader_holds_and_closes_once_on_ctrl_c(self):
        ws = Mock()
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", ws), \
             patch.object(host, "connect_rfcomm", return_value=42) as connect, \
             patch.object(receiver, "monitor_stream", side_effect=KeyboardInterrupt()), \
             contextlib.redirect_stdout(io.StringIO()):
            receiver.main([])
        connect.assert_called_once()
        ws.closesocket.assert_called_once_with(42)
        ws.WSACleanup.assert_called_once()

    def test_serial_reader_closes_port_on_ctrl_c(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", Mock()), \
             patch.object(host, "connect_rfcomm") as connect, \
             patch("serial.Serial", return_value=connection) as serial, \
             patch.object(receiver, "monitor_stream", side_effect=KeyboardInterrupt()), \
             contextlib.redirect_stdout(io.StringIO()):
            receiver.main(["--port", "COM10", "--baud", "115200"])
        connect.assert_not_called()
        serial.assert_called_once_with("COM10", 115200, timeout=0.25)
        connection.__exit__.assert_called_once()

    def test_fragmented_probe_echo_is_detected_but_not_forwarded(self):
        forward, output = self.run_chunks([b"\xff\nHC06_LO", b"OPBACK\n"], probe=True)
        self.assertIn("[LOOPBACK PASS]", output)
        forward.assert_not_called()

    def test_probe_send_is_opt_in(self):
        for arguments, expected_sends in (([], 0), (["--probe"], 1)):
            with patch.object(host, "initialize_windows"), patch.object(host, "ws2", Mock()), \
                 patch.object(host, "connect_rfcomm", return_value=42), \
                 patch.object(host, "send_rfcomm") as send, \
                 patch.object(receiver, "monitor_stream", side_effect=KeyboardInterrupt()), \
                 contextlib.redirect_stdout(io.StringIO()):
                receiver.main(arguments)
            self.assertEqual(send.call_count, expected_sends)
            if expected_sends:
                send.assert_called_once_with(42, receiver.PROBE)

    def test_explicit_com_probe_writes_the_same_message(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.write.return_value = len(receiver.PROBE)
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", Mock()), \
             patch("serial.Serial", return_value=connection), \
             patch.object(receiver, "monitor_stream", side_effect=KeyboardInterrupt()), \
             contextlib.redirect_stdout(io.StringIO()):
            receiver.main(["--port", "COM5", "--probe"])
        connection.write.assert_called_once_with(receiver.PROBE)
        self.assertEqual(connection.write_timeout, 1)


class ProbeSendTests(unittest.TestCase):
    def test_partial_writes_and_would_block_preserve_message(self):
        ws = Mock()
        remaining_messages = []
        sizes = iter([3, -1, len(receiver.PROBE) - 3])
        def send(sock, buffer, length, flags):
            remaining_messages.append(ctypes.string_at(buffer, length))
            return next(sizes)
        ws.send.side_effect = send
        ws.WSAGetLastError.return_value = 10035
        with patch.object(host, "ws2", ws), patch.object(host.time, "sleep"):
            host.send_rfcomm(42, receiver.PROBE)
        self.assertEqual(remaining_messages, [receiver.PROBE, receiver.PROBE[3:], receiver.PROBE[3:]])

    def test_send_timeout_is_bounded(self):
        ws = Mock()
        ws.send.return_value = -1
        ws.WSAGetLastError.return_value = 10035
        with patch.object(host, "ws2", ws), patch.object(host.time, "sleep"), \
             patch.object(host.time, "monotonic", side_effect=[0, 0, 2]):
            with self.assertRaises(TimeoutError):
                host.send_rfcomm(42, receiver.PROBE)

    def test_fatal_error_and_zero_write_are_reported(self):
        ws = Mock()
        for result in (-1, 0):
            ws.send.return_value = result
            ws.WSAGetLastError.return_value = 10054
            with patch.object(host, "ws2", ws), self.assertRaises(OSError):
                host.send_rfcomm(42, receiver.PROBE)


class RelayHostTests(unittest.TestCase):
    def test_real_port_conflict_is_explained_and_cleans_up(self):
        ws = Mock()
        output = io.StringIO()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as existing:
            existing.bind(("127.0.0.1", 0))
            port = existing.getsockname()[1]
            with patch.object(host, "initialize_windows"), patch.object(host, "ws2", ws), \
                 contextlib.redirect_stdout(output):
                result = host.main(["--listen", str(port)])
        self.assertEqual(result, 1)
        self.assertIn(f"Port {port} is already in use", output.getvalue())
        self.assertIn("Ctrl+C", output.getvalue())
        ws.WSACleanup.assert_called_once()

    def test_unrelated_listener_errors_are_not_hidden(self):
        ws = Mock()
        failure = OSError(5, "access denied")
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", ws), \
             patch.object(host, "run_udp_loop", side_effect=failure), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError) as raised:
                host.main(["--listen"])
        self.assertIs(raised.exception, failure)
        ws.WSACleanup.assert_called_once()

    def test_auto_ports_skip_usb_adapters(self):
        ports = [SimpleNamespace(device="COM10", description="CP210x USB UART", vid=0x10C4),
                 SimpleNamespace(device="COM11", description="Bluetooth serial", vid=None)]
        with patch("serial.tools.list_ports.comports", return_value=ports):
            self.assertEqual(host.available_ports(), ["COM11"])

    def test_listen_mode_never_opens_bluetooth_or_com(self):
        ws = Mock()
        with patch.object(host, "initialize_windows"), patch.object(host, "ws2", ws), \
             patch.object(host, "connect_rfcomm") as connect, \
             patch.object(host, "run_com_loop") as serial, \
             patch.object(host, "run_udp_loop", side_effect=KeyboardInterrupt()) as listen, \
             contextlib.redirect_stdout(io.StringIO()):
            host.main(["--listen"])
        listen.assert_called_once_with(8765, 5)
        connect.assert_not_called()
        serial.assert_not_called()
        ws.WSACleanup.assert_called_once()

    def test_udp_listener_only_binds_loopback_and_waits_after_silence(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        def packet_loop(read_chunk, timeout):
            self.assertEqual(read_chunk(1), b"5,6,0,0\n")
            raise TimeoutError()
        connection.recvfrom.return_value = (b"5,6,0,0\n", ("127.0.0.1", 40000))
        with patch.object(host.socket, "socket", return_value=connection), \
             patch.object(host, "run_packet_loop") as loop, \
             contextlib.redirect_stdout(io.StringIO()):
            calls = 0
            def dispatch(read_chunk, timeout):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return packet_loop(read_chunk, timeout)
                raise KeyboardInterrupt()
            loop.side_effect = dispatch
            with self.assertRaises(KeyboardInterrupt):
                host.run_udp_loop()
        connection.bind.assert_called_once_with(("127.0.0.1", 8765))
        self.assertEqual(calls, 2)
        connection.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
