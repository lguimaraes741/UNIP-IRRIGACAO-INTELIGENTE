"""Pure deterministic policy; no electrical/calibration defaults."""
import os
from datetime import datetime, timedelta
from app.contracts import DecisionConfiguration, Prediction, Telemetry


def source_enabled(source):
    """Each source needs its own explicit switch; physical actuation is off unless enabled."""
    if source == 'physical':
        return os.environ.get('PHYSICAL_CONTROL_ENABLED') == 'true'
    return os.environ.get('SIMULATION_CONTROL_ENABLED') == 'true'


def decide(config: DecisionConfiguration, telemetry: Telemetry | None, now: datetime,
           last_cycle_at: datetime | None = None, prediction: Prediction | None = None,
           active_command: bool = False, simulation_enabled: bool = False, hysteresis_latched: bool = False,
           daily_volume_ml: float = 0):
    # `simulation_enabled` = control enabled for config.source (see source_enabled).
    if not simulation_enabled:
        return False, 'physical_actuation_disabled' if config.source == 'physical' else 'control_disabled'
    if not config.enabled:
        return False, 'control_disabled'
    if active_command:
        return False, 'active_command'
    if telemetry is None:
        return False, 'missing_telemetry'
    if (telemetry.device_id, telemetry.unit_id, telemetry.experiment_id, telemetry.treatment, telemetry.source) != (config.device_id, config.unit_id, config.experiment_id, config.treatment, config.source):
        return False, 'telemetry_context_mismatch'
    age = (now - telemetry.collected_at).total_seconds()
    if age < 0 or age > config.max_telemetry_age_s:
        return False, 'stale_telemetry'
    if not telemetry.sensor_valid or telemetry.soil_index is None or telemetry.calibration_id != config.calibration_id:
        return False, 'invalid_or_uncalibrated_sensor'
    # Absolute mass is only meaningful for synthetic data: the physical scale drifts tens of grams
    # per degree, so the firmware guards physical water by refill accounting minus measured doses.
    if config.source == 'simulation' and (telemetry.reservoir_mass_g is None or telemetry.reservoir_mass_g < config.min_reservoir_mass_g):
        return False, 'insufficient_or_unknown_reservoir'
    if last_cycle_at and now < last_cycle_at + timedelta(seconds=config.cooldown_s):
        return False, 'cooldown'
    if config.max_daily_volume_ml is not None and daily_volume_ml + config.target_volume_ml > config.max_daily_volume_ml:
        return False, 'daily_volume_cap'
    if telemetry.soil_index >= config.soil_high:
        return False, 'soil_at_or_above_high'
    if config.treatment == 'T1':
        due = last_cycle_at is None or now >= last_cycle_at + timedelta(seconds=config.schedule_interval_s)
        return due, 'schedule_due' if due else 'schedule_wait'
    if config.treatment == 'T2':
        dry = telemetry.soil_index <= config.soil_low or hysteresis_latched
        return dry, ('soil_below_low' if telemetry.soil_index <= config.soil_low else 'hysteresis_latched') if dry else 'hysteresis_hold'
    valid_prediction = prediction and prediction.device_id == telemetry.device_id and prediction.source == telemetry.source and prediction.telemetry_message_id == telemetry.message_id and prediction.predicted_at <= now < prediction.valid_until
    if not valid_prediction:
        if config.prediction_fallback_enabled:
            return telemetry.soil_index <= config.soil_low or hysteresis_latched, 'prediction_unavailable_local_threshold_fallback'
        return False, 'prediction_unavailable'
    # Same stop rule as T2 (protocol 06/11): a prediction-triggered start latches until the
    # observed index reaches soil_high; only the start is anticipated by the 30 min forecast.
    if prediction.soil_index <= config.soil_low:
        return True, 'prediction_below_low'
    return (True, 'hysteresis_latched') if hysteresis_latched else (False, 'prediction_hold')


def hysteresis_state(config, telemetry, previous=False):
    # No invalid or foreign reading can create/change a calibrated latch.
    if telemetry is None or not telemetry.sensor_valid or telemetry.soil_index is None:
        return previous
    if (telemetry.device_id,telemetry.experiment_id,telemetry.unit_id,telemetry.source,telemetry.calibration_id,telemetry.treatment) != (config.device_id,config.experiment_id,config.unit_id,config.source,config.calibration_id,config.treatment):
        return previous
    if telemetry.soil_index >= config.soil_high:
        return False
    if telemetry.soil_index <= config.soil_low:
        return True
    return previous


def configuration_key(config):
    import hashlib
    return hashlib.sha256(config.model_dump_json().encode()).hexdigest()