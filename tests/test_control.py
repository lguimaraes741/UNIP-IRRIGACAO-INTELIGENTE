import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.contracts import Telemetry, DecisionConfiguration, Prediction, IrrigationCommand, CommandEvent
from app.control import decide
from pydantic import ValidationError


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.config = DecisionConfiguration(device_id='sim-01',experiment_id='sim-test',unit_id='pot-01',treatment='T2',source='simulation',enabled=True,calibration_id='sim-v1',soil_low=30,soil_high=60,min_reservoir_mass_g=100,target_volume_ml=10,max_duration_s=5,max_telemetry_age_s=60,cooldown_s=60)
        self.telemetry = Telemetry(schema_version=1,message_id=uuid4(),device_id='sim-01',experiment_id='sim-test',unit_id='pot-01',treatment='T2',source='simulation',collected_at=self.now,calibration_id='sim-v1',soil_raw=2500,soil_index=20,sensor_valid=True,reservoir_mass_g=1000)

    def run_policy(self, **kwargs):
        return decide(self.config,self.telemetry,self.now,simulation_enabled=True,**kwargs)

    def test_safe_default_and_physical_disabled(self):
        self.assertEqual(decide(self.config,self.telemetry,self.now)[1], 'control_disabled')
        self.config.source='physical'
        self.assertEqual(decide(self.config,self.telemetry,self.now)[1], 'physical_actuation_disabled')

    def test_physical_switch_and_daily_cap(self):
        import os
        from unittest.mock import patch
        from app.control import source_enabled
        with patch.dict(os.environ,{'SIMULATION_CONTROL_ENABLED':'true'},clear=False):
            os.environ.pop('PHYSICAL_CONTROL_ENABLED',None)
            self.assertFalse(source_enabled('physical'))
            self.assertTrue(source_enabled('simulation'))
        with patch.dict(os.environ,{'PHYSICAL_CONTROL_ENABLED':'true'}):
            self.assertTrue(source_enabled('physical'))
        # Physical decisions follow the same policy once enabled; absolute mass is not required.
        self.config.source=self.telemetry.source='physical'
        self.telemetry.reservoir_mass_g=None
        self.assertEqual(self.run_policy(),(True,'soil_below_low'))
        self.config.max_daily_volume_ml=300
        self.assertTrue(self.run_policy(daily_volume_ml=290)[0])
        self.assertEqual(self.run_policy(daily_volume_ml=291)[1],'daily_volume_cap')

    def test_low_and_hold(self):
        self.assertTrue(self.run_policy()[0])
        self.telemetry.soil_index=40
        self.assertEqual(self.run_policy(),(False,'hysteresis_hold'))
        self.telemetry.soil_index=60
        self.assertFalse(self.run_policy()[0])

    def test_blocks_missing_stale_calibration_reservoir_overlap(self):
        for field,value,reason in [('collected_at',self.now-timedelta(seconds=61),'stale_telemetry'),('calibration_id','other','invalid_or_uncalibrated_sensor'),('reservoir_mass_g',None,'insufficient_or_unknown_reservoir')]:
            old=getattr(self.telemetry,field)
            setattr(self.telemetry,field,value)
            self.assertEqual(self.run_policy()[1],reason)
            setattr(self.telemetry,field,old)
        self.assertEqual(self.run_policy(active_command=True)[1],'active_command')
        self.assertEqual(self.run_policy(last_cycle_at=self.now)[1],'cooldown')

    def test_t1_interval(self):
        self.config.treatment='T1'
        self.config.schedule_interval_s=3600
        self.telemetry.treatment='T1'
        self.assertTrue(self.run_policy()[0])
        self.assertEqual(self.run_policy(last_cycle_at=self.now-timedelta(seconds=100))[1],'schedule_wait')

    def test_prediction_link_and_fallback(self):
        self.config.treatment='T3'
        self.telemetry.treatment='T3'
        self.assertEqual(self.run_policy()[1],'prediction_unavailable')
        pred=Prediction(prediction_id=uuid4(),device_id='sim-01',telemetry_message_id=self.telemetry.message_id,source='simulation',predicted_at=self.now,valid_until=self.now+timedelta(seconds=60),soil_index=20,model_version='sim-v1')
        self.assertTrue(self.run_policy(prediction=pred)[0])
        pred.telemetry_message_id=uuid4()
        self.assertFalse(self.run_policy(prediction=pred)[0])
        # Latched after an anticipated start: continue until the observed index releases the latch.
        pred.telemetry_message_id=self.telemetry.message_id
        pred.soil_index=45
        self.assertEqual(self.run_policy(prediction=pred),(False,'prediction_hold'))
        self.assertEqual(self.run_policy(prediction=pred,hysteresis_latched=True),(True,'hysteresis_latched'))
        self.config.prediction_fallback_enabled=True
        self.assertEqual(self.run_policy()[1],'prediction_unavailable_local_threshold_fallback')

    def test_invalid_command_ttl_and_measurements(self):
        with self.assertRaises(ValidationError):
            IrrigationCommand(command_id=uuid4(),device_id='sim-01',experiment_id='sim-test',unit_id='pot-01',treatment='T2',source='simulation',issued_at=self.now,expires_at=self.now+timedelta(minutes=6),target_volume_ml=10,max_duration_s=5,reason='test')
        with self.assertRaises(ValidationError):
            CommandEvent(event_id=uuid4(),command_id=uuid4(),device_id='sim-01',source='simulation',occurred_at=self.now,status='completed',reason='test',volume_ml=0)

    def test_latch_low_band_high_and_configuration_reset(self):
        from app.control import hysteresis_state, configuration_key
        latch = hysteresis_state(self.config,self.telemetry)
        self.assertTrue(latch)
        self.telemetry.soil_index=45
        self.assertTrue(hysteresis_state(self.config,self.telemetry,latch))
        self.assertEqual(self.run_policy(hysteresis_latched=latch),(True,'hysteresis_latched'))
        self.telemetry.soil_index=60
        self.assertFalse(hysteresis_state(self.config,self.telemetry,latch))
        old_key=configuration_key(self.config)
        self.config.calibration_id='new-calibration'
        self.assertNotEqual(old_key,configuration_key(self.config))
        self.assertFalse(hysteresis_state(self.config,self.telemetry,False))
