#pragma once
#include "Arduino.h"
#include <vector>

struct SoftwareSerial {
  std::vector<std::string> packets;
  int baud = 0;
  bool interruptibleTx = true;
  SoftwareSerial(int, int) {}
  void begin(int speed) { baud = speed; }
  void enableIntTx(bool enabled) { interruptibleTx = enabled; }
  void end() { baud = 0; }
  void print(const char* packet) { packets.emplace_back(packet); }
};
