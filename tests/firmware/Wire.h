#pragma once
#include "Arduino.h"

struct WireMock {
  int failures = 0, shortReads = 0, reads = 0, index = 0;
  bool writeFails = false;
  int16_t raw[3] = {262, -131, 393};
  void begin(int, int) {}
  void setClock(int) {}
  void beginTransmission(int) {}
  void write(uint8_t) {}
  int endTransmission(bool stop = true) {
    if (stop) return writeFails ? 2 : 0;
    reads++;
    if (failures > 0) { failures--; return 2; }
    return 0;
  }
  int requestFrom(int, int count) {
    index = 0;
    if (shortReads > 0) { shortReads--; return 4; }
    return count;
  }
  int read() {
    uint16_t value = static_cast<uint16_t>(raw[index / 2]);
    return index++ % 2 == 0 ? value >> 8 : value & 255;
  }
};
static WireMock Wire;
