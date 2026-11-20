import io
import json
import unittest
from tools.bench_monitor import local_ip, snapshot


class BenchMonitorTest(unittest.TestCase):
    def test_no_control_routes_or_trial_claims(self):
        calls = []
        def opener(url, timeout):
            calls.append(url)
            return io.BytesIO(json.dumps({'ina': True, 'hx': True, 'peso': 0,
                                         'password': 'not-exported'}).encode())
        record = snapshot('192.168.15.39', opener)
        self.assertEqual(calls, ['http://192.168.15.39/dados'])
        self.assertFalse(record['trial_eligible'])
        self.assertFalse(record['calibration_verified'])
        self.assertNotIn('password', record['reported'])

    def test_failure_does_not_become_zero(self):
        def offline(*args, **kwargs):
            raise TimeoutError()
        record = snapshot('192.168.15.39', offline)
        self.assertFalse(record['read_ok'])
        self.assertIsNone(record['reported'])

    def test_nonfinite_json_rejected(self):
        record = snapshot('192.168.15.39', lambda *a, **k: io.BytesIO(b'{"peso":NaN}'))
        self.assertFalse(record['read_ok'])

    def test_only_private_literal_addresses(self):
        for value in ('8.8.8.8', '127.0.0.1', 'localhost', '192.168.15.39/tara'):
            with self.assertRaises(ValueError):
                local_ip(value)


if __name__ == '__main__':
    unittest.main()
