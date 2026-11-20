import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('analysis',ROOT/'tools/experiment_analysis.py')
analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.plan=json.loads((ROOT/'config/experiment.json').read_text())

    def test_quarter_boundary_not_replication(self):
        spans=analysis.windows(self.plan)
        self.assertEqual(spans['primeira15'][1],spans['ultima15'][0])
        report=analysis.describe(self.plan,[],[],spans['total30'][1])
        self.assertFalse(report['analysis_periods_are_replicates'])
        self.assertIsNone(report['periods']['total30']['T2']['water_saving_pct'])
        self.assertIsNone(report['periods']['total30']['T1']['observed_valid_water_ml'])

    def test_terminal_boundary_dedup_and_invalid_measurement(self):
        event=dict(event_id='1',command_id='1',unit_id='pot-01',treatment='T1',source='physical',experiment_id='tcc-2026',status='completed',occurred_at='2026-11-16T00:00:00-03:00',measurements_valid=True,volume_ml=10,energy_wh=.1)
        end=analysis.windows(self.plan)['total30'][1]
        result=analysis.describe(self.plan,[event,event],[],end)['periods']
        self.assertEqual(result['ultima15']['T1']['observed_valid_water_ml'],10)
        self.assertIsNone(result['primeira15']['T1']['observed_valid_water_ml'])
        # Water measured without pump energy still counts as water; energy stays absent.
        event['energy_wh']=None
        result=analysis.describe(self.plan,[event],[],end)['periods']['total30']['T1']
        self.assertEqual(result['observed_valid_water_ml'],10)
        self.assertIsNone(result['observed_valid_energy_wh'])
        self.assertEqual(result['cycles_without_energy'],1)
        event['measurements_valid']=False
        result=analysis.describe(self.plan,[event],[],end)['periods']['total30']['T1']
        self.assertEqual(result['invalid_cycles'],1)
        self.assertIsNone(result['observed_valid_water_ml'])

    def test_reject_shortened_or_replicated_protocol(self):
        self.plan['ends_at']='2026-11-16T00:00:00-03:00'
        with self.assertRaises(ValueError): analysis.windows(self.plan)

    def test_many_readings_same_minute_do_not_cover_missing_minutes(self):
        readings=[dict(message_id=str(i),unit_id='pot-01',treatment='T1',source='physical',experiment_id='tcc-2026',collected_at='2026-11-01T00:00:10-03:00',sensor_valid=True,soil_index=40) for i in range(10)]
        at=analysis.timestamp('2026-11-01T00:02:00-03:00')
        result=analysis.describe(self.plan,[],readings,at)['periods']['total30']['T1']
        self.assertEqual(result['valid_readings'],10)
        self.assertEqual(result['valid_reading_coverage'],.5)

    def test_unconfirmed_command_is_not_zero_water(self):
        event=dict(event_id='ledger-1',command_id='1',unit_id='pot-01',treatment='T1',source='physical',experiment_id='tcc-2026',status='unconfirmed',occurred_at='2026-11-02T00:00:00-03:00')
        result=analysis.describe(self.plan,[event],[],analysis.windows(self.plan)['total30'][1])['periods']['total30']['T1']
        self.assertEqual(result['unresolved_cycles'],1)
        self.assertIsNone(result['observed_valid_water_ml'])
