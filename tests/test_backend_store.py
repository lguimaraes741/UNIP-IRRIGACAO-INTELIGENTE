"""DB regression tests; isolated PostgreSQL schema, opt in with TEST_DATABASE_URL."""
import os
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
from app import store
from app.contracts import IrrigationCommand, CommandEvent, DecisionConfiguration, Telemetry


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL não definido')
class BackendStoreTests(unittest.TestCase):
    def setUp(self):
        self.url=os.environ['TEST_DATABASE_URL']
        self.schema='test_'+uuid4().hex
        self.real_connect=store.connect
        with psycopg.connect(self.url) as conn:
            conn.execute(f'CREATE SCHEMA {self.schema}')
            conn.execute(f'SET search_path TO {self.schema}')
            base=Path('/app/migrations/001-schema.sql')
            control=Path('/app/migrations/003-control.sql')
            if not base.exists():
                base=Path('infra/postgres/001-schema.sql')
                control=Path('infra/postgres/003-control.sql')
            conn.execute(base.read_text())
            conn.execute(control.read_text())
        def connect():
            from psycopg.rows import dict_row
            return psycopg.connect(self.url,options=f'-c search_path={self.schema}',row_factory=dict_row)
        self.patch=patch.object(store,'connect',connect)
        self.patch.start()
        self.env=patch.dict(os.environ,{'SIMULATION_CONTROL_ENABLED':'true'})
        self.env.start()
        self.now=datetime.now(timezone.utc)-timedelta(seconds=1)
        cfg=DecisionConfiguration(device_id='sim-01',experiment_id='sim-store',unit_id='pot-01',treatment='T2',source='simulation',enabled=True,calibration_id='sim-v1',soil_low=30,soil_high=60,min_reservoir_mass_g=100,target_volume_ml=10,max_duration_s=5,max_telemetry_age_s=60,cooldown_s=1)
        self.cfg=cfg
        with store.connect() as conn:
            conn.execute('INSERT INTO control_configs VALUES(%s,%s,now())',(cfg.device_id,Jsonb(cfg.model_dump(mode='json'))))
        store.save(Telemetry(schema_version=1,message_id=uuid4(),device_id=cfg.device_id,experiment_id=cfg.experiment_id,unit_id=cfg.unit_id,treatment=cfg.treatment,source=cfg.source,collected_at=self.now,calibration_id=cfg.calibration_id,soil_raw=2000,soil_index=20,sensor_valid=True,reservoir_mass_g=1000))
        self.command=IrrigationCommand(command_id=uuid4(),device_id=cfg.device_id,experiment_id=cfg.experiment_id,unit_id=cfg.unit_id,treatment=cfg.treatment,source=cfg.source,issued_at=self.now,expires_at=self.now+timedelta(seconds=60),target_volume_ml=10,max_duration_s=5,reason='test')

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        with psycopg.connect(self.url) as conn:
            conn.execute(f'DROP SCHEMA {self.schema} CASCADE')

    def event(self,status,seconds=0):
        return CommandEvent(event_id=uuid4(),command_id=self.command.command_id,device_id='sim-01',source='simulation',occurred_at=self.now+timedelta(seconds=seconds),status=status,reason='test')

    def test_command_dedup_conflict_outbox(self):
        self.assertTrue(store.save_command(self.command))
        self.assertFalse(store.save_command(self.command))
        changed=self.command.model_copy(update={'reason':'other'})
        with self.assertRaises(store.MessageConflict):
            store.save_command(changed)
        with store.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM command_outbox').fetchone()['n'],1)

    def test_event_dedup_order_conflict(self):
        store.save_command(self.command)
        accepted=self.event('accepted')
        self.assertTrue(store.save_event(accepted))
        self.assertFalse(store.save_event(accepted))
        with self.assertRaises(store.MessageConflict):
            store.save_event(accepted.model_copy(update={'reason':'other'}))
        with self.assertRaises(ValueError):
            store.save_event(self.event('completed'))
        store.save_event(self.event('started',0.1))
        with self.assertRaises(ValueError):
            store.save_event(self.event('completed',0.05))
        store.save_event(self.event('completed',0.2))
        with store.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM commands').fetchone()['status'],'completed')

    def test_persisted_latch_reset_config(self):
        decision=store.evaluate('sim-01',uuid4())
        self.assertTrue(decision['hysteresis_latched'])
        # Reject command so no active actuation remains; use a new config to reset state.
        store.save_event(self.event_for_id(decision['command_id']))
        self.cfg.soil_low=10
        with store.connect() as conn:
            conn.execute('UPDATE control_configs SET payload=%s WHERE device_id=%s',(Jsonb(self.cfg.model_dump(mode='json')),'sim-01'))
        later=store.evaluate('sim-01',uuid4())
        self.assertFalse(later['hysteresis_latched'])

    def event_for_id(self,command_id):
        return CommandEvent(event_id=uuid4(),command_id=command_id,device_id='sim-01',source='simulation',occurred_at=datetime.now(timezone.utc),status='rejected',reason='test')

    def test_local_cycle_order_dedup_and_no_remote_outbox(self):
        cid = uuid4()
        def event(status,seconds=0):
            return CommandEvent(event_id=uuid4(),command_id=cid,device_id='sim-01',source='simulation',
                origin='local',experiment_id='sim-store',unit_id='pot-01',occurred_at=self.now+timedelta(seconds=seconds),
                status=status,reason='local_fixture')
        with self.assertRaises(ValueError):
            store.save_event(event('completed'))
        accepted=event('accepted')
        self.assertTrue(store.save_event(accepted))
        self.assertFalse(store.save_event(accepted))
        with self.assertRaises(store.MessageConflict):
            store.save_event(accepted.model_copy(update={'reason':'other'}))
        store.save_event(event('started',.1))
        store.save_event(event('completed',.2))
        with store.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM commands').fetchone()['n'],0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM command_outbox').fetchone()['n'],0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM local_cycle_events').fetchone()['n'],3)

    def test_durable_rejection(self):
        store.save_rejection('irrigation/v1/sim-01/events',b'{bad','invalid_contract')
        with store.connect() as conn:
            row=conn.execute('SELECT reason,payload FROM rejected_messages').fetchone()
            self.assertEqual(row['reason'],'invalid_contract')
            self.assertEqual(bytes(row['payload']),b'{bad')
