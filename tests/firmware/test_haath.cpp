// Exercise the actual sketch with simulated I2C, clock and UART; no button API.
#include "../../firmware/haath/haath.ino"

int main() {
  Wire.failures = 100;
  assert(calibrate());
  assert(Wire.reads == 300);
  assert(std::abs(biasX - 4.0f) < 0.001f);
  assert(std::abs(biasY + 2.0f) < 0.001f);
  assert(std::abs(biasZ - 6.0f) < 0.001f);

  Wire = WireMock();
  Wire.shortReads = 50;
  assert(calibrate());
  assert(Wire.reads == 250);

  Wire = WireMock();
  Wire.failures = 400;
  assert(!calibrate());
  Wire = WireMock();
  Wire.writeFails = true;
  ready = false;
  setup();
  assert(!ready);
  ble.packets.clear();
  loop();
  assert(ble.packets.empty());

  Wire = WireMock();
  setup();
  assert(ready);
  assert(ble.baud == 115200);
  assert(!ble.interruptibleTx);
  ble.packets.clear();
  packetsSent = 0;
  lastSend = fakeMillis;
  for (int frame = 0; frame < 100; frame++) {
    fakeMillis += 20;
    loop();
  }
  assert(packetsSent == 100);
  assert(ble.packets.size() == 100);
  for (const auto& packet : ble.packets) {
    assert(packet == "0,0,0,0\n"); // Two seconds of idle output, without movement or buttons.
  }
  fakeMillis += 20;
  loop();
  assert(ble.packets.back() == "0,0,0,0\n");
  Wire.raw[2] = 655; // Rotation produces motion without any activation input.
  fakeMillis += 20;
  loop();
  assert(ble.packets.back() == "-4,0,0,0\n");
  Wire.raw[2] = 393;
  auto count = ble.packets.size();
  fakeMillis += 20;
  loop();
  assert(ble.packets.size() == count + 1); // Idle state is also a heartbeat.
  assert(ble.packets.back() == "0,0,0,0\n");

  count = ble.packets.size();
  Wire.failures = 1;
  fakeMillis += 20;
  loop();
  assert(ble.packets.size() == count); // Bad sensor data cannot refresh the host watchdog.
  puts("Firmware behavior checks passed.");
}
