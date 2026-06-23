"""
Script de teste para validar conexões com SAP e Supabase antes de executar.
Execute: python test_connections.py
"""

import os
import sys

from config import get_settings
from sap_connection import connect_sap_hana

# Garantir saída UTF-8 no console (Windows usa cp1252 e quebra com ✓/❌)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def test_sap_connection() -> bool:
    """Testa a conexão com o SAP HANA executando uma query simples.

    Returns:
        ``True`` se conectou e a query retornou; ``False`` caso contrário.
    """
    print("\n" + "=" * 60)
    print("TESTANDO CONEXÃO SAP HANA")
    print("=" * 60)

    try:
        from hdbcli import dbapi  # noqa: F401 — verifica instalação
    except ImportError:
        print("❌ hdbcli não instalado. Execute: pip install hdbcli")
        return False

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
    """Testa a conexão com o Supabase lendo um registro da tabela de destino.

    Returns:
        ``True`` se a tabela é acessível; ``False`` caso contrário.
    """
    print("\n" + "=" * 60)
    print("TESTANDO CONEXÃO SUPABASE")
    print("=" * 60)

    try:
        from supabase import create_client
    except ImportError:
        print("❌ supabase não instalado. Execute: pip install supabase")
        return False

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
        client = create_client(settings.supabase_url, settings.supabase_key)
        response = client.table(settings.table_name).select('*').limit(1).execute()

        print("✓ Conectado com sucesso!")
        print(f"  Tabela '{settings.table_name}' acessível")
        print(f"  Registros na amostra: {len(response.data)}")

        return True
    except Exception as exc:
        print(f"❌ Erro ao conectar: {exc}")
        return False


def test_view_exists() -> bool:
    """Lista as primeiras views disponíveis no SAP HANA (sanity check de acesso).

    Returns:
        ``True`` se conseguiu listar as views; ``False`` caso contrário.
    """
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
        query = """
        SELECT SCHEMA_NAME, VIEW_NAME
        FROM SYS.VIEWS
        WHERE SCHEMA_NAME NOT IN ('SYS', '_SYS_')
        ORDER BY SCHEMA_NAME, VIEW_NAME
        LIMIT 20
        """
        cursor.execute(query)
        views = cursor.fetchall()

        print("✓ Views disponíveis (primeiras 20):")
        for view in views:
            print(f"  - {view[0]}.{view[1]}")

        conn.close()
        return True
    except Exception as exc:
        print(f"⚠ Não foi possível listar views: {exc}")
        return False


def test_python_packages() -> bool:
    """Verifica se os pacotes Python obrigatórios estão instalados.

    Returns:
        ``True`` se todos os pacotes foram importados; ``False`` se algum faltou.
    """
    print("\n" + "=" * 60)
    print("VERIFICANDO PACOTES PYTHON")
    print("=" * 60)

    packages = {
        'hdbcli': 'SAP HANA',
        'supabase': 'Supabase',
        'pandas': 'Pandas',
        'dotenv': 'Python-dotenv',
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


def main() -> bool:
    """Executa toda a bateria de testes (pacotes, .env e conexões).

    Returns:
        ``True`` se SAP e Supabase conectaram; ``False`` caso contrário.
    """
    print("\n" + "=" * 60)
    print("TESTE DE CONEXÕES - SAP B1 TO SUPABASE")
    print("=" * 60)

    packages_ok = test_python_packages()
    if not packages_ok:
        print("\n❌ Instale os pacotes faltantes antes de continuar")
        return False

    if not os.path.exists('.env'):
        print("\n⚠ Arquivo .env não encontrado!")
        print("  Copie .env.example para .env e preencha os dados")
        return False

    sap_ok = test_sap_connection()
    supabase_ok = test_supabase_connection()

    if sap_ok:
        test_view_exists()

    print("\n" + "=" * 60)
    print("RESUMO DOS TESTES")
    print("=" * 60)
    print(f"SAP HANA:  {'✓' if sap_ok else '❌'}")
    print(f"Supabase:  {'✓' if supabase_ok else '❌'}")

    if sap_ok and supabase_ok:
        print("\n✓ Todas as conexões estão OK!")
        print("\nVocê pode executar:")
        print("  python extract_sap_to_supabase.py")
        return True

    print("\n❌ Existem problemas nas conexões. Verifique as mensagens acima.")
    return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
