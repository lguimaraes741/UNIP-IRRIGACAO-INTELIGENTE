CREATE TABLE telemetry (
    device_id text NOT NULL,
    message_id uuid NOT NULL,
    collected_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    experiment_id text NOT NULL,
    unit_id text NOT NULL,
    treatment text NOT NULL CHECK (treatment IN ('T1','T2','T3')),
    source text NOT NULL CHECK (source IN ('physical','simulation')),
    sensor_valid boolean NOT NULL,
    soil_index double precision CHECK (soil_index BETWEEN 0 AND 100),
    payload jsonb NOT NULL,
    PRIMARY KEY (device_id, message_id)
);
CREATE INDEX telemetry_experiment_time ON telemetry (experiment_id, source, collected_at);
