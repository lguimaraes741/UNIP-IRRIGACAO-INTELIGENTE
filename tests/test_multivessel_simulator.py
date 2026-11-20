import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path
from tempfile import TemporaryDirectory
import test_simulator  # Disponibiliza tools no sys.path ao rodar discover.
from device_simulator import MultiVesselSimulator


class MultiVesselTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.device = MultiVesselSimulator()
        self.device.resync(self.now)

    def command(self, index=1, **changes):
        value = dict(command_id=str(uuid4()),device_id='sim-01',source='simulation',
                     experiment_id='sim-three-pots',unit_id=f'sim-pot-0{index}',treatment=f'T{index}',
                     issued_at=self.now.isoformat(),expires_at=(self.now+timedelta(seconds=100)).isoformat(),
                     target_volume_ml=10,max_duration_s=10)
        value.update(changes)
        return value

    def test_serial_execution_waits_stabilization_and_measures_shared_water(self):
        first, second = self.command(1), self.command(2)
        self.assertEqual(self.device.receive(first,self.now)[-1]['status'],'started')
        self.assertEqual(self.device.receive(second,self.now)[0]['reason'],'shared_executor_busy')
        self.assertEqual(self.device.tick(self.now+timedelta(seconds=2)),[])
        self.assertFalse(self.device.pump_on)
        self.assertEqual(self.device.receive(second,self.now+timedelta(seconds=2))[0]['reason'],'shared_executor_busy')
        complete = self.device.tick(self.now+timedelta(seconds=3))[0]
        self.assertEqual(complete['unit_id'],'sim-pot-01')
        self.assertTrue(complete['measurements_valid'])
        self.assertEqual(self.device.reservoir_ml,1490)
        self.assertEqual(self.device.receive(second,self.now+timedelta(seconds=3))[-1]['status'],'started')
        self.device.tick(self.now+timedelta(seconds=6))
        complete2 = self.device.tick(self.now+timedelta(seconds=7))[0]
        self.assertEqual(complete2['unit_id'],'sim-pot-02')
        self.assertEqual(complete2['treatment'],'T2')
        self.assertNotEqual(complete['energy_wh'],complete2['energy_wh'])
        self.assertEqual(self.device.reservoir_ml,1480)
        self.assertEqual(self.device.receive(first,self.now+timedelta(seconds=8))[0]['reason'],'duplicate')

    def test_context_isolation_and_refill(self):
        self.device.units['sim-pot-02']['sensor_valid']=False
        self.assertEqual(self.device.receive(self.command(2),self.now)[0]['reason'],'invalid_sensor')
        self.assertEqual(self.device.receive(self.command(1),self.now)[-1]['status'],'started')
        event = self.device.refill(2000,self.now+timedelta(seconds=1))[0]
        self.assertEqual(event['reason'],'refill_during_cycle')
        self.assertFalse(event['measurements_valid'])
        self.assertNotIn('volume_ml',event)
        self.assertFalse(self.device.pump_on)
        self.assertEqual(self.device.reservoir_ml,2000)
        self.assertEqual(self.device.receive(self.command(3,treatment='T1'),self.now)[0]['reason'],'context')

    def test_disconnection_has_one_fallback_cycle_and_safe_resync(self):
        self.device.disconnect()
        self.device.fallback_enabled=True
        events=self.device.fallback(self.now)
        self.assertEqual(events[0]['unit_id'],'sim-pot-01')
        self.assertEqual(events[0]['origin'],'local')
        self.assertFalse(self.device.resync(self.now))
        self.device.tick(self.now+timedelta(seconds=5))
        self.assertFalse(self.device.resync(self.now+timedelta(seconds=5)))
        self.device.tick(self.now+timedelta(seconds=6))
        self.assertTrue(self.device.resync(self.now+timedelta(seconds=6)))
        self.assertEqual(self.device.fallback(self.now+timedelta(seconds=6)),[])

    def test_reboot_keeps_shared_reservoir_and_journal(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            path=Path(directory)/'state.json'
            first=MultiVesselSimulator(path)
            first.resync(self.now)
            command=self.command(2)
            first.receive(command,self.now)
            first.tick(self.now+timedelta(seconds=3))
            first.tick(self.now+timedelta(seconds=4))
            reboot=MultiVesselSimulator(path)
            self.assertFalse(reboot.pump_on)
            self.assertEqual(reboot.reservoir_ml,1490)
            reboot.resync(self.now)
            self.assertEqual(reboot.receive(command,self.now)[0]['reason'],'duplicate')

    def test_refill_and_stop_during_stabilization_do_not_publish_valid_metrics(self):
        self.device.receive(self.command(),self.now)
        self.device.tick(self.now+timedelta(seconds=2))
        event=self.device.refill(1600,self.now+timedelta(seconds=2.1))[0]
        self.assertFalse(event['measurements_valid'])
        self.assertIsNone(event['volume_ml'])
        self.device.receive(self.command(),self.now+timedelta(seconds=3))
        self.device.tick(self.now+timedelta(seconds=5))
        event=self.device.stop(self.now+timedelta(seconds=5.1))[0]
        self.assertEqual(event['reason'],'stopped_before_stabilization')
        self.assertFalse(event['measurements_valid'])

    def test_reboot_during_settling_restores_result_without_actuation(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            path=Path(directory)/'state.json'
            first=MultiVesselSimulator(path)
            first.resync(self.now)
            command=self.command(3)
            first.receive(command,self.now)
            first.tick(self.now+timedelta(seconds=4))
            reboot=MultiVesselSimulator(path)
            self.assertFalse(reboot.pump_on)
            self.assertFalse(reboot.resync(self.now+timedelta(seconds=4)))
            result=reboot.tick(self.now+timedelta(seconds=5))[0]
            self.assertEqual(result['command_id'],command['command_id'])
            self.assertEqual(result['treatment'],'T3')
            self.assertEqual(result['unit_id'],'sim-pot-03')
            self.assertEqual(result['volume_ml'],10)
            self.assertEqual(reboot.buffer[-1]['payload']['event_id'],result['event_id'])
            self.assertFalse(reboot.pump_on)
            reboot.resync(self.now+timedelta(seconds=5))
            self.assertEqual(reboot.receive(command,self.now+timedelta(seconds=5))[0]['reason'],'duplicate')

    def test_fallback_round_robin_and_cursor_persistence(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            path=Path(directory)/'state.json'
            device=MultiVesselSimulator(path)
            device.fallback_enabled=True
            for state in device.units.values():
                state['soil_index']=20
            self.assertEqual(device.fallback(self.now)[0]['unit_id'],'sim-pot-01')
            device.tick(self.now+timedelta(seconds=5))
            device.tick(self.now+timedelta(seconds=6))
            reboot=MultiVesselSimulator(path)
            reboot.fallback_enabled=True
            reboot.units['sim-pot-01']['fallback_latched']=False
            self.assertEqual(reboot.fallback(self.now+timedelta(seconds=7))[0]['unit_id'],'sim-pot-02')
