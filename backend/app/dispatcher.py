"""Durable FIFO commands with one globally shared executor per device."""
import logging
import os
import time
from datetime import datetime, timezone
from app.store import connect, migrate, device_barrier, revalidate_queued
from app.worker import make_client

log=logging.getLogger('dispatcher')


def dispatch_once(client):
    with connect() as conn:
        rows=conn.execute('''SELECT c.command_id,c.device_id,c.expires_at,c.payload,c.status
            FROM command_outbox o JOIN commands c USING(command_id)
            WHERE o.published_at IS NULL ORDER BY c.issued_at
            FOR UPDATE OF o SKIP LOCKED LIMIT 20''').fetchall()
        now=datetime.now(timezone.utc)
        for row in rows:
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(row['device_id'],))
            if row['status']!='queued' or row['expires_at']<=now:
                conn.execute('UPDATE command_outbox SET published_at=now() WHERE command_id=%s',(row['command_id'],))
                if row['status']=='queued':
                    conn.execute("UPDATE commands SET status='expired' WHERE command_id=%s",(row['command_id'],))
                return True
            if device_barrier(conn,row['device_id'],now):
                continue
            reason=revalidate_queued(conn,row['payload'],now)
            if reason:
                conn.execute("UPDATE commands SET status='rejected' WHERE command_id=%s",(row['command_id'],))
                conn.execute('UPDATE command_outbox SET published_at=now() WHERE command_id=%s',(row['command_id'],))
                # This is a server cancellation, not a fabricated actuation confirmation.
                from app.store import save_rejection
                conn.execute('INSERT INTO rejected_messages(topic,reason,payload) VALUES(%s,%s,%s)',(f"irrigation/v1/{row['device_id']}/commands",'queued_revalidation:'+reason,str(row['command_id']).encode()))
                return True
            import json
            result=client.publish(f"irrigation/v1/{row['device_id']}/commands",json.dumps(row['payload']),qos=1,retain=False)
            result.wait_for_publish(timeout=10)
            if not result.is_published():
                raise RuntimeError('Publicação MQTT não confirmada')
            conn.execute('UPDATE command_outbox SET published_at=now(),attempts=attempts+1 WHERE command_id=%s',(row['command_id'],))
            conn.execute("UPDATE commands SET status='published' WHERE command_id=%s AND status='queued'",(row['command_id'],))
            return True
    return False


if __name__=='__main__':
    migrate()
    client=make_client(client_id='tcc-dispatcher-v1')
    client.on_message=None
    client.on_connect=None
    client.connect(os.environ.get('MQTT_HOST','mosquitto'),8883,keepalive=30)
    client.loop_start()
    while True:
        if not dispatch_once(client):
            time.sleep(0.5)
