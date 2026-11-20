#!/bin/sh
set -eu
mkdir -p /backups
while true; do
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    target="/backups/daily-$stamp.dump"
    pg_dump -h postgres -U grafana_reader -d irrigation -Fc --no-owner --no-acl -f "$target.partial"
    mv "$target.partial" "$target"
    sha256sum "$target" > "$target.sha256"
    echo "Backup diário concluído: daily-$stamp.dump"
    sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done
