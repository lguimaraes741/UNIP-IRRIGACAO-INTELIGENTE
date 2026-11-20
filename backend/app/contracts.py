from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Telemetry(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal[1]
    message_id: UUID
    device_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    experiment_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    unit_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    treatment: Literal['T1', 'T2', 'T3']
    source: Literal['simulation', 'physical']
    collected_at: AwareDatetime
    calibration_id: str = Field(min_length=1, max_length=80)
    # Null when the ADC read failed; never replaced by a default value.
    soil_raw: int | None = Field(ge=0, le=4095, strict=True)
    soil_index: float | None = Field(default=None, ge=0, le=100)
    sensor_valid: bool = Field(strict=True)
    temperature_c: float | None = Field(default=None, ge=-40, le=85)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    # Relative to the provisioned tare: small negatives are drift evidence, not errors.
    reservoir_mass_g: float | None = Field(default=None, ge=-5000, le=50000)
    pump_voltage_v: float | None = Field(default=None, ge=0, le=26)
    # Signed shunt reading (INA3221 full scale 1.6 A at 0.1 ohm); noise can be slightly negative.
    pump_current_a: float | None = Field(default=None, ge=-1.6, le=1.6)
    # Physical acquisition metadata (optional so simulated messages stay valid).
    firmware_version: str | None = Field(default=None, max_length=40)
    scale_counts: int | None = Field(default=None, ge=0, le=0xFFFFFF, strict=True)
    mass_stable: bool | None = None
    # Raw-sample span of the ~1 s scale window, in grams (diagnostic for the stability threshold).
    scale_span_g: float | None = Field(default=None, ge=0, le=5000)
    scale_calibration_id: str | None = Field(default=None, min_length=1, max_length=80)
    ina_channel: int | None = Field(default=None, ge=1, le=3, strict=True)
    pump_output: int | None = Field(default=None, ge=1, le=3, strict=True)
    # Shared reservoir estimate by accounting (registered refill minus doses), not weighing.
    reservoir_ml: float | None = Field(default=None, ge=0, le=5000)
    reservoir_reserve_ml: float | None = Field(default=None, ge=0, le=5000)
    reservoir_ref: str | None = Field(default=None, max_length=36)
    # Link/heap diagnostics (firmware physical-telemetry-3); shared by the three units.
    uptime_s: float | None = Field(default=None, ge=0)
    reset_reason: int | None = Field(default=None, ge=0, le=64, strict=True)
    heap_free_b: float | None = Field(default=None, ge=0)
    heap_min_b: float | None = Field(default=None, ge=0)
    heap_largest_b: float | None = Field(default=None, ge=0)
    wifi_rssi_dbm: int | None = Field(default=None, ge=-127, le=20, strict=True)
    mqtt_connects: int | None = Field(default=None, ge=0, strict=True)
    wifi_disconnects: int | None = Field(default=None, ge=0, strict=True)
    wifi_last_reason: int | None = Field(default=None, ge=0, le=255, strict=True)
    publish_timeouts: int | None = Field(default=None, ge=0, strict=True)
    wifi_kicks: int | None = Field(default=None, ge=0, strict=True)
    outbox_dropped: float | None = Field(default=None, ge=0)
    manual_enabled: bool | None = None
    dht_faults: int | None = Field(default=None, ge=0, strict=True)
    dht_streak: int | None = Field(default=None, ge=0, strict=True)
    dht_resets: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode='after')
    def consistency(self):
        if self.collected_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError('collected_at está mais de cinco minutos no futuro')
        if self.sensor_valid and self.soil_raw is None:
            raise ValueError('sensor válido exige leitura bruta')
        if self.reservoir_mass_g is not None and self.source == 'physical' and not self.scale_calibration_id:
            raise ValueError('massa física exige identificação da calibração da balança')
        if self.sensor_valid and self.soil_index is None:
            raise ValueError('sensor válido exige soil_index calibrado')
        if not self.sensor_valid and self.soil_index is not None:
            raise ValueError('sensor inválido deve usar soil_index null')
        if self.source == 'simulation' and not self.experiment_id.startswith('sim-'):
            raise ValueError('experimento simulado exige prefixo sim-')
        if self.source == 'physical' and self.experiment_id.startswith('sim-'):
            raise ValueError('experimento físico não pode usar prefixo sim-')
        if self.source == 'simulation' and not self.device_id.startswith('sim-'):
            raise ValueError('dispositivo simulado exige prefixo sim-')
        if self.source == 'physical' and self.device_id.startswith('sim-'):
            raise ValueError('dispositivo simulado não pode produzir dado físico')
        return self

class StrictContract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class IrrigationCommand(StrictContract):
    schema_version: Literal[1] = 1
    command_id: UUID
    decision_id: UUID | None = None
    device_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    experiment_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    unit_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    treatment: Literal['T1', 'T2', 'T3']
    source: Literal['simulation', 'physical']
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    target_volume_ml: float = Field(gt=0)
    max_duration_s: float = Field(gt=0, le=300)
    reason: str = Field(min_length=1, max_length=300)
    model_version: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def consistency(self):
        if not timedelta(0) < self.expires_at - self.issued_at <= timedelta(minutes=5):
            raise ValueError('validade deve estar entre zero e cinco minutos')
        if self.source == 'simulation' and not (self.device_id.startswith('sim-') and self.experiment_id.startswith('sim-')):
            raise ValueError('simulação exige prefixos sim-')
        if self.source == 'physical' and (self.device_id.startswith('sim-') or self.experiment_id.startswith('sim-')):
            raise ValueError('prefixo sim- não pode atuar em fonte física')
        return self


class CommandEvent(StrictContract):
    treatment: Literal['T1', 'T2', 'T3'] | None = None
    origin: Literal['remote', 'local'] = 'remote'
    experiment_id: str | None = None
    unit_id: str | None = None
    schema_version: Literal[1] = 1
    event_id: UUID
    command_id: UUID
    device_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    source: Literal['simulation', 'physical']
    occurred_at: AwareDatetime
    status: Literal['accepted', 'rejected', 'started', 'completed', 'failed']
    reason: str = Field(min_length=1, max_length=300)
    duration_s: float | None = Field(default=None, ge=0)
    volume_ml: float | None = Field(default=None, ge=0)
    energy_wh: float | None = Field(default=None, ge=0)
    measurements_valid: bool = False

    @model_validator(mode='after')
    def consistency(self):
        if self.occurred_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError('evento no futuro')
        if self.origin == 'local' and (self.source != 'simulation' or not self.device_id.startswith('sim-') or not self.experiment_id or not self.experiment_id.startswith('sim-') or not self.unit_id):
            raise ValueError('ciclo local exige simulação e contexto explícito')
        if not self.measurements_valid and (self.volume_ml is not None or self.energy_wh is not None):
            raise ValueError('medições inválidas devem ser null')
        return self


ExecutionEvent = CommandEvent


class Prediction(StrictContract):
    prediction_id: UUID
    device_id: str
    telemetry_message_id: UUID
    source: Literal['simulation', 'physical']
    predicted_at: AwareDatetime
    valid_until: AwareDatetime
    horizon_s: Literal[1800] = 1800
    soil_index: float = Field(ge=0, le=100)
    model_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def consistency(self):
        if self.valid_until <= self.predicted_at or self.valid_until - self.predicted_at > timedelta(seconds=self.horizon_s):
            raise ValueError('validade da previsão fora dos limites')
        if self.predicted_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError('previsão no futuro')
        return self


class DecisionConfiguration(StrictContract):
    device_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$')
    experiment_id: str
    unit_id: str
    treatment: Literal['T1', 'T2', 'T3']
    source: Literal['simulation', 'physical']
    enabled: bool = False
    calibration_id: str = Field(min_length=1)
    soil_low: float = Field(ge=0, le=100)
    soil_high: float = Field(ge=0, le=100)
    min_reservoir_mass_g: float = Field(ge=0, description='Massa mínima na mesma referência/tara da telemetria; definida por calibração incluindo reserva para não operar a seco')
    target_volume_ml: float = Field(gt=0)
    max_duration_s: float = Field(gt=0, le=300)
    max_telemetry_age_s: int = Field(gt=0, le=3600)
    cooldown_s: int = Field(gt=0)
    schedule_interval_s: int | None = Field(default=None, gt=0)
    prediction_fallback_enabled: bool = False
    stabilization_s: float = Field(default=0,ge=0,le=3600)
    max_daily_volume_ml: float | None = Field(default=None, gt=0, description='Teto por vaso em 24 h (protocolo: 300 mL)')
    model_path: str | None = None

    @model_validator(mode='after')
    def consistency(self):
        if self.soil_low >= self.soil_high:
            raise ValueError('histerese exige low < high')
        if self.treatment == 'T1' and self.schedule_interval_s is None:
            raise ValueError('T1 exige intervalo')
        return self


class Intervention(StrictContract):
    intervention_id: UUID
    device_id: str
    experiment_id: str
    unit_id: str
    source: Literal['simulation','physical']
    kind: Literal['refill','maintenance','calibration','other']
    status: Literal['started','completed']
    occurred_at: AwareDatetime
    reason: str = Field(min_length=1,max_length=300)
    responsible: str | None = Field(default=None,max_length=80)
    invalidates_measurements: bool = True
