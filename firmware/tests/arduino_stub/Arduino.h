#pragma once
// Host-only adapter. Does not simulate electrical behaviour or an ESP32 runtime.
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <deque>
#include <string>
constexpr int LOW = 0, HIGH = 1, OUTPUT = 1;
inline uint32_t fakeMillis = 0;
inline int levels[40] = {};
inline uint32_t millis() { return fakeMillis; }
inline void digitalWrite(int pin, int value) { levels[pin] = value; }
inline void pinMode(int, int) {}
struct FakeSerial {
  std::deque<char> bytes;
  void begin(int) {}
  void println(const char*) {}
  template<class... Args> void printf(const char*, Args...) {}
  int available() { return !bytes.empty(); }
  int read() { char c = bytes.front(); bytes.pop_front(); return c; }
} inline Serial;
