#include <cassert>
#include <limits>
#include "../diagnostics/led_channels/led_channels.ino"

static void send(const std::string& text) {
  for (char c : text + "\n") Serial.bytes.push_back(c);
  while (Serial.available()) loop();
}
static void allLow() {
  for (int pin : bench::output_pins) assert(levels[pin] == LOW);
}
int main() {
  setup(); allLow(); assert(!armed);
  assert(bench::load_sensor_count == 4);
  assert(bench::soil_labels[0] == 'B' && bench::soil_pins[0] == 32);
  send("1"); allLow(); assert(!armed);
  send("LED_ONLY"); allLow(); assert(armed);
  send("1"); assert(levels[25] == HIGH && levels[26] == LOW && levels[27] == LOW);
  fakeMillis = 999; loop(); assert(levels[25] == HIGH);
  fakeMillis = 1000; loop(); allLow();
  send("2"); assert(levels[26] == HIGH);
  send("3"); assert(levels[27] == HIGH && levels[26] == LOW && levels[25] == LOW);
  send("OFF"); allLow(); assert(!armed);
  send("LED_ONLY"); send(std::string(80, 'x')); allLow(); assert(!armed);
  send("LED_ONLY"); send("invalid"); allLow(); assert(!armed);
  send("LED_ONLY"); fakeMillis += sessionMs; send("1"); allLow(); assert(!armed);
  fakeMillis = std::numeric_limits<uint32_t>::max() - 500;
  send("LED_ONLY"); send("1");
  fakeMillis += 1000; loop(); allLow(); // millis rollover
  setup(); allLow();
}
