"""Exemplos avançados de uso do extrator: filtro por data, transformação, validação
e relatório de execução.
"""

import sys
from datetime import datetime, timedelta

import pandas as pd

from config import get_settings
from extract_sap_to_supabase import SupabaseLoader, prepare_data
from sap_connection import SAPExtractor


def _sap_extractor() -> SAPExtractor:
    settings = get_settings()
    return SAPExtractor(
        settings.sap_host,
        settings.sap_port,
        settings.sap_user,
        settings.sap_password,
        settings.sap_database,
    )


def _supabase_loader() -> SupabaseLoader:
    settings = get_settings()
    return SupabaseLoader(settings.supabase_url, settings.supabase_key)


def exemplo_filtro_data() -> None:
    """Extrai e carrega apenas as cotações dos últimos 7 dias."""
    sap = _sap_extractor()
    if not sap.connect():
        return

    data_inicio = (datetime.now() - timedelta(days=7)).date()
    query = f"""
    SELECT * FROM SUA_VIEW_SAP
    WHERE DataCotacao >= '{data_inicio}'
    ORDER BY DataCotacao DESC
    """

    df = sap.execute_query(query)
    sap.close()

    if df is not None:
        print(f"Registros extraídos: {len(df)}")
        print(df.head())

        loader = _supabase_loader()
        data_to_insert, _exec_id = prepare_data(df)
        loader.insert_data(get_settings().table_name, data_to_insert)


def exemplo_com_transformacao() -> None:
    """Adiciona colunas calculadas (valor de comissão e categoria) antes de inserir."""
    sap = _sap_extractor()
    if not sap.connect():
        return

    df = sap.execute_query("SELECT * FROM SUA_VIEW_SAP")
    sap.close()
    if df is None:
        return

    if 'Valor' in df.columns and 'PctComissao' in df.columns:
        df['ValorComissao'] = df['Valor'] * df['PctComissao'] / 100

    if 'Valor' in df.columns:
        df['CategoriaValor'] = pd.cut(
            df['Valor'],
            bins=[0, 1000, 5000, 10000, float('inf')],
            labels=['Pequeno', 'Médio', 'Grande', 'Muito Grande'],
        )

    loader = _supabase_loader()
    data_to_insert, exec_id = prepare_data(df)
    loader.insert_data(get_settings().table_name, data_to_insert)
    print(f"Inserção concluída com ID: {exec_id}")


def exemplo_validacao_dados() -> None:
    """Valida e limpa os dados (duplicatas, nulos, datas) antes de inserir."""
    sap = _sap_extractor()
    if not sap.connect():
        return

    df = sap.execute_query("SELECT * FROM SUA_VIEW_SAP")
    sap.close()
    if df is None:
        return

    print(f"Total de registros: {len(df)}")

    df_limpo = df.drop_duplicates(subset=['CodPN', 'DataCotacao'], keep='last')
    print(f"Após remover duplicatas: {len(df_limpo)}")

    if 'Valor' in df_limpo.columns:
        df_limpo = df_limpo[df_limpo['Valor'].notna()]
        print(f"Após remover Valor nulo: {len(df_limpo)}")

    if 'CodPN' in df_limpo.columns:
        df_limpo = df_limpo[df_limpo['CodPN'].notna()]
        df_limpo = df_limpo[df_limpo['CodPN'].str.strip() != '']
        print(f"Após validar CodPN: {len(df_limpo)}")

    for col in ['DataCotacao', 'DataOport', 'DataCriacaoPN', 'DataContatoCliente']:
        if col in df_limpo.columns:
            df_limpo[col] = pd.to_datetime(df_limpo[col], errors='coerce')

    print(f"Total final para inserção: {len(df_limpo)}")

    loader = _supabase_loader()
    data_to_insert, exec_id = prepare_data(df_limpo)

    if data_to_insert:
        loader.insert_data(get_settings().table_name, data_to_insert)
        print(f"Inserção concluída com sucesso! ID de execução: {exec_id}")
    else:
        print("Nenhum dado válido para inserir")


def exemplo_relatorio_execucao() -> None:
    """Gera um relatório com estatísticas dos dados e então os insere."""
    sap = _sap_extractor()
    if not sap.connect():
        return

    df = sap.execute_query("SELECT * FROM SUA_VIEW_SAP")
    sap.close()
    if df is None:
        return

    print("\n" + "=" * 60)
    print("RELATÓRIO DE EXECUÇÃO")
    print("=" * 60)
    print(f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"Total de registros: {len(df)}")
    print(f"\nColunas: {len(df.columns)}")
    print(f"\nMemória utilizada: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

    if 'Valor' in df.columns:
        print("\nValor - Estatísticas:")
        print(f"  Soma: R$ {df['Valor'].sum():,.2f}")
        print(f"  Média: R$ {df['Valor'].mean():,.2f}")
        print(f"  Mínimo: R$ {df['Valor'].min():,.2f}")
        print(f"  Máximo: R$ {df['Valor'].max():,.2f}")

    if 'StatusWBC' in df.columns:
        print("\nStatusWBC - Distribuição:")
        print(df['StatusWBC'].value_counts())

    print("=" * 60 + "\n")

    loader = _supabase_loader()
    data_to_insert, exec_id = prepare_data(df)
    loader.insert_data(get_settings().table_name, data_to_insert)
    print("✓ Dados inseridos com sucesso!")
    print(f"  ID de execução: {exec_id}")


if __name__ == "__main__":
    print("Exemplos de uso avançado:")
    print("1. Filtro por data")
    print("2. Com transformação de dados")
    print("3. Com validação de dados")
    print("4. Com relatório de execução")

    if len(sys.argv) > 1:
        exemplo = sys.argv[1]
        if exemplo == '1':
            exemplo_filtro_data()
        elif exemplo == '2':
            exemplo_com_transformacao()
        elif exemplo == '3':
            exemplo_validacao_dados()
        elif exemplo == '4':
            exemplo_relatorio_execucao()
    else:
        print("\nUso: python exemplo_avancado.py <numero>")
        print("Exemplo: python exemplo_avancado.py 1")
