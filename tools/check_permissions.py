from app.store import connect

with connect() as conn:
    row = conn.execute("""SELECT current_user, rolsuper, rolcreatedb,
        has_schema_privilege(current_user,'public','CREATE') AS schema_create
        FROM pg_roles WHERE rolname=current_user""").fetchone()
    assert row['current_user'] == 'irrigation_app'
    assert not row['rolsuper'] and not row['rolcreatedb'] and not row['schema_create']
print('PASS: aplicação sem superusuário, criação de banco ou criação de objetos no schema.')
