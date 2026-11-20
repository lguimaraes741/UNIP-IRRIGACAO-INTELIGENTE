#pragma once
#include <array>

// User test sketch + physical soil identification, 2026-11-01.
// A GPIO assignment is not approval of the motor circuit or voltage levels.
namespace bench {
// Four three-wire load sensors share HX711 channel A; no per-sensor readings.
constexpr int load_sensor_count = 4;
constexpr int hx_dout = 5;
constexpr int hx_sck = 4;
constexpr int i2c_sda = 21;
constexpr int i2c_scl = 22;
constexpr int ina_address = 0x40;
constexpr int dht_data = 18;
// 13/11/2026: DHT22 VCC moved by the user from 3V3 to GPIO13 so firmware can power-cycle it
// (motor noise latches the sensor; only a power loss recovered it on 10-11/11).
constexpr int dht_power = 13;
constexpr std::array<int, 3> soil_pins{{32, 33, 34}};
constexpr std::array<char, 3> soil_labels{{'B', 'A', 'C'}};
constexpr std::array<int, 3> output_pins{{25, 26, 27}};
constexpr int verified_led_output = 26; // User observed pulses; original LED connector.
constexpr std::array<int, 3> led_tested_connector_order{{26, 25, 27}};
// Connector order is the user's test order, not physical left-to-right order.
// 11/11/2026 dry pulses: output n -> INA3221 channel n; GPIO25 -> pump 3, GPIO26 -> pump 2,
// GPIO27 -> pump 1 (user-observed labels). User assignment: pump n -> pot-0n (Tn), so
// pot-01/T1 = output 3 (CH3), pot-02/T2 = output 2 (CH2), pot-03/T3 = output 1 (CH1); provisioned out*/ina*.
// Never infer those associations from array ordering.
constexpr bool motor_circuit_validated = false;
}
