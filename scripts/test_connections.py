"""Pre-flight connectivity tests. Run: python scripts/test_connections.py"""

from __future__ import annotations

import scripts._bootstrap  # noqa: F401

import os
import sys
from datetime import datetime, timezone
from typing import Optional

from config import get_settings
from extract_sap_to_supabase import get_sqlserver_connection
from sap_connection import connect_sap_hana

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass


def test_python_packages() -> bool:
    print('\n' + '=' * 60)
    print('PYTHON PACKAGES')
    print('=' * 60)
    packages = {
        'hdbcli': 'SAP HANA',
        'supabase': 'Supabase',
        'pandas': 'Pandas',
        'dotenv': 'python-dotenv',
        'pyodbc': 'SQL Server (pyodbc)',
        'apscheduler': 'APScheduler',
    }
    ok = True
    for pkg, label in packages.items():
        try:
            __import__(pkg)
            print(f'✓ {label} ({pkg})')
        except ImportError:
            print(f'❌ {label} ({pkg}) — not installed')
            ok = False
    if not ok:
        print('\nInstall: pip install -r requirements.txt')
    return ok


def test_sap_connection() -> bool:
    print('\n' + '=' * 60)
    print('SAP HANA')
    print('=' * 60)
    s = get_settings()
    print(f'Host: {s.sap_host}\nPort: {s.sap_port}\nUser: {s.sap_user}')
    print(f'Database: {s.sap_database or "(optional, not set)"}')
    if not s.sap_ready():
        print('❌ Missing SAP_HOST / SAP_USER / SAP_PASSWORD')
        return False
    try:
        conn = connect_sap_hana(
            s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database,
            with_timeouts=False, with_retry=False,
        )
        cur = conn.cursor()
        cur.execute('SELECT CURRENT_DATE FROM DUMMY')
        print(f'✓ OK — server date: {cur.fetchone()[0]}')
        conn.close()
        return True
    except Exception as exc:
        print(f'❌ {exc}')
        return False


def test_supabase_connection() -> bool:
    print('\n' + '=' * 60)
    print('SUPABASE (anon read)')
    print('=' * 60)
    s = get_settings()
    if not s.supabase_url or not s.supabase_key:
        print('❌ Missing SUPABASE_URL / SUPABASE_KEY')
        return False
    try:
        from supabase import create_client
        client = create_client(s.supabase_url, s.supabase_key)
        res = client.table(s.table_name).select('*').limit(1).execute()
        print(f"✓ Table '{s.table_name}' OK — sample rows: {len(res.data)}")
        return True
    except Exception as exc:
        print(f'❌ {exc}')
        return False


def test_supabase_service_role_write() -> Optional[bool]:
    print('\n' + '=' * 60)
    print('SUPABASE (service_role write)')
    print('=' * 60)
    s = get_settings()
    if not s.supabase_url or not s.supabase_service_role_key:
        print('⬜ SUPABASE_SERVICE_ROLE_KEY not set — skipped')
        return None
    try:
        from supabase import create_client
        client = create_client(s.supabase_url, s.supabase_service_role_key)
        ins = client.table(s.sync_log_table_name).insert({
            'data_hora_sincronizacao': datetime.now(timezone.utc).isoformat(),
            'duracao_segundos': 0,
            'status': 'teste_conexao',
            'qtd_registros': 0,
        }).execute()
        if not ins.data:
            return False
        probe_id = ins.data[0]['id']
        client.table(s.sync_log_table_name).delete().eq('id', probe_id).execute()
        print(f"✓ Insert/delete probe OK on '{s.sync_log_table_name}'")
        return True
    except Exception as exc:
        print(f'❌ {exc}')
        return False


def test_sqlserver_connection() -> Optional[bool]:
    print('\n' + '=' * 60)
    print('SQL SERVER')
    print('=' * 60)
    s = get_settings()
    if not s.sql_ready():
        print('⬜ Not configured — skipped')
        return None
    try:
        conn = get_sqlserver_connection(
            s.sql_host, s.sql_port, s.sql_user, s.sql_password, s.sql_database, s.sql_driver,
        )
        if conn is None:
            print('❌ Connection failed (ODBC driver or credentials)')
            return False
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        conn.close()
        print('✓ OK')
        return True
    except Exception as exc:
        print(f'❌ {exc}')
        return False


def test_view_exists() -> bool:
    print('\n' + '=' * 60)
    print('SAP VIEWS (sample)')
    print('=' * 60)
    s = get_settings()
    if not s.sap_ready():
        return False
    try:
        conn = connect_sap_hana(
            s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database,
            with_timeouts=False, with_retry=False,
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT SCHEMA_NAME, VIEW_NAME FROM SYS.VIEWS
            WHERE SCHEMA_NAME NOT IN ('SYS', '_SYS_')
            ORDER BY SCHEMA_NAME, VIEW_NAME LIMIT 20
        """)
        for row in cur.fetchall():
            print(f'  - {row[0]}.{row[1]}')
        conn.close()
        return True
    except Exception as exc:
        print(f'⚠ {exc}')
        return False


def main() -> bool:
    print('\n' + '=' * 60)
    print('CONNECTION TESTS')
    print('=' * 60)
    if not test_python_packages():
        return False
    if not os.path.exists('.env'):
        print('\n⚠ .env not found — copy from .env.example')
        return False

    sap_ok = test_sap_connection()
    supa_ok = test_supabase_connection()
    write_ok = test_supabase_service_role_write()
    sql_ok = test_sqlserver_connection()
    if sap_ok:
        test_view_exists()

    print('\n' + '=' * 60)
    print('SUMMARY')
    print('=' * 60)
    print(f'SAP:            {"✓" if sap_ok else "❌"}')
    print(f'Supabase read:  {"✓" if supa_ok else "❌"}')
    print(f'Supabase write: {"⬜" if write_ok is None else "✓" if write_ok else "❌"}')
    print(f'SQL Server:     {"⬜" if sql_ok is None else "✓" if sql_ok else "❌"}')

    if sap_ok and supa_ok and write_ok is not False:
        print('\n✓ Ready — run: python extract_sap_to_supabase.py')
        return True
    return False


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
