-- Composite context via JSON fields preserves legacy row layout.
ALTER TABLE control_configs DROP CONSTRAINT IF EXISTS control_configs_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS control_configs_context ON control_configs(device_id,(payload->>'experiment_id'),(payload->>'unit_id'));
CREATE TABLE IF NOT EXISTS interventions (intervention_id uuid PRIMARY KEY,device_id text NOT NULL,experiment_id text NOT NULL,unit_id text NOT NULL,kind text NOT NULL,status text NOT NULL,started_at timestamptz NOT NULL,ended_at timestamptz,payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS controller_cursor (device_id text PRIMARY KEY,last_context text NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS telemetry_unit_time ON telemetry(device_id,experiment_id,unit_id,source,collected_at);
CREATE INDEX IF NOT EXISTS commands_context ON commands(device_id,(payload->>'experiment_id'),(payload->>'unit_id'),issued_at);
