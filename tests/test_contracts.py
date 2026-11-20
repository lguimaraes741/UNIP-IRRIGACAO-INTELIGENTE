import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import ValidationError
from app.contracts import Telemetry


class ContractTests(unittest.TestCase):
    def fixture(self, **changes):
        payload = dict(schema_version=1, message_id=str(uuid4()), device_id='sim-01',
            experiment_id='sim-test', unit_id='pot-01', treatment='T1', source='simulation',
            collected_at=datetime.now(timezone.utc).isoformat(), calibration_id='sim-v1',
            soil_raw=1800, soil_index=50, sensor_valid=True)
        payload.update(changes)
        return payload

    def test_invalid_values(self):
        for changes in [dict(soil_index=101), dict(soil_index=float('nan')),
                        dict(soil_raw=1.5), dict(sensor_valid='false'),
                        dict(collected_at='2026-01-01T00:00:00'),
                        dict(collected_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Telemetry(**self.fixture(**changes))

    def test_invalid_reading_requires_null(self):
        with self.assertRaises(ValidationError):
            Telemetry(**self.fixture(sensor_valid=False))
        item = Telemetry(**self.fixture(sensor_valid=False, soil_index=None))
        self.assertIsNone(item.soil_index)

    def test_source_separation(self):
        for changes in [dict(source='physical'), dict(experiment_id='real-experiment')]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Telemetry(**self.fixture(**changes))

    def test_physical_firmware_payload(self):
        physical = self.fixture(device_id='esp32-01', experiment_id='tcc-2026', source='physical',
            calibration_id='soil-uncalibrated', soil_raw=3329, soil_index=None, sensor_valid=False,
            reservoir_mass_g=-3.4, scale_counts=8605637, mass_stable=True, scale_span_g=0.8,
            scale_calibration_id='scale-20261104-provisorio', pump_voltage_v=5.32,
            pump_current_a=-0.0004, ina_channel=3, firmware_version='physical-telemetry-1')
        item = Telemetry(**physical)
        self.assertLess(item.reservoir_mass_g, 0)
        self.assertIsNone(Telemetry(**dict(physical, soil_raw=None)).soil_raw)
        for changes in [dict(scale_calibration_id=None), dict(ina_channel=4), dict(scale_counts=-1),
                        dict(soil_raw=None, soil_index=50, sensor_valid=True)]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Telemetry(**dict(physical, **changes))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValidationError):
            Telemetry(**self.fixture(pump_on=True))
