#include <Wire.h>
#include <SoftwareSerial.h>

// --- PIN DEFINITIONS ---
#define PIN_BLE_RX          14  // D5 (GPIO14) -> HC-06 TXD
#define PIN_BLE_TX          12  // D6 (GPIO12) -> HC-06 RXD

// --- AIR MOUSE TUNING ---
#define SENSITIVITY         0.70f // Mouse speed scaling
#define DEADZONE            1.80f // Deg/s deadzone threshold to stop jitter
#define ACCEL_EXPONENT      1.25f // Smooth precision + fast travel

SoftwareSerial ble(PIN_BLE_RX, PIN_BLE_TX);

float biasX = 0.0f;
float biasY = 0.0f;
float biasZ = 0.0f;
bool ready = false;

bool readGyro(int16_t raw[3]) {
  Wire.beginTransmission(0x68);
  Wire.write(0x43);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(0x68, 6) != 6) return false;
  for (int axis = 0; axis < 3; axis++) {
    uint8_t high = Wire.read();
    uint8_t low = Wire.read();
    raw[axis] = (int16_t)((high << 8) | low);
  }
  return true;
}

bool writeRegister(uint8_t address, uint8_t value) {
  Wire.beginTransmission(0x68);
  Wire.write(address);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool calibrate() {
  Serial.println("\n[+] Calibrating Gyro... Keep still on desk for 2 seconds!");
  float sumX = 0, sumY = 0, sumZ = 0;

  int samples = 0;
  for (int attempt = 0; attempt < 400 && samples < 200; attempt++) {
    int16_t raw[3];
    if (readGyro(raw)) {
      sumX += (float)raw[0] / 65.5f;
      sumY += (float)raw[1] / 65.5f;
      sumZ += (float)raw[2] / 65.5f;
      samples++;
    }
    delay(10);
  }

  if (samples < 200) {
    Serial.printf("[ERROR] Only %d valid gyro samples. Check wiring and reset.\n", samples);
    return false;
  }

  biasX = sumX / samples;
  biasY = sumY / samples;
  biasZ = sumZ / samples;

  Serial.println("[✓] Gyro Calibration Complete!");
  Serial.printf("    Offsets (deg/s) -> X: %.2f | Y: %.2f | Z: %.2f\n", biasX, biasY, biasZ);
  return true;
}

void setup() {
  Serial.begin(115200);
  ble.begin(115200); // Working HC-06 UART baud identified by the Bluetooth scan.
  ble.enableIntTx(false); // Transmit-only mouse: improve bit timing at 115200 baud.

  Wire.begin(4, 5); // SDA = D2 (GPIO4), SCL = D1 (GPIO5)
  Wire.setClock(100000);

  Serial.println("\n=======================================================");
  Serial.println("  ESP8266 + MPU6050 + HC-06 AIR MOUSE ONLINE");
  Serial.println("[BUILD] AIR_MOUSE_ACTIVE_115200_V1 | UART=115200 | 50Hz | no buttons");
  Serial.println("=======================================================");

  // Wake the sensor, set +/- 500 deg/s, and select DLPF configuration 3.
  if (!writeRegister(0x6B, 0x00) || !writeRegister(0x1B, 0x08) ||
      !writeRegister(0x1A, 0x03)) {
    Serial.println("[ERROR] MPU6050 setup failed. Check wiring and reset.");
    return;
  }
  delay(100);
  ready = calibrate();
  if (!ready) return;
  Serial.println("\n[✓] Air Mouse ACTIVE! Move the sensor to glide your cursor.");
}

unsigned long lastSend = 0;
unsigned long packetsSent = 0;

void loop() {
  if (!ready) {
    delay(100);
    return;
  }

  // Send the full mouse state at 50 Hz, including idle heartbeat packets.
  if (millis() - lastSend < 20) return;
  lastSend = millis();

  int16_t raw[3];
  if (!readGyro(raw)) return; // Do not keep a failed sensor connection alive.
  int16_t rawX = raw[0], rawY = raw[1], rawZ = raw[2];

  // Convert to deg/s and subtract calibrated bias
  // Yaw (turn left/right) = Gyro Z -> Mouse X
  // Pitch (tilt up/down)  = Gyro X -> Mouse Y
  float rateX = -(((float)rawZ / 65.5f) - biasZ);
  float rateY = -(((float)rawX / 65.5f) - biasX);

  // Apply deadzone threshold
  if (abs(rateX) < DEADZONE) rateX = 0.0f;
  if (abs(rateY) < DEADZONE) rateY = 0.0f;

  int moveX = 0;
  int moveY = 0;

  if (rateX != 0.0f) {
    float magX = pow(abs(rateX), ACCEL_EXPONENT) * SENSITIVITY;
    moveX = (rateX > 0) ? (int)(magX + 0.5f) : -(int)(magX + 0.5f);
  }
  if (rateY != 0.0f) {
    float magY = pow(abs(rateY), ACCEL_EXPONENT) * SENSITIVITY;
    moveY = (rateY > 0) ? (int)(magY + 0.5f) : -(int)(magY + 0.5f);
  }

  // Motion is always active; zero button fields preserve host compatibility.
  char packet[32];
  snprintf(packet, sizeof(packet), "%d,%d,0,0\n", moveX, moveY);
  ble.print(packet);    // Wireless stream over Bluetooth to PC
  Serial.print(packet); // USB Debug stream
  packetsSent++;

  // Periodic heartbeat
  static unsigned long lastHeartbeat = 0;
  if (millis() - lastHeartbeat > 2000) {
    lastHeartbeat = millis();
    Serial.printf("[Heartbeat] Build=AIR_MOUSE_ACTIVE_115200_V1 | Packets: %lu | Gyro (deg/s): X=%.1f, Y=%.1f, Z=%.1f\n",
                  packetsSent, (float)rawX / 65.5f, (float)rawY / 65.5f, (float)rawZ / 65.5f);
  }
}
