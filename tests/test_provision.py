import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('provision',Path(__file__).resolve().parents[1]/'tools/provision-device.py')
provision=importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        (provision.ROOT/'.local').mkdir(exist_ok=True)
        self.meta=patch.object(provision,'partition_metadata',return_value={'partition':'provision','offset':0x130000,'size':0x10000})
        self.meta.start()
        self.addCleanup(self.meta.stop)
        self.validation=patch.object(provision,'validate',side_effect=lambda s,m:s)
        self.validation.start()
        self.addCleanup(self.validation.stop)

    def settings(self):
        return dict(device='sim-01',experiment='sim-test',unit='pot-01',broker='mqtts://notebook:8883',username='sim-01',password='secret',ca='certificate',ssid='bancada',wifi_password='password',ntp='pool.ntp.org')

    def test_identity_tls_and_wifi_bounds(self):
        self.validation.stop()
        for change in [dict(broker='mqtt://notebook'),dict(broker='mqtts://u:password@notebook'),dict(device='esp32-01'),dict(ssid='a'*32),dict(wifi_password='short')]:
            item=self.settings();item.update(change)
            with self.assertRaises(ValueError):
                provision.validate(item,'simulation')
        with patch.object(provision,'validate_ca'):
            self.assertEqual(provision.validate(self.settings(),'simulation')['device'],'sim-01')
        with self.assertRaises(ValueError):
            provision.validate(self.settings(),'physical')

    def test_optional_physical_calibration_keys(self):
        self.validation.stop()
        base=dict(self.settings(),device='esp32-01',experiment='tcc-2026',username='esp32-01')
        physical=dict(base,scale_cal='scale-20261104',scale_offset='8605705',scale_factor='19.9955',ina3='3',telemetry_s='60')
        with patch.object(provision,'validate_ca'):
            self.assertEqual(provision.validate(physical,'physical')['scale_factor'],'19.9955')
            for change in [dict(scale_factor='0'),dict(scale_offset='abc'),dict(dry1='nan'),dict(ina1='4'),
                           dict(telemetry_s='5'),dict(soil_cal='Solo 1'),dict(unknown='x')]:
                with self.subTest(change=change),self.assertRaises(ValueError):
                    provision.validate(dict(physical,**change),'physical')

    def test_bad_ca_and_private_path(self):
        self.validation.stop()
        with self.assertRaises(ValueError):
            provision.validate(self.settings(),'simulation')
        with self.assertRaises(ValueError):
            provision.private_path(provision.ROOT/'settings.csv')

    def test_csv_namespace_no_commands_or_overwrite(self):
        with tempfile.TemporaryDirectory(dir=provision.ROOT/'.local') as temp,patch.object(provision,'private_path',side_effect=lambda p:Path(p)):
            csv_path,_=provision.prepare(self.settings(),Path(temp)/'out',csv_only=True)
            with csv_path.open(newline='',encoding='utf-8') as f:
                rows=list(csv.reader(f))
            self.assertEqual(rows[1],['settings','namespace','',''])
            self.assertNotIn('commands',[r[0] for r in rows])
            with self.assertRaises(ValueError):
                provision.prepare(self.settings(),Path(temp)/'out',csv_only=True)

    def test_generator_argv_no_secrets_and_output_size(self):
        with tempfile.TemporaryDirectory(dir=provision.ROOT/'.local') as temp,patch.object(provision,'private_path',side_effect=lambda p:Path(p)):
            idf=Path(temp)/'idf'
            gen=idf/'components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py'
            gen.parent.mkdir(parents=True);gen.touch()
            def run(argv,**kwargs):
                self.assertNotIn('secret',argv)
                self.assertTrue(kwargs['capture_output'])
                Path(argv[-2]).write_bytes(b'\xff'*0x10000)
                from types import SimpleNamespace
                return SimpleNamespace(returncode=0)
            with patch.object(provision.subprocess,'run',side_effect=run):
                _,bin_path=provision.prepare(self.settings(),Path(temp)/'out',idf)
            self.assertEqual(bin_path.stat().st_size,0x10000)

    def test_partition_size_rejected(self):
        with patch.object(provision,'private_path',side_effect=lambda p:Path(p)):
            with self.assertRaises(ValueError):
                provision.prepare(self.settings(),'unused',csv_only=True,size=0x3100)

    def test_partition_table_type_and_size_validation(self):
        self.meta.stop()
        with tempfile.TemporaryDirectory(dir=provision.ROOT/'.local') as temp,patch.object(provision,'ROOT',Path(temp)):
            table=Path(temp)/'firmware/partitions.csv'
            table.parent.mkdir()
            table.write_text('provision,data,nvs,0x130000,0x10000,\n')
            meta=provision.partition_metadata()
            self.assertEqual(meta['offset'],0x130000)
            self.assertEqual(meta['size'],0x10000)
            table.write_text('provision,data,nvs,0x130000,0x10000,\njournal,data,nvs,0x130000,0x10000,\n')
            with self.assertRaises(ValueError):
                provision.partition_metadata()
            table.write_text('provision,app,factory,0x130000,0x10000,\n')
            with self.assertRaises(ValueError):
                provision.partition_metadata()


    def test_three_unit_map_unique_complete_and_legacy_alias(self):
        self.validation.stop()
        item=self.settings();item.pop('unit')
        item.update(unit1='pot-01',unit2='pot-02',unit3='pot-03')
        with patch.object(provision,'validate_ca'):
            self.assertEqual(provision.validate(item,'simulation')['unit3'],'pot-03')
            for change in [dict(unit2='pot-01'),dict(unit='different')]:
                with self.assertRaises(ValueError):
                    provision.validate(dict(item,**change),'simulation')
            item.pop('unit3')
            with self.assertRaises(ValueError): provision.validate(item,'simulation')
