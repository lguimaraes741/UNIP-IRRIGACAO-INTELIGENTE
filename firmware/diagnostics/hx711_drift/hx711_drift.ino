// Standalone Arduino diagnostic, NOT the ESP-IDF irrigation application.
// Isolates the HX711 weighing: no Wi-Fi, DHT, INA, output toggling or motors.
// Reads the stored factor (EEPROM size/address of the user's sketch) without
// writing it. No automatic tara/autozero/dead zone: only the TARA command.
// Library pinned for this test: HX711_ADC 1.2.12, default config.h (16 samples).
#include <Arduino.h>
#include <EEPROM.h>
#include <HX711_ADC.h>
#include "bench_pins.hpp" // Build with firmware/core on the compiler include path.

constexpr int eepromSize = 512;  // Same size as the user's sketch: avoids a resize write.
constexpr int calAddress = 0;
constexpr uint32_t settleMs = 3000;   // Relative to begin, unlike start(t)'s absolute t.
constexpr uint32_t reportMs = 1000;
constexpr uint32_t staleMs = 500;     // ~10 SPS expected; older data is reported invalid.
constexpr uint32_t tareLimitMs = 10000;

enum State { SETTLING, UNTARED, TARING, TARED, TARE_FAILED };
const char *stateName[] = {"ESTABILIZANDO", "SEM_TARA", "TARANDO", "TARADO", "TARA_FALHOU"};

HX711_ADC cell(bench::hx_dout, bench::hx_sck);
State state = SETTLING;
float cal = NAN;
bool calValid = false;
uint32_t begunAt = 0, lastSampleAt = 0, lastReport = 0, tareAt = 0;
uint32_t samples = 0, samplesAtReport = 0;
char input[16];
size_t used = 0;
bool overflow = false;

void header() {
  Serial.printf("# hx711_drift v1; HX711_ADC 1.2.12; SAMPLES=%d; fator=%.4f (%s); EEPROM somente leitura\n",
                cell.getSamplesInUse(), cal, calValid ? "valido" : "INVALIDO");
  Serial.println("# uptime_ms,estado,idade_amostra_ms,amostras_no_intervalo,contagens_media_lib,"
                 "gramas_media_lib,offset_tara,timeout_sinal");
}

void command() {
  input[used] = '\0';
  if (overflow) Serial.println("# entrada longa ignorada");
  else if (strcmp(input, "TARA") == 0) {
    if (state == SETTLING) Serial.println("# aguarde estabilizar antes da tara");
    else {
      cell.tareNoDelay();
      tareAt = millis();
      state = TARING;
      Serial.printf("# tara iniciada em %lu ms\n", (unsigned long)tareAt);
    }
  } else if (strcmp(input, "STATUS") == 0) header();
  else Serial.println("# comandos: TARA, STATUS");
  used = 0;
  overflow = false;
}

void report(uint32_t now) {
  const uint32_t age = now - lastSampleAt;
  const bool fresh = samples > 0 && age <= staleMs && !cell.getSignalTimeoutFlag();
  Serial.printf("%lu,%s,", (unsigned long)now, fresh ? stateName[state] : "SEM_AMOSTRA");
  if (samples > 0) Serial.printf("%lu", (unsigned long)age);
  Serial.printf(",%lu,", (unsigned long)(samples - samplesAtReport));
  samplesAtReport = samples;
  // Counts come from the library moving average (not single raw conversions).
  if (fresh && state != SETTLING && state != TARING) {
    const float grams = cell.getData();
    Serial.printf("%.0f,", grams * cell.getCalFactor() + cell.getTareOffset());
    if (state == TARED && calValid) Serial.printf("%.2f", grams);
  } else Serial.print(",");
  Serial.printf(",%ld,%d\n", cell.getTareOffset(), cell.getSignalTimeoutFlag() ? 1 : 0);
}

void setup() {
  // LOW is a diagnostic state, NOT a verified motor-OFF polarity.
  for (int pin : bench::output_pins) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  Serial.begin(115200);
  if (EEPROM.begin(eepromSize)) EEPROM.get(calAddress, cal);
  else Serial.println("# EEPROM indisponivel");
  // Sign may legitimately depend on orientation; only finite, non-zero is required.
  calValid = isfinite(cal) && fabsf(cal) > 1e-6f;
  cell.begin();
  cell.start(0, false); // No tara at boot; settling is timed below with update() running.
  cell.setCalFactor(calValid ? cal : 1.0f);
  begunAt = millis();
  header();
  Serial.println("# sem tara automatica; envie TARA apos estabilizar, com a plataforma vazia");
}

void loop() {
  const uint32_t now = millis();
  if (cell.update()) {
    lastSampleAt = now;
    samples++;
  }
  if (state == SETTLING && now - begunAt >= settleMs) state = UNTARED;
  if (state == TARING) {
    if (cell.getTareStatus()) {
      state = TARED;
      Serial.printf("# tara concluida em %lu ms; offset=%ld\n", (unsigned long)now, cell.getTareOffset());
    } else if (now - tareAt >= tareLimitMs) {
      state = TARE_FAILED;
      Serial.println("# tara sem conclusao no prazo; leituras em gramas suspensas");
    }
  }
  if (now - lastReport >= reportMs) {
    lastReport = now;
    report(now);
  }
  // One byte per loop keeps HX711 acquisition running during serial input.
  if (Serial.available()) {
    const char c = char(Serial.read());
    if (c == '\n') command();
    else if (c != '\r') {
      if (used + 1 < sizeof(input)) input[used++] = c;
      else overflow = true;
    }
  }
}
