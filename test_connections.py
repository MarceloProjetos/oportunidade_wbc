"""
Script de teste para validar conexões com SAP e Supabase antes de executar.
Execute: python test_connections.py
"""

import os
import sys
from dotenv import load_dotenv

# Garantir saída UTF-8 no console (Windows usa cp1252 e quebra com ✓/❌)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# Carregar variáveis de ambiente
load_dotenv()


def test_sap_connection() -> bool:
    """Testa a conexão com o SAP HANA executando uma query simples.

    Returns:
        ``True`` se conectou e a query retornou; ``False`` caso contrário.
    """
    print("\n" + "="*60)
    print("TESTANDO CONEXÃO SAP HANA")
    print("="*60)
    
    try:
        from hdbcli import dbapi
    except ImportError:
        print("❌ hdbcli não instalado. Execute: pip install hdbcli")
        return False
    
    # Carregar configurações
    sap_host = os.getenv('SAP_HOST')
    sap_port = int(os.getenv('SAP_PORT', 30013))
    sap_user = os.getenv('SAP_USER')
    sap_password = os.getenv('SAP_PASSWORD')
    sap_database = os.getenv('SAP_DATABASE')
    
    print(f"Host: {sap_host}")
    print(f"Port: {sap_port}")
    print(f"User: {sap_user}")
    print(f"Database: {sap_database}")
    
    if not all([sap_host, sap_user, sap_password, sap_database]):
        print("❌ Faltam variáveis de ambiente SAP:")
        if not sap_host:
            print("   - SAP_HOST")
        if not sap_user:
            print("   - SAP_USER")
        if not sap_password:
            print("   - SAP_PASSWORD")
        if not sap_database:
            print("   - SAP_DATABASE")
        return False
    
    try:
        connect_args = {
            'address': sap_host,
            'port': sap_port,
            'user': sap_user,
            'password': sap_password,
            'CHARSET': 'UTF8'
        }

        if sap_database:
            connect_args['databaseName'] = sap_database

        try:
            conn = dbapi.connect(**connect_args)
        except Exception as initial_error:
            message = str(initial_error).lower()
            if sap_database and 'not connected' in message:
                print(f"⚠ Database '{sap_database}' não conectado. Tentando sem databaseName...")
                connect_args.pop('databaseName', None)
                conn = dbapi.connect(**connect_args)
            else:
                raise

        # Testar query simples
        cursor = conn.cursor()
        cursor.execute("SELECT CURRENT_DATE FROM DUMMY")
        result = cursor.fetchone()
        
        print(f"✓ Conectado com sucesso!")
        print(f"  Data do servidor: {result[0]}")
        
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Erro ao conectar: {e}")
        return False


def test_supabase_connection() -> bool:
    """Testa a conexão com o Supabase lendo um registro da tabela de destino.

    Returns:
        ``True`` se a tabela é acessível; ``False`` caso contrário.
    """
    print("\n" + "="*60)
    print("TESTANDO CONEXÃO SUPABASE")
    print("="*60)
    
    try:
        from supabase import create_client
    except ImportError:
        print("❌ supabase não instalado. Execute: pip install supabase")
        return False
    
    # Carregar configurações
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_KEY')
    table_name = os.getenv('TABLE_NAME', 'oportunidades')
    
    print(f"URL: {supabase_url}")
    print(f"Tabela: {table_name}")
    
    if not all([supabase_url, supabase_key]):
        print("❌ Faltam variáveis de ambiente Supabase:")
        if not supabase_url:
            print("   - SUPABASE_URL")
        if not supabase_key:
            print("   - SUPABASE_KEY")
        return False
    
    try:
        client = create_client(supabase_url, supabase_key)
        
        # Testar leitura da tabela
        response = client.table(table_name).select('*').limit(1).execute()
        
        print(f"✓ Conectado com sucesso!")
        print(f"  Tabela '{table_name}' acessível")
        print(f"  Registros na tabela: {len(response.data)}")
        
        return True
    except Exception as e:
        print(f"❌ Erro ao conectar: {e}")
        return False


def test_view_exists() -> bool:
    """Lista as primeiras views disponíveis no SAP HANA (sanity check de acesso).

    Returns:
        ``True`` se conseguiu listar as views; ``False`` caso contrário.
    """
    print("\n" + "="*60)
    print("TESTANDO VIEWS SAP")
    print("="*60)
    
    try:
        from hdbcli import dbapi
    except ImportError:
        print("❌ hdbcli não instalado. Pulando teste de view.")
        return False
    
    # Carregar configurações
    sap_host = os.getenv('SAP_HOST')
    sap_port = int(os.getenv('SAP_PORT', 30013))
    sap_user = os.getenv('SAP_USER')
    sap_password = os.getenv('SAP_PASSWORD')
    sap_database = os.getenv('SAP_DATABASE')
    
    try:
        connect_args = {
            'address': sap_host,
            'port': sap_port,
            'user': sap_user,
            'password': sap_password,
            'CHARSET': 'UTF8'
        }

        if sap_database:
            connect_args['databaseName'] = sap_database

        try:
            conn = dbapi.connect(**connect_args)
        except Exception as initial_error:
            message = str(initial_error).lower()
            if sap_database and 'not connected' in message:
                print(f"⚠ Database '{sap_database}' não conectado. Tentando sem databaseName...")
                connect_args.pop('databaseName', None)
                conn = dbapi.connect(**connect_args)
            else:
                raise

        cursor = conn.cursor()
        
        # Listar views disponíveis
        query = """
        SELECT SCHEMA_NAME, VIEW_NAME
        FROM SYS.VIEWS
        WHERE SCHEMA_NAME NOT IN ('SYS', '_SYS_')
        ORDER BY SCHEMA_NAME, VIEW_NAME
        LIMIT 20
        """
        cursor.execute(query)
        views = cursor.fetchall()
        
        print(f"✓ Views disponíveis (primeiras 20):")
        for view in views:
            print(f"  - {view[0]}")
        
        conn.close()
        return True
    except Exception as e:
        print(f"⚠ Não foi possível listar views: {e}")
        return False


def test_python_packages() -> bool:
    """Verifica se os pacotes Python obrigatórios estão instalados.

    Returns:
        ``True`` se todos os pacotes foram importados; ``False`` se algum faltou.
    """
    print("\n" + "="*60)
    print("VERIFICANDO PACOTES PYTHON")
    print("="*60)
    
    packages = {
        'hdbcli': 'SAP HANA',
        'supabase': 'Supabase',
        'pandas': 'Pandas',
        'dotenv': 'Python-dotenv'
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
    print("\n" + "="*60)
    print("TESTE DE CONEXÕES - SAP B1 TO SUPABASE")
    print("="*60)
    
    # 1. Verificar pacotes
    packages_ok = test_python_packages()
    
    if not packages_ok:
        print("\n❌ Instale os pacotes faltantes antes de continuar")
        return False
    
    # 2. Testar arquivo .env
    if not os.path.exists('.env'):
        print("\n⚠ Arquivo .env não encontrado!")
        print("  Copie .env.example para .env e preencha os dados")
        return False
    
    # 3. Testar conexões
    sap_ok = test_sap_connection()
    supabase_ok = test_supabase_connection()
    
    if sap_ok:
        test_view_exists()
    
    # Resumo
    print("\n" + "="*60)
    print("RESUMO DOS TESTES")
    print("="*60)
    print(f"SAP HANA:  {'✓' if sap_ok else '❌'}")
    print(f"Supabase:  {'✓' if supabase_ok else '❌'}")
    
    if sap_ok and supabase_ok:
        print("\n✓ Todas as conexões estão OK!")
        print("\nVocê pode executar:")
        print("  python extract_sap_to_supabase.py")
        return True
    else:
        print("\n❌ Existem problemas nas conexões. Verifique as mensagens acima.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
