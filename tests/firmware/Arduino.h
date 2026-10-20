#pragma once
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>

static unsigned long fakeMillis = 0;
inline unsigned long millis() { return fakeMillis; }
inline void delay(unsigned long ms) { fakeMillis += ms; }

struct SerialMock {
  void begin(int) {}
  void println(const char*) {}
  void print(const char*) {}
  template<typename... Args> void printf(const char*, Args...) {}
};
static SerialMock Serial;
