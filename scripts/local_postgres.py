"""Run a project-local PostgreSQL without installing a Windows service.

Place official PostgreSQL binaries under tmp/postgres/runtime/pgsql, or pass
--bin-dir. Data, credentials and logs stay in the Git-ignored tmp/postgres.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'tmp' / 'postgres'
DATA = STATE / 'data'
CONNECTION = STATE / 'connection.json'
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=STATE / 'runtime' / 'pgsql' / 'bin')
    parser.add_argument('action', choices=('start', 'stop', 'status', 'manage'))
    parser.add_argument('args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    suffix = '.exe' if os.name == 'nt' else ''

    def run(program, *args, **kwargs):
        # A spawned Windows server can inherit pipe handles and keep communicate()
        # waiting after pg_ctl exits. A file captures output without that deadlock.
        with tempfile.TemporaryFile() as output:
            kwargs.setdefault('stdout', output)
            kwargs.setdefault('stderr', subprocess.STDOUT)
            result = subprocess.run([str(options.bin_dir / (program + suffix)), *map(str, args)],
                                    creationflags=HIDDEN, **kwargs)
            output.seek(0)
            print(output.read().decode('utf-8', errors='replace'), end='')
            return result

    STATE.mkdir(parents=True, exist_ok=True)
    if options.action == 'status':
        return run('pg_ctl', '-D', DATA, 'status').returncode
    if options.action == 'stop':
        return run('pg_ctl', '-D', DATA, '-m', 'fast', '-w', 'stop').returncode
    if not CONNECTION.exists():
        if DATA.exists():
            parser.error('Existing cluster has no connection file; restore its credentials before continuing.')
        CONNECTION.write_text(json.dumps({'user': 'lostlink', 'password': secrets.token_urlsafe(32),
                                         'host': '127.0.0.1', 'port': 54329, 'database': 'lostlink'}))
    config = json.loads(CONNECTION.read_text())
    if options.action == 'start':
        if not (DATA / 'PG_VERSION').exists():
            password_file = STATE / 'init-password.txt'
            password_file.write_text(config['password'], encoding='utf-8')
            try:
                run('initdb', '-D', DATA, '-U', config['user'], '-A', 'scram-sha-256',
                    '--encoding=UTF8', '--locale=C', f'--pwfile={password_file}', check=True)
            finally:
                password_file.unlink(missing_ok=True)
        status = run('pg_ctl', '-D', DATA, 'status', stdout=subprocess.DEVNULL)
        if status.returncode:
            run('pg_ctl', '-D', DATA, '-l', STATE / 'server.log',
                '-o', f"-h 127.0.0.1 -p {config['port']}", '-w', 'start', check=True)
        import psycopg
        from psycopg import sql
        with psycopg.connect(host=config['host'], port=config['port'], user=config['user'],
                             password=config['password'], dbname='postgres', autocommit=True) as conn:
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', (config['database'],)).fetchone():
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(config['database'])))
            print(conn.execute('SELECT version()').fetchone()[0])
        print(f"Local database ready at 127.0.0.1:{config['port']}/{config['database']}")
        return 0
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['DATABASE_URL'] = (f"postgresql://{quote(config['user'], safe='')}:{quote(config['password'], safe='')}"
                           f"@{config['host']}:{config['port']}/{config['database']}?sslmode=disable")
    if not options.args:
        parser.error('manage requires a Django command, such as migrate, test, or runserver.')
    return subprocess.run([sys.executable, str(ROOT / 'manage.py'), *options.args],
                          cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
