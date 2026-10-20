// Diagnose the HC-06's UART speed without sending AT commands or changing it.
#include <SoftwareSerial.h>

#define PIN_BLE_RX 14  // D5 <- HC-06 TXD
#define PIN_BLE_TX 12  // D6 -> HC-06 RXD

SoftwareSerial hc06(PIN_BLE_RX, PIN_BLE_TX);
const uint32_t BAUDS[] = {9600, 19200, 38400, 57600, 115200, 4800, 2400, 1200};
const unsigned long DWELL_MS = 5000;
uint8_t baudIndex = 0;
unsigned long changedAt = 0, lastSend = 0, sequence = 0;

void selectBaud() {
  hc06.end();
  hc06.begin(BAUDS[baudIndex]);
  delay(100);
  changedAt = millis();
  lastSend = changedAt - 500;
  Serial.printf("[SCAN] Testing HC-06 UART at %lu baud for 5 seconds\n",
                (unsigned long)BAUDS[baudIndex]);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== HC06_BAUD_SCAN v1 ===");
  Serial.println("Normal crossed UART wiring required; remove all loopback jumpers.");
  Serial.println("Run Bluetooth receiver --raw, without --probe. Scan repeats every ~41 seconds.");
  selectBaud();
}

void loop() {
  unsigned long now = millis();
  if (now - changedAt >= DWELL_MS) {
    baudIndex = (baudIndex + 1) % (sizeof(BAUDS) / sizeof(BAUDS[0]));
    selectBaud();
    now = millis();
  }
  if (now - lastSend < 500) {
    delay(1);
    return;
  }
  lastSend = now;
  char message[48];
  // Leading newline separates any garbage from the preceding wrong-speed test.
  snprintf(message, sizeof(message), "\nBAUD:%lu HELLO:%lu\n",
           (unsigned long)BAUDS[baudIndex], ++sequence);
  hc06.print(message);
  Serial.print(message);
}
