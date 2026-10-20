// Standalone HC-06 link diagnostic (upload instead of the air mouse sketch).
// Minimal diagnostic test: sends "HELLO:<count>\n" every 500ms via SoftwareSerial to HC-06
// Also echoes back anything received from HC-06 over USB serial.

#include <SoftwareSerial.h>

#define PIN_BLE_RX 14  // D5 (GPIO14) <- HC-06 TXD
#define PIN_BLE_TX 12  // D6 (GPIO12) -> HC-06 RXD

SoftwareSerial hc06(PIN_BLE_RX, PIN_BLE_TX);

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== HC-06 UART TEST ===");
  Serial.println("Sending HELLO every 500ms on SoftwareSerial D6 -> HC-06 RXD");
  Serial.println("Connect via Bluetooth COM port or RFCOMM socket to check incoming bytes.");

  hc06.begin(115200);
  Serial.println("SoftwareSerial started at 115200 baud (verified by baud scan).");
}

unsigned long lastSend = 0;
int count = 0;

void loop() {
  if (millis() - lastSend > 500) {
    lastSend = millis();
    count++;
    String msg = "HELLO:" + String(count) + "\n";
    hc06.print(msg);
    Serial.print("[USB] Sent to HC-06: ");
    Serial.print(msg);
  }

  if (hc06.available()) {
    char c = hc06.read();
    Serial.print("[HC06->USB] RX: 0x");
    Serial.println((int)c, HEX);
  }
}
