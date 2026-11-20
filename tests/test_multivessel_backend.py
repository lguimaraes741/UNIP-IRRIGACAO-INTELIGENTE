"""Multi-unit persistence regression tests, opt in TEST_DATABASE_URL."""
import os
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
import test_backend_store as existing
from app import store
from app.contracts import Telemetry,CommandEvent,Intervention
from app.dispatcher import dispatch_once
import app.dispatcher as dispatcher
from unittest.mock import patch


class Published:
    def wait_for_publish(self,timeout): pass
    def is_published(self): return True


class Publisher:
    def __init__(self): self.sent=[]
    def publish(self,topic,payload,**kwargs):
        import json
        self.sent.append(json.loads(payload))
        return Published()


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'TEST_DATABASE_URL não definido')
class MultiVesselTests(existing.BackendStoreTests):
    def setUp(self):
        super().setUp()
        dispatcher_connection=patch.object(dispatcher,'connect',store.connect)
        dispatcher_connection.start()
        self.addCleanup(dispatcher_connection.stop)
        migration=Path('/app/migrations/004-multivessel.sql')
        if not migration.exists(): migration=Path('infra/postgres/004-multivessel.sql')
        with store.connect() as conn: conn.execute(migration.read_text())
        self.second=self.cfg.model_copy(update={'unit_id':'pot-02','treatment':'T2'})
        with store.connect() as conn:
            conn.execute('INSERT INTO control_configs(device_id,payload) VALUES(%s,%s)',('sim-01',Jsonb(self.second.model_dump(mode='json'))))
        self.reading(self.second,15)

    def reading(self,cfg,soil):
        item=Telemetry(schema_version=1,message_id=uuid4(),device_id=cfg.device_id,experiment_id=cfg.experiment_id,unit_id=cfg.unit_id,treatment=cfg.treatment,source=cfg.source,collected_at=datetime.now(timezone.utc),calibration_id=cfg.calibration_id,soil_raw=2000,soil_index=soil,sensor_valid=True,reservoir_mass_g=1000)
        store.save(item)
        return item

    def evaluation(self,cfg):
        return store.evaluate(cfg.device_id,uuid4(),cfg.unit_id,cfg.experiment_id)

    def terminal(self,command):
        now=datetime.now(timezone.utc)
        for offset,status in enumerate(['accepted','started','completed']):
            store.save_event(CommandEvent(event_id=uuid4(),command_id=command['command_id'],device_id=command['device_id'],source='simulation',occurred_at=now+timedelta(microseconds=offset),status=status,reason='test',experiment_id=command['experiment_id'],unit_id=command['unit_id']))

    def test_configuration_ambiguity_and_unit_cooldown(self):
        with self.assertRaises(ValueError): store.evaluate('sim-01',uuid4())
        a=self.evaluation(self.cfg);b=self.evaluation(self.second)
        self.assertTrue(a['actuate']);self.assertTrue(b['actuate'])
        self.assertFalse(self.evaluation(self.cfg)['actuate'])
        self.assertEqual(a['unit_id'],'pot-01');self.assertEqual(b['unit_id'],'pot-02')

    def test_global_executor_requires_terminal_even_after_expiration(self):
        self.evaluation(self.cfg);self.evaluation(self.second)
        publisher=Publisher()
        self.assertTrue(dispatch_once(publisher));self.assertEqual(len(publisher.sent),1)
        with store.connect() as conn:
            conn.execute("UPDATE commands SET status='started',expires_at=now()-interval '1 minute' WHERE command_id=%s",(publisher.sent[0]['command_id'],))
        self.assertFalse(dispatch_once(publisher));self.assertEqual(len(publisher.sent),1)

    def test_queue_revalidates_context_sensor(self):
        self.evaluation(self.second)
        self.reading(self.second,80)
        publisher=Publisher()
        self.assertTrue(dispatch_once(publisher));self.assertEqual(publisher.sent,[])
        with store.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM commands').fetchone()['status'],'rejected')

    def test_queue_rejects_parameter_changes_after_decision(self):
        self.evaluation(self.second)
        changed=self.second.model_copy(update={'soil_low':35})
        with store.connect() as conn:
            conn.execute("UPDATE control_configs SET payload=%s WHERE payload->>'unit_id'=%s",(Jsonb(changed.model_dump(mode='json')),changed.unit_id))
        publisher=Publisher()
        self.assertTrue(dispatch_once(publisher))
        self.assertEqual(publisher.sent,[])
        with store.connect() as conn:
            reason=conn.execute('SELECT reason FROM rejected_messages ORDER BY rejection_id DESC LIMIT 1').fetchone()['reason']
            self.assertEqual(reason,'queued_revalidation:configuration_changed_after_decision')

    def test_refill_blocks_shared_executor_and_invalidates_cycle(self):
        self.evaluation(self.cfg)
        publisher=Publisher();dispatch_once(publisher)
        command=publisher.sent[0]
        now=datetime.now(timezone.utc)
        for status in ['accepted','started']:
            store.save_event(CommandEvent(event_id=uuid4(),command_id=command['command_id'],device_id='sim-01',source='simulation',occurred_at=now,status=status,reason='test'))
        intervention=Intervention(intervention_id=uuid4(),device_id='sim-01',experiment_id=self.cfg.experiment_id,unit_id=self.cfg.unit_id,source='simulation',kind='refill',status='started',occurred_at=now,reason='refill')
        store.save_intervention(intervention)
        done=CommandEvent(event_id=uuid4(),command_id=command['command_id'],device_id='sim-01',source='simulation',occurred_at=now,status='completed',reason='test',measurements_valid=True,volume_ml=10,energy_wh=.01)
        self.assertTrue(store.save_event(done));self.assertFalse(store.save_event(done))
        with store.connect() as conn:
            row=conn.execute('SELECT payload FROM command_events WHERE event_id=%s',(done.event_id,)).fetchone()
            self.assertFalse(row['payload']['measurements_valid']);self.assertIsNone(row['payload']['volume_ml'])
        self.evaluation(self.second)
        self.assertFalse(dispatch_once(publisher))

    # Base regressions remain meaningful, except its no-context evaluate requires now an explicit unit.
    def test_persisted_latch_reset_config(self):
        result=self.evaluation(self.cfg)
        self.assertTrue(result['hysteresis_latched'])
        self.reading(self.second,50)
        self.assertFalse(self.evaluation(self.second)['hysteresis_latched'])
