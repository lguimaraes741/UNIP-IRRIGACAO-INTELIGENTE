#pragma once
#include "sensor_math.hpp"
#include <array>
#include <cstdint>
#include <optional>
#include <cmath>

// Physical bench acquisition (sensors only). Pump outputs are only ever driven LOW
// here; actuation remains blocked until the power circuit is validated.
namespace hardware {
struct Snapshot {
 int64_t now_ms=0;
 // Scale: median/span of the last window, in HX711_ADC offset-binary counts.
 bool scale_fresh=false;int32_t scale_counts=0,scale_span=0;int64_t scale_age_ms=-1;
 uint32_t scale_samples=0,scale_faults=0;
 std::array<int,3> soil_raw{{-1,-1,-1}};int64_t soil_at_ms=-1; // GPIO32, 33, 34.
 bool ina_ok=false;std::array<std::optional<irrigation::PowerReading>,3> power;int64_t power_at_ms=-1;
 std::optional<irrigation::Climate> climate;int64_t climate_at_ms=-1;uint32_t climate_faults=0,climate_streak=0,climate_resets=0;
};
// Manual pump pulses: INA3221 sampled every ~100 ms while active; peaks/minimum since the last take.
struct PowerPeaks {std::array<double,3> peak_current_a{{NAN,NAN,NAN}},min_bus_v{{NAN,NAN,NAN}};uint32_t samples=0;};
void power_fast(bool on);
// Pumps running (manual pulse or cycle): DHT22 reads are skipped until 2 s after they stop.
void pumps_active(bool on);
PowerPeaks take_peaks();
void outputs_low();     // First action at boot: GPIO25/26/27 LOW (LED test state, not validated motor-off).
void start();           // Sensor task pinned to the application core.
Snapshot snapshot();
}
