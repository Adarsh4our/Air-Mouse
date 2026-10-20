# Air Mouse

An ESP8266 + MPU6050 handheld mouse, with an HC-06 Bluetooth serial link to a Windows Python receiver. This version has no buttons: motion control stays active after startup calibration.

## Setup

1. Wire the device using [WIRING_AND_CONNECTIONS.md](docs/WIRING_AND_CONNECTIONS.md).
2. Open **firmware/haath/haath.ino** in Arduino IDE. Select your actual ESP8266 board (NodeMCU 1.0 for a NodeMCU, LOLIN D1 mini for a D1 mini). The checked local ESP8266 core is **3.1.2**, which includes Wire and EspSoftwareSerial **8.0.1**.
3. Upload the sketch. Keep the sensor still for calibration: normally about two seconds, up to four seconds if I2C reads need retries. USB debug output runs at **115200 baud**. A setup/calibration error stops mouse packets; fix the connection and reset.
4. Pair the HC-06 in Windows. Close Arduino Serial Monitor before opening the same USB port in Python.
5. From this project folder, install the Python dependency and start the receiver:

```powershell
python -m pip install -r requirements.txt
python host/air_mouse_host.py
```

Verify the uploaded sketch in USB Serial Monitor after reset: the current build prints **`AIR_MOUSE_ACTIVE_115200_V1`** at startup and in its heartbeat. After calibration, `0,0,0,0` must stream even while still, and the packet count should rise by about 100 every two seconds. Open this repository's `firmware/haath/haath.ino` in Arduino IDE to ensure you upload the current sketch.

The receiver tries native Bluetooth first, then enumerated non-USB COM ports, preferring those described as Bluetooth. USB adapters are skipped in auto mode to avoid resetting unrelated devices; select USB explicitly with `--port`. Both USB and the verified HC-06 UART now use **115200 baud**, also the default for Python serial connections. The receiver retries after failures. A silent transport or sensor stops holding mouse buttons after five seconds without a valid packet. Ctrl+C releases held buttons and closes the connection.

To choose a known Bluetooth port directly (replace COM11 with your port):

```powershell
python diagnostics/com_port_scanner.py
python host/air_mouse_host.py --port COM11
```

The scanner checks actual enumerated ports, including COM10 and above, and reports a match only for a complete mouse or `HELLO` packet. Opening serial ports can reset attached microcontrollers; use `--port` to limit a scan when other serial equipment is attached.

For a USB-only check, with the port from Device Manager:

```powershell
python host/air_mouse_host.py --port COM10 --baud 115200
```

Use `--mac <address>` for a different HC-06 address, `--channel` for a different RFCOMM channel, and `--timeout` to change the five-second data timeout. `--help` lists the options.

## Bluetooth diagnostics in two terminals

Stop the previous host with Ctrl+C first, and close any other program reading the HC-06. Run both terminals from this project folder.

**Terminal 1: hold Bluetooth and inspect data.** This script keeps one connection open through silence, reports received byte/packet counts every two seconds, and never sends mouse events:

```powershell
python host/bluetooth_receiver.py --raw
```

Native Bluetooth is the default. To test one Windows Bluetooth COM port instead, stop the receiver and restart it with `--port COM5 --raw`, substituting the actual outgoing Bluetooth port. The diagnostic receiver does not switch to other ports because of silence. It reconnects only after a transport error or link closure.

**Terminal 2: move the cursor after Terminal 1 reports `[VALID]`.**

```powershell
python host/air_mouse_host.py --listen
```

Only Terminal 1 opens Bluetooth. It forwards complete valid mouse packets to UDP `127.0.0.1:8765`; Terminal 2 listens locally and dispatches mouse events. Terminal 2 may be started or stopped while Terminal 1 stays running. Only one cursor listener can bind the chosen port. To use a different port, pass `--udp-port 8766` to Terminal 1 and `--listen 8766` to Terminal 2.

If Terminal 2 reports that port 8765 is already in use (Windows error 10048), an earlier cursor listener or another program is still bound to it. Stop the previous cursor-listener terminal with Ctrl+C, then retry. To identify the owner, run `netstat -ano -p udp | Select-String ':8765\s'` in PowerShell; its last column is the process ID. Do not stop all Python processes: Terminal 1 needs to keep running. If another application needs that port, use the alternate port on both scripts. The cursor command explains this conflict and exits with code 1 instead of printing a traceback.

### Cursor speed

The Windows consumer defaults to **0.25x** the original cursor movement, a slower starting point for the requested roughly 400-DPI feel. Gyro motion is measured as rotation, so this is not a literal or calibrated mouse DPI value; perceived speed also depends on the existing firmware acceleration and Windows pointer settings. Fractional movements accumulate between packets so reducing speed preserves fine aiming movements.

Keep Terminal 1 running and restart only Terminal 2 to tune speed, without uploading firmware:

```powershell
python host/air_mouse_host.py --listen --sensitivity 0.25
```

Lower values move more slowly: try `0.15` if it still feels fast, or `0.35` if it feels too slow. `1` restores the original movement scale. The same option applies in direct Bluetooth, COM and localhost modes. Fractional state resets when a receive loop restarts.

### Diagnostic output

Interpret Terminal 1's output:

- `bytes=0`: the connection opened but no application bytes have arrived. Splitting the scripts cannot create missing data; check firmware upload, sensor startup, UART wiring/baud and module power.
- `[RAW]` data with `mouse_packets=0`: bytes arrive, but they are not valid mouse packets. Inspect the displayed bytes for wrong baud or unexpected firmware output.
- `[TEST] HELLO:...`: the HC-06 test sketch is delivering data. Upload the production `haath` sketch before expecting cursor movement.
- `[VALID]`: a complete mouse packet arrived and is being relayed locally.

To isolate the ESP USB stream without moving the cursor, use `python host/bluetooth_receiver.py --port COM10 --baud 115200 --raw`, replacing COM10 with its actual port. Close Serial Monitor first. Seeing valid USB packets proves the sensor/firmware stream works over USB; it does not prove the HC-06 UART/radio path works.

The diagnostic reader uses short reads to remain interruptible and keeps the connection open across empty reads, as described by the [pySerial timeout behavior](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.read). Local forwarding uses the standard library [socket API](https://docs.python.org/3/library/socket.html).

### HC-06 module loopback

After confirming the ESP8266 D6-to-D5 loopback, isolate the module itself:

1. Stop receivers and disconnect power before changing wires.
2. Remove the ESP8266 D6-to-D5 jumper and disconnect both HC-06 UART wires from the ESP8266.
3. Join **HC-06 TXD directly to HC-06 RXD**, on the module side. Leave the module's verified supply and ground connected; its UART pins must have no connection to ESP8266 GPIOs during this test.
4. Restore power and run:

```powershell
python host/bluetooth_receiver.py --raw --probe
```

The optional probe sends the plain-text line `HC06_LOOPBACK` once per connection. The expected response is `[LOOPBACK PASS] HC-06 echoed HC06_LOOPBACK over this connection.` The path under test is PC -> Bluetooth -> HC-06 TXD -> jumper -> HC-06 RXD -> Bluetooth -> PC. No firmware upload is needed for this module-only test.

An exact echo verifies that this path works; by itself, it does not establish the HC-06's configured UART baud or prove it matches the ESP8266. If it passes, investigate the ESP-to-module wiring and baud match next. If it fails, check the module jumper/pin contacts and supply, and compare the native connection with the Windows outgoing Bluetooth COM port using `--port COM5 --raw --probe` (substitute the actual outgoing port, and stop the previous receiver first).

Power off before removing the module jumper and restoring **ESP D6 -> HC-06 RXD**, **HC-06 TXD -> ESP D5**, and common ground. Without `--probe`, the diagnostic receiver remains read-only on the device connection. Probe writes handle partial sends and use a bounded timeout, following [Winsock send semantics](https://learn.microsoft.com/en-us/windows/win32/api/winsock2/nf-winsock2-send).

## Find the module UART baud

**Physical result for this device: 115200 baud.** The Bluetooth receiver captured repeated intact `BAUD:115200 HELLO:126` through `HELLO:130` messages. The older 9600-baud assumption was incorrect for this module. Production firmware, the fixed HELLO diagnostic and Python serial defaults now use 115200. Upload `firmware/haath/haath.ino` after the scan, then run the Bluetooth receiver and start the cursor listener once `[VALID]` appears. The scan sketch intentionally changes speeds and will continue producing garbage during wrong-speed phases until replaced.

The ESP D6/D5 loopback and the module-only Bluetooth loopback can both pass even when their UART speeds differ. To test the baud match, upload [firmware/hc06_baud_scan/hc06_baud_scan.ino](firmware/hc06_baud_scan/hc06_baud_scan.ino). Use normal crossed wiring: ESP D6 -> HC-06 RXD, HC-06 TXD -> ESP D5, and common ground; remove both loopback jumpers with power disconnected before restoring the normal wiring.

Run `python host/bluetooth_receiver.py --raw` without `--probe`. The scan changes only the ESP UART speed, testing 9600, 19200, 38400, 57600, 115200, 4800, 2400 and 1200 baud for five seconds each, repeating about every 41 seconds. Let the receiver run for about 90 seconds so it spans full scan cycles. Look for repeated readable messages such as `BAUD:38400 HELLO:42` in the **Bluetooth terminal**, not the USB Serial Monitor. USB always prints readable labels at 115200, regardless of whether Bluetooth receives them.

The scanner sends no AT commands and does not change the HC-06 configuration. Wrong-speed phases can produce unreadable bytes; readable repeated labels identify a working candidate speed. This is a transmit-only test and does not establish the reverse UART direction. If no speed works on another module, do not assume baud is the only problem: check pin contacts, crossed wiring and power/signal levels. Production mouse firmware uses `enableIntTx(false)` to improve transmit timing at 115200; it does not need concurrent UART reception, consistent with the [library guidance](https://github.com/plerup/espsoftwareserial/blob/8.0.1/README.md).

The scanner can be checked locally with `g++ -std=c++11 -Wall -Wextra -I tests/firmware tests/firmware/test_hc06_baud_scan.cpp -o .build/test_hc06_baud_scan.exe`, then `& .build/test_hc06_baud_scan.exe`. Reinitialization uses the installed [EspSoftwareSerial 8.0.1 API](https://github.com/plerup/espsoftwareserial/blob/8.0.1/src/SoftwareSerial.h).

The scanner simulation and ESP8266 object compilation passed locally; the user identified the working module speed over the physical Bluetooth connection.

## Firmware and protocol

- **firmware/haath/haath.ino:** production mouse, reads gyro X/Z for pitch/yaw, subtracts startup bias, applies a 1.8 degree/s deadzone and a 1.25 acceleration exponent.
- **firmware/hc06_test/hc06_test.ino:** separate diagnostic sketch, sends `HELLO:<count>` every 500 ms. Upload this instead of the production sketch when isolating the Bluetooth link; it does not control the mouse.
- **firmware/hc06_baud_scan/hc06_baud_scan.ino:** separate labelled UART-speed scan, with no HC-06 configuration writes.
- No buttons are connected or read. Leave **D7/GPIO13** and **D3/GPIO0** unused. Motion needs no activation button; this device currently provides cursor movement only.
- Production packets: ASCII `dx,dy,0,0\n`, sent at **50 Hz**, including `0,0,0,0\n` at rest. The two zero fields preserve compatibility with the existing receiver and never request a click. UART is **115200 baud, 8N1**, matching the observed module setting. The 20 ms interval leaves bandwidth headroom for full-scale packets.
- No packet is transmitted after a failed sensor read. The host watchdog therefore detects a stalled/disconnected sensor as well as a stalled radio link.

## Checks

The Python suite uses the standard library and the installed pyserial dependency. It mocks mouse events, so it does not move your cursor or open Bluetooth/COM devices. On Windows it also checks the native bindings with a local socket pair.

```powershell
python -m unittest discover -s tests -v
```

The firmware behavior test runs the actual production sketch against simulated I2C, UART and time, without a button API. It requires a C++11 compiler:

```powershell
New-Item -ItemType Directory -Force .build | Out-Null
g++ -std=c++11 -Wall -Wextra -I tests/firmware tests/firmware/test_haath.cpp -o .build/test_haath.exe
& .build/test_haath.exe
```

With Arduino CLI and ESP8266 core 3.1.2 installed, build each sketch separately. These commands assume NodeMCU hardware; use `esp8266:esp8266:d1_mini` for a D1 mini:

```powershell
arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 firmware/haath
arduino-cli compile --fqbn esp8266:esp8266:nodemcuv2 firmware/hc06_test
```

The simulated tests do not prove the physical wiring, radio connection, battery supply, or cursor feel. Verify those on the device after uploading.

The Python and simulated firmware checks do not operate the physical mouse. They cannot verify wiring, the radio connection, battery operation, or cursor feel; check those on the assembled device after uploading.
