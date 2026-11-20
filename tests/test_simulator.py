import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from device_simulator import DeviceSimulator


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.device = DeviceSimulator()
        self.device.resync()

    def command(self, **changes):
        command = dict(command_id=str(uuid4()), device_id='sim-01', source='simulation',
                       unit_id='sim-pot-01', experiment_id='sim-integration', treatment='T1',
                       issued_at=self.now.isoformat(), expires_at=(self.now+timedelta(seconds=20)).isoformat(),
                       target_volume_ml=25, max_duration_s=10)
        command.update(changes)
        return command

    def test_complete_measurement_and_duplicate(self):
        command = self.command()
        self.assertEqual([e['status'] for e in self.device.receive(command, self.now)], ['accepted', 'started'])
        self.assertEqual(self.device.receive(command, self.now)[0]['reason'], 'duplicate')
        event = self.device.tick(self.now+timedelta(seconds=6))[0]
        self.assertEqual(event['status'], 'completed')
        self.assertEqual(event['volume_ml'], 25)
        self.assertAlmostEqual(event['energy_wh'], 3*5/3600)
        self.assertFalse(self.device.pump_on)
        self.assertEqual(self.device.reservoir_ml, 1475)

    def test_rejections(self):
        for change, expected in [({'expires_at': self.now.isoformat()}, 'expired'),
                                 ({'target_volume_ml': 101}, 'volume_limit'),
                                 ({'max_duration_s': 31}, 'duration_limit'),
                                 ({'source': 'physical'}, 'identity')]:
            with self.subTest(expected=expected):
                self.assertEqual(self.device.receive(self.command(**change), self.now)[0]['reason'], expected)
                self.assertFalse(self.device.pump_on)
        self.device.sensor_valid = False
        self.assertEqual(self.device.receive(self.command(), self.now)[0]['reason'], 'invalid_sensor')
        self.device.sensor_valid = True
        self.device.reservoir_ml = 10
        self.assertEqual(self.device.receive(self.command(), self.now)[0]['reason'], 'insufficient_water')

    def test_duration_and_invalid_sensor_stop(self):
        self.device.receive(self.command(target_volume_ml=100, max_duration_s=2), self.now)
        event = self.device.tick(self.now+timedelta(seconds=100))[0]
        self.assertEqual(event['status'], 'failed')
        self.assertEqual(event['volume_ml'], 10)
        self.assertFalse(self.device.pump_on)
        self.device.receive(self.command(), self.now)
        self.device.sensor_valid = False
        self.assertEqual(self.device.tick(self.now)[0]['reason'], 'invalid_sensor')
        self.assertFalse(self.device.pump_on)

    def test_reboot_retains_journal_and_buffer(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory)/'state.json'
            first = DeviceSimulator(path, buffer_size=2)
            first.resync()
            command = self.command()
            first.receive(command, self.now)
            for n in range(3):
                first.queue('telemetry', {'n': n})
            rebooted = DeviceSimulator(path, buffer_size=2)
            self.assertFalse(rebooted.pump_on)
            rebooted.resync()
            self.assertEqual(rebooted.receive(command, self.now)[0]['reason'], 'duplicate')
            self.assertEqual(list(rebooted.buffer), [{'topic': 'telemetry', 'payload': {'n': 1}}, {'topic': 'telemetry', 'payload': {'n': 2}}])
            self.assertEqual(rebooted.dropped, 1)

    def test_fallback_and_resync(self):
        self.device.disconnect()
        self.device.fallback_enabled = True
        self.assertEqual(self.device.receive(self.command(), self.now)[0]['reason'], 'resync_required')
        self.assertEqual(len(self.device.fallback(self.now)), 2)
        self.assertFalse(self.device.resync())
        self.device.tick(self.now+timedelta(seconds=5))
        self.assertEqual(self.device.fallback(self.now), [])
        self.device.soil_index = 50
        self.device.fallback(self.now)
        self.device.soil_index = 20
        self.assertEqual(len(self.device.fallback(self.now)), 2)
        self.device.tick(self.now+timedelta(seconds=5))
        self.assertTrue(self.device.resync())
        self.assertEqual(self.device.fallback(self.now), [])

    def test_journal_full_fails_closed(self):
        self.device.journal_capacity = 1
        self.device.receive(self.command(expires_at=self.now.isoformat()), self.now)
        self.assertEqual(self.device.receive(self.command(), self.now)[0]['reason'], 'journal_full')

    def test_old_command_after_reconnection(self):
        self.device.resync(self.now+timedelta(seconds=1))
        self.assertEqual(self.device.receive(self.command(), self.now+timedelta(seconds=2))[0]['reason'], 'before_resync')

    def test_context_and_explicit_stop(self):
        for changes in [{'unit_id':'other'}, {'experiment_id':'sim-other'}, {'treatment':'T2'}]:
            self.assertEqual(self.device.receive(self.command(**changes), self.now)[0]['reason'], 'context')
        command = self.command()
        self.device.receive(command, self.now)
        event = self.device.stop(self.now+timedelta(seconds=1))[0]
        self.assertEqual(event['status'], 'failed')
        self.assertFalse(event['measurements_valid'])
        self.assertNotIn('volume_ml', event)
        self.assertFalse(self.device.pump_on)
        self.assertEqual(self.device.receive(command, self.now)[0]['reason'], 'duplicate')

    def test_disconnect_buffer_reconnect_preserves_original_timestamp(self):
        self.device.disconnect()
        payload = {'collected_at':self.now.isoformat(), 'message_id':str(uuid4())}
        self.device.queue('telemetry', payload)
        self.device.resync(self.now+timedelta(seconds=10))
        self.assertEqual(self.device.buffer[0]['payload'], payload)
        self.device.acknowledge_buffer()
        self.assertEqual(len(self.device.buffer), 0)
