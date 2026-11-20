import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from app import bench

T0 = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)


def plan(loads, seconds=120):
    steps = []
    for i, load in enumerate(loads):
        start = T0 + timedelta(seconds=i * seconds)
        steps.append(dict(index=i, load=load, label=load, started_at=start.isoformat(),
                          ended_at=(start + timedelta(seconds=seconds)).isoformat()))
    return steps


def samples(steps, counts_for, drift_per_s=0.0, every=10):
    out, t = [], T0
    end = datetime.fromisoformat(steps[-1]['ended_at'])
    while t <= end:
        step = next(s for s in steps if datetime.fromisoformat(s['started_at']) <= t <= datetime.fromisoformat(s['ended_at']))
        out.append(dict(at=t, counts=counts_for(step) + drift_per_s * (t - T0).total_seconds(), mass=None, temp=25.0))
        t += timedelta(seconds=every)
    return out


class BenchAnalysisTests(unittest.TestCase):
    def test_calibration_cancels_linear_drift(self):
        steps = plan(['empty', 'loaded', 'empty', 'loaded', 'empty', 'loaded', 'empty'])
        # 20 counts/g, 600 g load, plus a strong linear drift (~1 g/min).
        data = samples(steps, lambda s: 8_000_000 + (12_000 if s['load'] == 'loaded' else 0), drift_per_s=20 / 60)
        result = bench.calibration(bench.summarize(steps, data), 600)
        self.assertTrue(result['ok'])
        self.assertAlmostEqual(result['factor'], 20, places=2)
        self.assertEqual(result['repeats'], 3)
        self.assertLess(result['repeatability_g'], 0.5)

    def test_calibration_needs_empty_on_both_sides(self):
        steps = plan(['empty', 'loaded'])
        data = samples(steps, lambda s: 1000 if s['load'] == 'loaded' else 0)
        self.assertFalse(bench.calibration(bench.summarize(steps, data), 600)['ok'])

    def test_calibration_rejects_load_not_applied(self):
        steps = plan(['empty', 'loaded', 'empty'])
        data = samples(steps, lambda s: 8_000_000 + (3 if s['load'] == 'loaded' else 0))
        result = bench.calibration(bench.summarize(steps, data), 600)
        self.assertFalse(result['ok'])
        self.assertIn('implausível', result['reason'])

    def test_withdrawal_counts_reps_within_two_grams(self):
        steps = plan(['empty'] + ['loaded', 'empty'] * 10, seconds=40)
        bad = {3}  # one repetition off by 5 g

        def counts(step):
            if step['load'] != 'loaded':
                return 0
            rep = (step['index'] - 1) // 2
            return 20 * (55 if rep in bad else 50)
        result = bench.withdrawal(bench.summarize(steps, samples(steps, counts)), 50, 20)
        self.assertEqual(result['total'], 10)
        self.assertEqual(result['within_2g'], 9)
        self.assertTrue(result['approved'])

    def test_press_reports_return_difference(self):
        steps = plan(['empty', 'press', 'empty', 'press', 'empty'], seconds=40)
        offsets = {0: 0, 2: 40, 4: 0}
        data = samples(steps, lambda s: offsets.get(s['index'], 5000))
        result = bench.press(bench.summarize(steps, data), 20)
        self.assertEqual([round(r['diff_g'], 1) for r in result['returns']], [2.0, 0.0])
        self.assertEqual(result['max_abs_g'], 2.0)

    def test_settling_readings_are_discarded(self):
        steps = plan(['empty'], seconds=40)
        data = [dict(at=T0 + timedelta(seconds=5), counts=999, mass=None, temp=None),
                dict(at=T0 + timedelta(seconds=30), counts=10, mass=None, temp=None)]
        self.assertEqual(bench.summarize(steps, data)[0]['median'], 10)


class BenchSessionTests(unittest.TestCase):
    def test_new_session_abandons_running_and_latest_calibration_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = bench.SESSIONS
            bench.SESSIONS = Path(tmp)
            try:
                for i, (status, cal) in enumerate([('completed', dict(factor=22.0)), ('completed', dict(factor=22.2)), ('running', None)]):
                    session = dict(id=f'00000000-0000-0000-0000-00000000000{i}', kind='calibration', status=status,
                                   started_at=f'2026-11-06T2{i}:00:00+00:00', steps=[])
                    if cal:
                        session['calibration'] = cal
                    bench.store(session)
                self.assertEqual(bench.latest_calibration()['factor'], 22.2)
                bench.start(bench.SessionStart(kind='press'))
                statuses = {s['id'][-1]: s['status'] for s in bench.all_sessions()}
                self.assertEqual(statuses['2'], 'abandoned')
            finally:
                bench.SESSIONS = old


class ReservoirTests(unittest.TestCase):
    def test_status_levels(self):
        self.assertEqual(bench.reservoir_status(dict(reservoir_ml=None))['level'], 'unknown')
        self.assertEqual(bench.reservoir_status(None)['level'], 'unknown')
        ok = bench.reservoir_status(dict(reservoir_ml=1000, reservoir_reserve_ml=200, reservoir_ref='x'))
        self.assertEqual((ok['level'], ok['doses'], ok['usable_ml']), ('ok', 16, 800))
        self.assertEqual(bench.reservoir_status(dict(reservoir_ml=320, reservoir_reserve_ml=200))['level'], 'low')
        self.assertEqual(bench.reservoir_status(dict(reservoir_ml=240, reservoir_reserve_ml=200))['level'], 'empty')

    def test_refill_message_matches_firmware_checks(self):
        msg = bench.refill_message(bench.Refill(total_ml=1000, reserve_ml=200), '00000000-0000-4000-8000-000000000000', T0)
        self.assertEqual(msg['schema_version'], 1)
        self.assertEqual(datetime.fromisoformat(msg['expires_at']) - datetime.fromisoformat(msg['issued_at']), timedelta(minutes=5))
        self.assertTrue(msg['issued_at'].endswith('+00:00'))


class PulseTests(unittest.TestCase):
    def test_pulse_limits_match_firmware(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            bench.Pulse(output=1, duration_ms=2001)
        with self.assertRaises(ValidationError):
            bench.Pulse(output=4, duration_ms=500)
        msg = bench.pulse_message(bench.Pulse(output=2, duration_ms=1000), '00000000-0000-4000-8000-000000000000', T0)
        self.assertEqual((msg['output'], msg['duration_ms']), (2, 1000))
        self.assertEqual(datetime.fromisoformat(msg['expires_at']) - datetime.fromisoformat(msg['issued_at']), timedelta(seconds=30))


class FlowTests(unittest.TestCase):
    def test_flow_excludes_priming_and_computes_dose_time(self):
        steps = []
        loads = []
        for output in (1, 2):
            loads.append(('empty', None))
            for _ in range(3):
                loads += [('pulse', output), ('empty', None)]
        for i, (load, output) in enumerate(loads):
            start = T0 + timedelta(seconds=i * 40)
            steps.append(dict(index=i, load=load, label=load, output=output, started_at=start.isoformat(),
                              ended_at=(start + timedelta(seconds=40)).isoformat()))
        level = {}
        mass = 1000.0
        for step in steps:  # each pulse removes 100 g (priming 140 g); 20 counts/g
            if step['load'] == 'pulse':
                prior = sum(1 for s in steps[:step['index']] if s['load'] == 'pulse' and s['output'] == step['output'])
                mass -= 140 if prior == 0 else 100
            level[step['index']] = mass
        data = samples(steps, lambda s: 20 * level[s['index']])
        result = bench.flow(bench.summarize(steps, data), 20, 5000)
        self.assertTrue(result['ok'])
        self.assertEqual(result['pumps'][0]['reps'][1]['pulses'], 1)
        pump = result['pumps'][0]
        self.assertAlmostEqual(pump['ml_per_s'], 20, places=3)
        self.assertTrue(pump['reps'][0]['priming'])
        self.assertAlmostEqual(pump['dose_s'], 2.5, places=3)

    def test_flow_groups_consecutive_pulses(self):
        # Fast plan: priming pulse, weigh, 3 pulses in a row, weigh (per pump).
        loads = []
        for output in (3, 2):
            loads += [('pulse', output), ('empty', None), ('pulse', output), ('pulse', output), ('pulse', output), ('empty', None)]
        steps = []
        for i, (load, output) in enumerate(loads):
            start = T0 + timedelta(seconds=i * 30)
            steps.append(dict(index=i, load=load, label=load, output=output, started_at=start.isoformat(),
                              ended_at=(start + timedelta(seconds=30)).isoformat()))
        mass, level = 1000.0, {}
        for step in steps:  # 2 s pulses at 25 mL/s = 50 g; priming 70 g
            if step['load'] == 'pulse':
                first = not any(s['load'] == 'pulse' and s['output'] == step['output'] for s in steps[:step['index']])
                mass -= 70 if first else 50
            level[step['index']] = mass
        data = samples(steps, lambda s: 20 * level[s['index']])
        result = bench.flow(bench.summarize(steps, data, bench.FLOW_SETTLE_S), 20, 2000)
        self.assertTrue(result['ok'])
        for pump in result['pumps']:
            self.assertEqual([r['pulses'] for r in pump['reps']], [1, 3])
            self.assertTrue(pump['reps'][0]['priming'])
            self.assertAlmostEqual(pump['ml_per_s'], 25, places=3)
            self.assertAlmostEqual(pump['dose_s'], 2.0, places=3)

    def test_wet_pulse_limits(self):
        from pydantic import ValidationError
        self.assertEqual(bench.Pulse(output=1, duration_ms=5000, mode='wet').duration_ms, 5000)
        with self.assertRaises(ValidationError):
            bench.Pulse(output=1, duration_ms=5000)
        with self.assertRaises(ValidationError):
            bench.Pulse(output=1, duration_ms=10001, mode='wet')


if __name__ == '__main__':
    unittest.main()
