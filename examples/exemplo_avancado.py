"""Exemplos avançados de uso do extrator: filtro por data, transformação, validação
e relatório de execução.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pandas as pd

from extract_sap_to_supabase import SAPExtractor, SupabaseLoader, prepare_data

load_dotenv()


def exemplo_filtro_data() -> None:
    """Extrai e carrega apenas as cotações dos últimos 7 dias."""

    sap = SAPExtractor(
        host=os.getenv('SAP_HOST'),
        port=int(os.getenv('SAP_PORT', 30013)),
        user=os.getenv('SAP_USER'),
        password=os.getenv('SAP_PASSWORD'),
        database=os.getenv('SAP_DATABASE')
    )
    
    if not sap.connect():
        return
    
    # Exemplo: Últimos 7 dias
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
        
        # Preparar e inserir
        loader = SupabaseLoader(
            os.getenv('SUPABASE_URL'),
            os.getenv('SUPABASE_KEY')
        )
        data_to_insert, exec_id = prepare_data(df)
        loader.insert_data(os.getenv('TABLE_NAME', 'oportunidades'), data_to_insert)


def exemplo_com_transformacao() -> None:
    """Adiciona colunas calculadas (valor de comissão e categoria) antes de inserir."""

    sap = SAPExtractor(
        host=os.getenv('SAP_HOST'),
        port=int(os.getenv('SAP_PORT', 30013)),
        user=os.getenv('SAP_USER'),
        password=os.getenv('SAP_PASSWORD'),
        database=os.getenv('SAP_DATABASE')
    )
    
    if not sap.connect():
        return
    
    query = "SELECT * FROM SUA_VIEW_SAP"
    df = sap.execute_query(query)
    sap.close()
    
    if df is None:
        return
    
    # Adicionar coluna calculada
    if 'Valor' in df.columns and 'PctComissao' in df.columns:
        df['ValorComissao'] = df['Valor'] * df['PctComissao'] / 100
    
    # Adicionar categorização
    if 'Valor' in df.columns:
        df['CategoriaValor'] = pd.cut(
            df['Valor'],
            bins=[0, 1000, 5000, 10000, float('inf')],
            labels=['Pequeno', 'Médio', 'Grande', 'Muito Grande']
        )
    
    # Preparar e inserir
    loader = SupabaseLoader(
        os.getenv('SUPABASE_URL'),
        os.getenv('SUPABASE_KEY')
    )
    data_to_insert, exec_id = prepare_data(df)
    loader.insert_data(os.getenv('TABLE_NAME', 'oportunidades'), data_to_insert)
    
    print(f"Inserção concluída com ID: {exec_id}")


def exemplo_validacao_dados() -> None:
    """Valida e limpa os dados (duplicatas, nulos, datas) antes de inserir."""

    sap = SAPExtractor(
        host=os.getenv('SAP_HOST'),
        port=int(os.getenv('SAP_PORT', 30013)),
        user=os.getenv('SAP_USER'),
        password=os.getenv('SAP_PASSWORD'),
        database=os.getenv('SAP_DATABASE')
    )
    
    if not sap.connect():
        return
    
    query = "SELECT * FROM SUA_VIEW_SAP"
    df = sap.execute_query(query)
    sap.close()
    
    if df is None:
        return
    
    print(f"Total de registros: {len(df)}")
    
    # Validações
    # 1. Remover duplicatas por CodPN e DataCotacao
    df_limpo = df.drop_duplicates(subset=['CodPN', 'DataCotacao'], keep='last')
    print(f"Após remover duplicatas: {len(df_limpo)}")
    
    # 2. Remover registros com Valor nulo
    if 'Valor' in df_limpo.columns:
        df_limpo = df_limpo[df_limpo['Valor'].notna()]
        print(f"Após remover Valor nulo: {len(df_limpo)}")
    
    # 3. Validar CodPN não vazio
    if 'CodPN' in df_limpo.columns:
        df_limpo = df_limpo[df_limpo['CodPN'].notna()]
        df_limpo = df_limpo[df_limpo['CodPN'].str.strip() != '']
        print(f"Após validar CodPN: {len(df_limpo)}")
    
    # 4. Garantir que datas estão no formato correto
    date_columns = ['DataCotacao', 'DataOport', 'DataCriacaoPN', 'DataContatoCliente']
    for col in date_columns:
        if col in df_limpo.columns:
            df_limpo[col] = pd.to_datetime(df_limpo[col], errors='coerce')
    
    print(f"Total final para inserção: {len(df_limpo)}")
    
    # Preparar e inserir
    loader = SupabaseLoader(
        os.getenv('SUPABASE_URL'),
        os.getenv('SUPABASE_KEY')
    )
    data_to_insert, exec_id = prepare_data(df_limpo)
    
    if len(data_to_insert) > 0:
        loader.insert_data(os.getenv('TABLE_NAME', 'oportunidades'), data_to_insert)
        print(f"Inserção concluída com sucesso! ID de execução: {exec_id}")
    else:
        print("Nenhum dado válido para inserir")


def exemplo_relatorio_execucao() -> None:
    """Gera um relatório com estatísticas dos dados e então os insere."""

    sap = SAPExtractor(
        host=os.getenv('SAP_HOST'),
        port=int(os.getenv('SAP_PORT', 30013)),
        user=os.getenv('SAP_USER'),
        password=os.getenv('SAP_PASSWORD'),
        database=os.getenv('SAP_DATABASE')
    )
    
    if not sap.connect():
        return
    
    query = "SELECT * FROM SUA_VIEW_SAP"
    df = sap.execute_query(query)
    sap.close()
    
    if df is None:
        return
    
    # Gerar relatório
    print("\n" + "="*60)
    print("RELATÓRIO DE EXECUÇÃO")
    print("="*60)
    print(f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"Total de registros: {len(df)}")
    print(f"\nColunas: {len(df.columns)}")
    print(f"\nMemória utilizada: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")
    
    if 'Valor' in df.columns:
        print(f"\nValor - Estatísticas:")
        print(f"  Soma: R$ {df['Valor'].sum():,.2f}")
        print(f"  Média: R$ {df['Valor'].mean():,.2f}")
        print(f"  Mínimo: R$ {df['Valor'].min():,.2f}")
        print(f"  Máximo: R$ {df['Valor'].max():,.2f}")
    
    if 'StatusWBC' in df.columns:
        print(f"\nStatusWBC - Distribuição:")
        print(df['StatusWBC'].value_counts())
    
    print("="*60 + "\n")
    
    # Preparar e inserir
    loader = SupabaseLoader(
        os.getenv('SUPABASE_URL'),
        os.getenv('SUPABASE_KEY')
    )
    data_to_insert, exec_id = prepare_data(df)
    loader.insert_data(os.getenv('TABLE_NAME', 'oportunidades'), data_to_insert)
    
    print(f"✓ Dados inseridos com sucesso!")
    print(f"  ID de execução: {exec_id}")


if __name__ == "__main__":
    import sys
    
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
