"""Modelo determinístico de dispositivo; nunca aciona hardware nem valida agronomia."""
import json
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4


def replace_state(tmp, target):
    # Windows/OneDrive pode manter breve bloqueio; nunca ignorar falha de persistência.
    for attempt in range(4):
        try:
            tmp.replace(target)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(.05)


class DeviceSimulator:
    def __init__(self, state_path=None, buffer_size=64):
        self.device_id = 'sim-01'
        self.unit_id = 'sim-pot-01'
        self.experiment_id = 'sim-integration'
        self.treatment = 'T1'
        self.state_path = Path(state_path) if state_path else None
        self.journal = {}
        self.journal_capacity = 4096
        self.buffer = deque(maxlen=buffer_size)
        self.dropped = 0
        self.pump_on = False
        self.active = None
        self.remote_ready = False
        self.remote_since = None
        self.soil_index = 25.0
        self.sensor_valid = True
        self.reservoir_ml = 1500.0
        self.max_volume_ml = 100.0
        self.max_duration_s = 30.0
        self.flow_ml_s = 5.0  # Fixture explícita, não calibração.
        self.power_w = 3.0
        self.fallback_enabled = False
        self.fallback_latched = False
        if self.state_path and self.state_path.exists():
            saved = json.loads(self.state_path.read_text())
            self.journal = saved['journal']
            self.buffer.extend(saved.get('buffer', []))
            self.dropped = saved.get('dropped', 0)
        # Boot sempre desligado; diário mantém comandos inclusive interrompidos.

    def save(self):
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(dict(journal=self.journal, buffer=list(self.buffer), dropped=self.dropped)))
            replace_state(tmp, self.state_path)

    def queue(self, topic, payload):
        if len(self.buffer) == self.buffer.maxlen:
            self.dropped += 1
        self.buffer.append(dict(topic=topic, payload=payload))
        self.save()

    def acknowledge_buffer(self):
        self.buffer.popleft()
        self.save()

    def event(self, command, now, status, reason, **metrics):
        return dict(schema_version=1, event_id=str(uuid4()), command_id=str(command['command_id']),
                    device_id=self.device_id, source='simulation', occurred_at=now.isoformat(),
                    origin=command.get('origin','remote'), experiment_id=self.experiment_id, unit_id=self.unit_id,
                    treatment=command.get('treatment', self.treatment),
                    status=status, reason=reason, **metrics)

    def resync(self, now=None):
        if self.active:
            return False
        self.remote_ready = True
        self.remote_since = now
        return True

    def disconnect(self):
        self.remote_ready = False

    def receive(self, command, now, local=False):
        command = dict(command, origin='local' if local else 'remote')
        identifier = str(command['command_id'])
        reason = None
        if identifier in self.journal:
            reason = 'duplicate'
        elif len(self.journal) >= self.journal_capacity:
            reason = 'journal_full'
        elif command.get('device_id') != self.device_id or command.get('source') != 'simulation':
            reason = 'identity'
        elif (command.get('unit_id'), command.get('experiment_id'), command.get('treatment')) != (self.unit_id, self.experiment_id, self.treatment):
            reason = 'context'
        elif not local and not self.remote_ready:
            reason = 'resync_required'
        elif self.active:
            reason = 'busy'
        elif datetime.fromisoformat(command['expires_at'].replace('Z', '+00:00')) <= now:
            reason = 'expired'
        elif datetime.fromisoformat(command['issued_at'].replace('Z', '+00:00')) > now:
            reason = 'future_command'
        elif not local and self.remote_since and datetime.fromisoformat(command['issued_at'].replace('Z', '+00:00')) < self.remote_since:
            reason = 'before_resync'
        elif not self.sensor_valid or self.soil_index is None:
            reason = 'invalid_sensor'
        elif not 0 < command['target_volume_ml'] <= self.max_volume_ml:
            reason = 'volume_limit'
        elif not 0 < command['max_duration_s'] <= self.max_duration_s:
            reason = 'duration_limit'
        elif self.reservoir_ml < command['target_volume_ml']:
            reason = 'insufficient_water'
        if reason:
            if identifier not in self.journal and len(self.journal) < self.journal_capacity:
                self.journal[identifier] = 'rejected'
            self.save()
            return [self.event(command, now, 'rejected', reason)]
        # Gravar ANTES de iniciar: reboot não reexecuta comando recebido.
        self.journal[identifier] = 'started'
        self.save()
        self.active = dict(command=command, start=now, volume=0.0)
        self.pump_on = True
        return [self.event(command, now, 'accepted', 'simulation'), self.event(command, now+timedelta(microseconds=1), 'started', 'simulation')]

    def tick(self, now):
        if not self.active:
            return []
        active = self.active
        command = active['command']
        elapsed = max(0, (now-active['start']).total_seconds())
        effective = min(elapsed, command['max_duration_s'])
        volume = min(effective*self.flow_ml_s, command['target_volume_ml'])
        reason = None
        valid = self.sensor_valid and self.soil_index is not None
        if not valid:
            reason = 'invalid_sensor'
        elif volume > self.reservoir_ml:
            reason = 'insufficient_water'
        elif volume >= command['target_volume_ml']:
            reason = 'target_reached'
        elif elapsed >= command['max_duration_s']:
            reason = 'duration_limit'
        if not reason:
            return []
        duration = min(effective, volume/self.flow_ml_s)
        volume = min(volume, self.reservoir_ml)
        self.reservoir_ml -= volume
        self.pump_on = False
        self.active = None
        status = 'completed' if reason == 'target_reached' else 'failed'
        self.journal[str(command['command_id'])] = status
        self.save()
        return [self.event(command, now, status, reason, duration_s=duration,
                           volume_ml=volume, energy_wh=self.power_w*duration/3600,
                           measurements_valid=True)]

    def fallback(self, now):
        if not self.fallback_enabled or self.remote_ready or self.active or not self.sensor_valid or self.soil_index is None:
            return []
        if self.soil_index >= 45:
            self.fallback_latched = False
        if self.soil_index >= 30 or self.fallback_latched:
            return []
        from datetime import timedelta
        self.fallback_latched = True
        command = dict(command_id=str(uuid4()), device_id=self.device_id, source='simulation',
                       unit_id=self.unit_id, experiment_id=self.experiment_id, treatment=self.treatment,
                       issued_at=now.isoformat(), expires_at=(now+timedelta(seconds=10)).isoformat(),
                       target_volume_ml=25, max_duration_s=10)
        return self.receive(command, now, local=True)

    def stop(self, now):
        """Parada explícita: desliga, encerra comando e não atribui medição ausente a zero."""
        self.pump_on = False
        if not self.active:
            return []
        command = self.active['command']
        occurred_at = max(now, self.active['start']+timedelta(microseconds=1))
        self.active = None
        self.journal[str(command['command_id'])] = 'failed'
        self.save()
        return [self.event(command, occurred_at, 'failed', 'simulator_stopped', measurements_valid=False)]


class MultiVesselSimulator(DeviceSimulator):
    """Três contextos sintéticos, executor e instrumentação compartilhados.

    Fila fica no backend; dispositivo rejeita sobreposição inclusive na estabilização.
    """
    def __init__(self, state_path=None, buffer_size=64, stabilization_s=.5):
        super().__init__(state_path, buffer_size)
        self.experiment_id = 'sim-three-pots'
        self.units = {f'sim-pot-0{i}': dict(treatment=f'T{i}', soil_index=20+5*i,
                       sensor_valid=True, flow_ml_s=6-i, power_w=3.5-.5*i,
                       fallback_latched=False) for i in range(1,4)}
        self.stabilization_s = stabilization_s
        self.settling = None
        self.refill_revision = 0
        self.fallback_cursor = 0
        if self.state_path and self.state_path.exists():
            saved = json.loads(self.state_path.read_text())
            self.units.update(saved.get('units', {}))
            self.reservoir_ml = saved.get('reservoir_ml', self.reservoir_ml)
            self.refill_revision = saved.get('refill_revision', 0)
            self.fallback_cursor = saved.get('fallback_cursor', 0)
            if saved.get('settling'):
                self.settling = saved['settling']
                self.settling['until'] = datetime.fromisoformat(self.settling['until'])

    def save(self):
        if not hasattr(self, 'units'):
            return super().save()
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix('.tmp')
            settling = dict(self.settling, until=self.settling['until'].isoformat()) if self.settling else None
            tmp.write_text(json.dumps(dict(journal=self.journal, buffer=list(self.buffer), dropped=self.dropped,
                                          units=self.units, reservoir_ml=self.reservoir_ml,
                                          refill_revision=self.refill_revision, fallback_cursor=self.fallback_cursor,
                                          settling=settling)))
            replace_state(tmp, self.state_path)

    def select(self, unit_id):
        state = self.units[unit_id]
        self.unit_id, self.treatment = unit_id, state['treatment']
        self.soil_index, self.sensor_valid = state['soil_index'], state['sensor_valid']
        self.flow_ml_s, self.power_w = state['flow_ml_s'], state['power_w']
        self.fallback_latched = state['fallback_latched']

    def event(self, command, now, status, reason, **metrics):
        result = super().event(command, now, status, reason, **metrics)
        result.update(unit_id=command.get('unit_id', self.unit_id),
                      experiment_id=command.get('experiment_id', self.experiment_id))
        return result

    def resync(self, now=None):
        return False if self.settling else super().resync(now)

    def receive(self, command, now, local=False):
        unit = command.get('unit_id')
        if unit not in self.units:
            return [self.event(command, now, 'rejected', 'unknown_unit')]
        if self.active or self.settling:
            reason = 'duplicate' if str(command['command_id']) in self.journal else 'shared_executor_busy'
            return [self.event(command, now, 'rejected', reason)]
        self.select(unit)
        events = super().receive(command, now, local)
        if self.active:
            self.active['refill_revision'] = self.refill_revision
        return events

    def tick(self, now):
        if self.settling:
            if now < self.settling['until']:
                return []
            event = self.settling['event']
            persisted = next((item['payload'] for item in self.buffer if item['payload'].get('event_id') == event['event_id']), None)
            if persisted:
                event = persisted
            else:
                event['occurred_at'] = now.isoformat()
            # Persistir no outbox antes de remover o resultado pendente de estabilização.
            # Reinício entre retorno de tick e transporte não perde a confirmação.
            if not persisted:
                self.queue(f'irrigation/v1/{self.device_id}/events', event)
            self.settling = None
            self.save()
            return [event]
        if self.active:
            self.select(self.active['command']['unit_id'])
        events = super().tick(now)
        if events and events[-1]['status'] in ('completed','failed'):
            event = events[-1]
            self.settling = dict(event=event, until=now+timedelta(seconds=self.stabilization_s))
            self.save()
            return []
        return events

    def refill(self, new_volume_ml, now):
        if not 0 <= new_volume_ml <= 5000:
            raise ValueError('Volume sintético inválido')
        self.refill_revision += 1
        event = None
        if self.active:
            command = self.active['command']
            self.pump_on = False
            self.active = None
            self.journal[str(command['command_id'])] = 'failed'
            event = self.event(command, now, 'failed', 'refill_during_cycle', measurements_valid=False)
        elif self.settling:
            event = dict(self.settling['event'], status='failed', reason='refill_during_settling',
                         occurred_at=now.isoformat(), measurements_valid=False,
                         volume_ml=None, energy_wh=None, duration_s=None)
        self.settling = None
        self.reservoir_ml = new_volume_ml
        self.save()
        return [event] if event else []

    def fallback(self, now):
        if self.active or self.settling or not self.fallback_enabled or self.remote_ready:
            return []
        ordered = list(self.units)
        for offset in range(len(ordered)):
            position = (self.fallback_cursor+offset) % len(ordered)
            unit = ordered[position]
            state = self.units[unit]
            self.select(unit)
            events = super().fallback(now)
            state['fallback_latched'] = self.fallback_latched
            if events:
                self.fallback_cursor = (position+1) % len(ordered)
                self.save()
                return events
        return []

    def stop(self, now):
        if self.settling:
            event = dict(self.settling['event'], status='failed', reason='stopped_before_stabilization',
                         occurred_at=now.isoformat(), measurements_valid=False,
                         volume_ml=None, energy_wh=None, duration_s=None)
            self.settling = None
            self.save()
            return [event]
        return super().stop(now)
