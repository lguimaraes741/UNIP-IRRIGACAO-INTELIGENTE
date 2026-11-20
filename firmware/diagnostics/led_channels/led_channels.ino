// Standalone Arduino diagnostic, NOT the ESP-IDF irrigation application.
// Only upload with ALL motors disconnected; one LED + resistor is the load.
// No Wi-Fi credentials, EEPROM changes, tara, calibration or motor control.
#include <Arduino.h>
#include "bench_pins.hpp" // Build with firmware/core on the compiler include path.

bool armed = false;
int selected = -1;
uint32_t started = 0;
uint32_t armedAt = 0;
constexpr uint32_t pulseMs = 1000;
constexpr uint32_t sessionMs = 60000;
char input[24];
size_t used = 0;
bool overflow = false;

void offAll() {
  for (int pin : bench::output_pins) digitalWrite(pin, LOW);
  selected = -1;
}

void command() {
  input[used] = '\0';
  offAll();
  if (overflow) {
    armed = false;
    Serial.println("Entrada longa: bloqueado.");
  } else if (strcmp(input, "LED_ONLY") == 0) {
    armed = true;
    armedAt = millis();
    Serial.println("LED somente: sessao 60 s. Envie 1, 2 ou 3; OFF desarma.");
  } else if (strcmp(input, "OFF") == 0) {
    armed = false;
    Serial.println("Saidas LOW; desarmado.");
  } else if (armed && used == 1 && input[0] >= '1' && input[0] <= '3') {
    selected = input[0] - '1';
    digitalWrite(bench::output_pins[selected], HIGH);
    started = millis();
    Serial.printf("GPIO%d HIGH por no maximo 1000 ms. Observe o LED.\n",
                  bench::output_pins[selected]);
  } else {
    armed = false;
    Serial.println("Bloqueado. Motores desconectados? Envie LED_ONLY para teste.");
  }
  used = 0;
  overflow = false;
}

void setup() {
  // LOW is a diagnostic state, NOT a verified motor-OFF polarity.
  for (int pin : bench::output_pins) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  Serial.begin(115200);
  Serial.println("DIAGNOSTICO LED; sem bombas. Desarmado. Envie LED_ONLY.");
}

void loop() {
  const uint32_t now = millis();
  if (armed && uint32_t(now - armedAt) >= sessionMs) {
    offAll();
    armed = false;
    Serial.println("Sessao expirada; desarmado.");
  }
  if (selected >= 0 && uint32_t(now - started) >= pulseMs) {
    offAll();
    Serial.println("Pulso concluido; todas as saidas LOW.");
  }
  // One byte per loop: serial traffic cannot starve pulse expiration checks.
  if (Serial.available()) {
    const char c = char(Serial.read());
    if (c == '\n') command();
    else if (c != '\r') {
      if (used + 1 < sizeof(input)) input[used++] = c;
      else overflow = true;
    }
  }
}
