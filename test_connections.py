"""
Script de teste para validar conexões com SAP e Supabase antes de executar.
Execute: python test_connections.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Optional

from config import get_settings
from extract_sap_to_supabase import get_sqlserver_connection
from sap_connection import connect_sap_hana

# Garantir saída UTF-8 no console (Windows usa cp1252 e quebra com ✓/❌)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def test_python_packages() -> bool:
    """Verifica se os pacotes Python obrigatórios estão instalados."""
    print("\n" + "=" * 60)
    print("VERIFICANDO PACOTES PYTHON")
    print("=" * 60)

    packages = {
        'hdbcli': 'SAP HANA',
        'supabase': 'Supabase',
        'pandas': 'Pandas',
        'dotenv': 'Python-dotenv',
        'pyodbc': 'SQL Server (pyodbc)',
        'apscheduler': 'Agendador (APScheduler)',
    }

    all_ok = True
    for package, name in packages.items():
        try:
            __import__(package)
            print(f"✓ {name} ({package})")
        except ImportError:
            print(f"❌ {name} ({package}) - não instalado")
            all_ok = False

    if not all_ok:
        print("\nInstale os pacotes faltantes com:")
        print("  pip install -r requirements.txt")

    return all_ok


def test_sap_connection() -> bool:
    """Testa a conexão com o SAP HANA executando uma query simples."""
    print("\n" + "=" * 60)
    print("TESTANDO CONEXÃO SAP HANA")
    print("=" * 60)

    settings = get_settings()

    print(f"Host: {settings.sap_host}")
    print(f"Port: {settings.sap_port}")
    print(f"User: {settings.sap_user}")
    print(f"Database: {settings.sap_database or '(não informado — opcional)'}")

    if not settings.sap_ready():
        print("❌ Faltam variáveis de ambiente SAP:")
        if not settings.sap_host:
            print("   - SAP_HOST")
        if not settings.sap_user:
            print("   - SAP_USER")
        if not settings.sap_password:
            print("   - SAP_PASSWORD")
        return False

    try:
        conn = connect_sap_hana(
            settings.sap_host,
            settings.sap_port,
            settings.sap_user,
            settings.sap_password,
            settings.sap_database,
            with_timeouts=False,
            with_retry=False,
        )

        cursor = conn.cursor()
        cursor.execute("SELECT CURRENT_DATE FROM DUMMY")
        result = cursor.fetchone()

        print("✓ Conectado com sucesso!")
        print(f"  Data do servidor: {result[0]}")

        conn.close()
        return True
    except Exception as exc:
        print(f"❌ Erro ao conectar: {exc}")
        return False


def test_supabase_connection() -> bool:
    """Testa leitura Supabase com a chave anon."""
    print("\n" + "=" * 60)
    print("TESTANDO CONEXÃO SUPABASE (ANON — LEITURA)")
    print("=" * 60)

    settings = get_settings()

    print(f"URL: {settings.supabase_url}")
    print(f"Tabela: {settings.table_name}")

    if not settings.supabase_url or not settings.supabase_key:
        print("❌ Faltam variáveis de ambiente Supabase:")
        if not settings.supabase_url:
            print("   - SUPABASE_URL")
        if not settings.supabase_key:
            print("   - SUPABASE_KEY")
        return False

    try:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_key)
        response = client.table(settings.table_name).select('*').limit(1).execute()

        print("✓ Leitura anon OK!")
        print(f"  Tabela '{settings.table_name}' acessível")
        print(f"  Registros na amostra: {len(response.data)}")

        return True
    except Exception as exc:
        print(f"❌ Erro ao conectar: {exc}")
        return False


def test_supabase_service_role_write() -> Optional[bool]:
    """Testa escrita com service_role (insert + delete de sonda no log).

    Returns:
        ``True`` se escrita OK; ``False`` se falhou; ``None`` se não configurado.
    """
    print("\n" + "=" * 60)
    print("TESTANDO SUPABASE (SERVICE_ROLE — ESCRITA)")
    print("=" * 60)

    settings = get_settings()

    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("⬜ SUPABASE_SERVICE_ROLE_KEY não configurada — pulando teste de escrita")
        return None

    try:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        tabela = settings.sync_log_table_name
        agora = datetime.now(timezone.utc).isoformat()

        insert = client.table(tabela).insert({
            'data_hora_sincronizacao': agora,
            'duracao_segundos': 0,
            'status': 'teste_conexao',
            'qtd_registros': 0,
        }).execute()

        if not insert.data:
            print("❌ Insert de sonda não retornou dados")
            return False

        probe_id = insert.data[0]['id']
        client.table(tabela).delete().eq('id', probe_id).execute()

        print("✓ Escrita service_role OK!")
        print(f"  Insert/delete de sonda na tabela '{tabela}' bem-sucedido")
        return True
    except Exception as exc:
        print(f"❌ Erro no teste de escrita service_role: {exc}")
        return False


def test_sqlserver_connection() -> Optional[bool]:
    """Testa conexão ao SQL Server (opcional).

    Returns:
        ``True``/``False`` se configurado; ``None`` se SQL Server não está no .env.
    """
    print("\n" + "=" * 60)
    print("TESTANDO CONEXÃO SQL SERVER")
    print("=" * 60)

    settings = get_settings()

    if not settings.sql_ready():
        print("⬜ SQL Server não configurado (SQL_HOST/USER/PASSWORD) — pulando")
        return None

    print(f"Host: {settings.sql_host}:{settings.sql_port}")
    print(f"Database: {settings.sql_database}")

    try:
        conn = get_sqlserver_connection(
            host=settings.sql_host,
            port=settings.sql_port,
            user=settings.sql_user,
            password=settings.sql_password,
            database=settings.sql_database,
            driver=settings.sql_driver,
        )
        if conn is None:
            print("❌ Não foi possível conectar (driver ODBC ou credenciais)")
            return False

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        conn.close()

        print("✓ Conectado com sucesso!")
        return True
    except Exception as exc:
        print(f"❌ Erro ao conectar: {exc}")
        return False


def test_view_exists() -> bool:
    """Lista as primeiras views disponíveis no SAP HANA."""
    print("\n" + "=" * 60)
    print("TESTANDO VIEWS SAP")
    print("=" * 60)

    settings = get_settings()

    if not settings.sap_ready():
        print("❌ Credenciais SAP incompletas; pulando teste de views.")
        return False

    try:
        conn = connect_sap_hana(
            settings.sap_host,
            settings.sap_port,
            settings.sap_user,
            settings.sap_password,
            settings.sap_database,
            with_timeouts=False,
            with_retry=False,
        )

        cursor = conn.cursor()
        cursor.execute("""
        SELECT SCHEMA_NAME, VIEW_NAME
        FROM SYS.VIEWS
        WHERE SCHEMA_NAME NOT IN ('SYS', '_SYS_')
        ORDER BY SCHEMA_NAME, VIEW_NAME
        LIMIT 20
        """)
        views = cursor.fetchall()

        print("✓ Views disponíveis (primeiras 20):")
        for view in views:
            print(f"  - {view[0]}.{view[1]}")

        conn.close()
        return True
    except Exception as exc:
        print(f"⚠ Não foi possível listar views: {exc}")
        return False


def main() -> bool:
    """Executa a bateria de testes (pacotes, .env e conexões)."""
    print("\n" + "=" * 60)
    print("TESTE DE CONEXÕES - SAP B1 TO SUPABASE")
    print("=" * 60)

    if not test_python_packages():
        print("\n❌ Instale os pacotes faltantes antes de continuar")
        return False

    if not os.path.exists('.env'):
        print("\n⚠ Arquivo .env não encontrado!")
        print("  Copie .env.example para .env e preencha os dados")
        return False

    sap_ok = test_sap_connection()
    supabase_ok = test_supabase_connection()
    service_role_ok = test_supabase_service_role_write()
    sql_ok = test_sqlserver_connection()

    if sap_ok:
        test_view_exists()

    print("\n" + "=" * 60)
    print("RESUMO DOS TESTES")
    print("=" * 60)
    print(f"SAP HANA:       {'✓' if sap_ok else '❌'}")
    print(f"Supabase anon:  {'✓' if supabase_ok else '❌'}")
    if service_role_ok is None:
        print("Supabase write: ⬜ (service_role não configurada)")
    else:
        print(f"Supabase write: {'✓' if service_role_ok else '❌'}")
    if sql_ok is None:
        print("SQL Server:     ⬜ (não configurado)")
    else:
        print(f"SQL Server:     {'✓' if sql_ok else '❌'}")

    core_ok = sap_ok and supabase_ok
    write_ok = service_role_ok is not False

    if core_ok and write_ok:
        print("\n✓ Conexões principais OK!")
        print("\nVocê pode executar:")
        print("  python extract_sap_to_supabase.py")
        return True

    print("\n❌ Existem problemas nas conexões. Verifique as mensagens acima.")
    return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
