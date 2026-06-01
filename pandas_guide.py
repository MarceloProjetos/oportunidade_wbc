"""Guia de manipulação de dados com pandas antes da inserção no Supabase.

Reúne funções utilitárias de inspeção, limpeza, transformação, filtragem, agregação
e validação de DataFrames, além de um pipeline de exemplo que as encadeia.
"""

from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd


# ============================================================================
# 1. LEITURA E INSPEÇÃO DE DADOS
# ============================================================================

def inspecionar_dados(df: pd.DataFrame) -> None:
    """Imprime um panorama do DataFrame (shape, memória, tipos, amostras e nulos).

    Args:
        df: DataFrame a inspecionar.
    """
    print("INFORMAÇÕES DO DATAFRAME")
    print("="*60)
    print(f"Shape: {df.shape} (linhas, colunas)")
    print(f"\nMemória: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")
    print("\nTipos de dados:")
    print(df.dtypes)
    print("\nPrimeiras linhas:")
    print(df.head())
    print("\nÚltimas linhas:")
    print(df.tail())
    print("\nValores ausentes:")
    print(df.isnull().sum())


# ============================================================================
# 2. LIMPEZA DE DADOS
# ============================================================================

def limpar_dados(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicatas e linhas-chave nulas, e normaliza valores sentinela.

    Args:
        df: DataFrame de entrada.

    Returns:
        DataFrame limpo.
    """
    # Remover duplicatas
    df = df.drop_duplicates()
    print(f"Após remover duplicatas: {len(df)} linhas")

    # Remover linhas onde coluna chave é nula
    df = df.dropna(subset=['CodPN', 'DataCotacao'])
    print(f"Após remover linhas com CodPN ou DataCotacao nula: {len(df)} linhas")

    # Substituir valores específicos
    df = df.replace({-1: None, '-': None, '': None})

    return df


def remover_duplicatas_por_chave(
    df: pd.DataFrame, chave_columns: Optional[List[str]] = None
) -> pd.DataFrame:
    """Remove duplicatas por chave mantendo o registro mais recente.

    Args:
        df: DataFrame de entrada.
        chave_columns: Colunas que definem a chave. Default: ``['CodPN', 'DataCotacao']``.

    Returns:
        DataFrame sem duplicatas por chave.
    """
    if chave_columns is None:
        chave_columns = ['CodPN', 'DataCotacao']
    df = df.sort_values('DataCotacao', ascending=False)
    df = df.drop_duplicates(subset=chave_columns, keep='first')
    return df


def tratar_valores_nulos(df: pd.DataFrame, estrategia: str = 'drop') -> pd.DataFrame:
    """Trata valores nulos conforme a estratégia escolhida.

    Args:
        df: DataFrame de entrada.
        estrategia: Como tratar os nulos:
            ``'drop'`` — remove linhas com nulos;
            ``'forward'`` — preenche com o valor anterior;
            ``'zero'`` — preenche com 0;
            ``'mean'`` — preenche colunas numéricas com a média.

    Returns:
        DataFrame com os nulos tratados.
    """
    if estrategia == 'drop':
        return df.dropna()
    elif estrategia == 'forward':
        return df.fillna(method='ffill')
    elif estrategia == 'zero':
        return df.fillna(0)
    elif estrategia == 'mean':
        numeric_columns = df.select_dtypes(include=[np.number]).columns
        for col in numeric_columns:
            df[col] = df[col].fillna(df[col].mean())
        return df

    return df


# ============================================================================
# 3. TRANSFORMAÇÃO DE DADOS
# ============================================================================

def transformar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Converte datas, números e strings para os tipos corretos.

    Args:
        df: DataFrame de entrada.

    Returns:
        DataFrame com os tipos ajustados.
    """
    # Datas
    date_columns = ['DataCotacao', 'DataOport', 'DataCriacaoPN', 'DataContatoCliente']
    for col in date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # Números
    numeric_columns = ['Cotacao', 'NumDoc', 'NumOport', 'Valor', 'PctComissao']
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Strings (remover espaços em branco)
    string_columns = df.select_dtypes(include=['object']).columns
    for col in string_columns:
        if col in df.columns:
            df[col] = df[col].str.strip()

    return df


def adicionar_colunas_calculadas(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona colunas derivadas (comissão, idade da cotação, categorias).

    Args:
        df: DataFrame de entrada.

    Returns:
        DataFrame com as colunas calculadas adicionadas.
    """
    # Valor da comissão
    if 'Valor' in df.columns and 'PctComissao' in df.columns:
        df['ValorComissao'] = df['Valor'] * df['PctComissao'] / 100

    # Dias desde a cotação
    if 'DataCotacao' in df.columns:
        df['DiasDesdeCotacao'] = (datetime.now() - df['DataCotacao']).dt.days

    # Categoria de valor
    if 'Valor' in df.columns:
        df['CategoriaValor'] = pd.cut(
            df['Valor'],
            bins=[0, 1000, 5000, 10000, 50000, float('inf')],
            labels=['Micro', 'Pequeno', 'Médio', 'Grande', 'Mega']
        )

    # Quartis de valor
    if 'Valor' in df.columns:
        df['QuartilValor'] = pd.qcut(df['Valor'], q=4, labels=['Q1', 'Q2', 'Q3', 'Q4'], duplicates='drop')

    # Status formatado (maiúscula)
    if 'StatusWBC' in df.columns:
        df['StatusWBC'] = df['StatusWBC'].str.upper()

    return df


def normalizar_campos_texto(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza campos de texto (UF em maiúsculas, Município em title case, trim).

    Args:
        df: DataFrame de entrada.

    Returns:
        DataFrame com os campos de texto normalizados.
    """
    text_fields = {
        'UF': lambda x: x.upper().strip() if pd.notna(x) else x,
        'Municipio': lambda x: x.title().strip() if pd.notna(x) else x,
        'CodPN': lambda x: x.strip() if pd.notna(x) else x,
    }

    for col, normalizer in text_fields.items():
        if col in df.columns:
            df[col] = df[col].apply(normalizer)

    return df


# ============================================================================
# 4. FILTRAGEM E SELEÇÃO
# ============================================================================

def filtrar_por_data(df: pd.DataFrame, dias_retroativos: int = 30) -> pd.DataFrame:
    """Filtra registros cuja ``DataCotacao`` está dentro dos últimos N dias.

    Args:
        df: DataFrame de entrada.
        dias_retroativos: Janela em dias a contar de hoje.

    Returns:
        DataFrame filtrado (inalterado se não houver coluna ``DataCotacao``).
    """
    if 'DataCotacao' in df.columns:
        data_limite = datetime.now() - timedelta(days=dias_retroativos)
        df = df[df['DataCotacao'] >= data_limite]
    return df


def filtrar_por_status(df: pd.DataFrame, status_validos: List[str]) -> pd.DataFrame:
    """Filtra registros cujo ``StatusWBC`` está na lista informada.

    Args:
        df: DataFrame de entrada.
        status_validos: Status aceitos.

    Returns:
        DataFrame filtrado (inalterado se não houver coluna ``StatusWBC``).
    """
    if 'StatusWBC' in df.columns:
        df = df[df['StatusWBC'].isin(status_validos)]
    return df


def filtrar_por_valor(
    df: pd.DataFrame, valor_min: float = 0, valor_max: Optional[float] = None
) -> pd.DataFrame:
    """Filtra registros por faixa de ``Valor``.

    Args:
        df: DataFrame de entrada.
        valor_min: Valor mínimo (inclusivo).
        valor_max: Valor máximo (inclusivo). Se ``None``, não há limite superior.

    Returns:
        DataFrame filtrado (inalterado se não houver coluna ``Valor``).
    """
    if 'Valor' in df.columns:
        df = df[(df['Valor'] >= valor_min)]
        if valor_max:
            df = df[(df['Valor'] <= valor_max)]
    return df


def selecionar_colunas(df: pd.DataFrame, colunas_importantes: List[str]) -> pd.DataFrame:
    """Seleciona apenas as colunas informadas que existem no DataFrame.

    Args:
        df: DataFrame de entrada.
        colunas_importantes: Colunas desejadas (as inexistentes são ignoradas).

    Returns:
        DataFrame apenas com as colunas existentes da lista.
    """
    colunas_existentes = [col for col in colunas_importantes if col in df.columns]
    return df[colunas_existentes]


# ============================================================================
# 5. AGREGAÇÃO E RESUMO
# ============================================================================

def gerar_relatorio_resumo(df: pd.DataFrame) -> None:
    """Imprime um resumo dos dados (período, estatísticas de valor, contagens).

    Args:
        df: DataFrame de entrada.
    """
    print("\n" + "="*60)
    print("RELATÓRIO RESUMIDO")
    print("="*60)

    print(f"\nTotal de registros: {len(df)}")
    print(f"Período: {df['DataCotacao'].min() if 'DataCotacao' in df.columns else 'N/A'} a {df['DataCotacao'].max() if 'DataCotacao' in df.columns else 'N/A'}")

    if 'Valor' in df.columns:
        print("\nValor:")
        print(f"  Total: R$ {df['Valor'].sum():,.2f}")
        print(f"  Média: R$ {df['Valor'].mean():,.2f}")
        print(f"  Mediana: R$ {df['Valor'].median():,.2f}")
        print(f"  Mínimo: R$ {df['Valor'].min():,.2f}")
        print(f"  Máximo: R$ {df['Valor'].max():,.2f}")

    if 'StatusWBC' in df.columns:
        print("\nPor Status:")
        print(df['StatusWBC'].value_counts())

    if 'UF' in df.columns:
        print("\nPor UF:")
        print(df['UF'].value_counts())


def agrupar_por_periodo(df: pd.DataFrame, periodo: str = 'M') -> Optional[pd.DataFrame]:
    """Agrega ``Valor`` por período temporal a partir de ``DataCotacao``.

    Args:
        df: DataFrame de entrada.
        periodo: Frequência de resample —
            ``'D'`` diário, ``'W'`` semanal, ``'M'`` mensal,
            ``'Q'`` trimestral, ``'Y'`` anual.

    Returns:
        DataFrame com ``TotalValor``, ``QtdRegistros`` e ``ValorMedio`` por período,
        ou ``None`` se faltarem as colunas ``DataCotacao``/``Valor``.
    """
    if 'DataCotacao' not in df.columns or 'Valor' not in df.columns:
        return None

    df_periodo = df.set_index('DataCotacao').resample(periodo)['Valor'].agg(['sum', 'count', 'mean'])
    df_periodo = df_periodo.rename(columns={
        'sum': 'TotalValor',
        'count': 'QtdRegistros',
        'mean': 'ValorMedio'
    })

    return df_periodo


def agrupar_por_campos(df: pd.DataFrame, colunas_agrupamento: List[str]) -> pd.DataFrame:
    """Agrupa por um ou mais campos, agregando ``Valor`` quando disponível.

    Args:
        df: DataFrame de entrada.
        colunas_agrupamento: Colunas usadas no ``groupby``.

    Returns:
        DataFrame agregado (somas/contagens/médias de ``Valor``) ou a contagem por grupo
        quando não houver coluna ``Valor``.
    """
    if 'Valor' in df.columns:
        df_agrupado = df.groupby(colunas_agrupamento).agg({
            'Valor': ['sum', 'count', 'mean'],
            'CodPN': 'count'
        })
    else:
        df_agrupado = df.groupby(colunas_agrupamento).size()

    return df_agrupado


# ============================================================================
# 6. VALIDAÇÃO DE DADOS
# ============================================================================

def validar_dados(df: pd.DataFrame) -> bool:
    """Valida a integridade dos dados e imprime os problemas encontrados.

    Args:
        df: DataFrame de entrada.

    Returns:
        ``True`` se nenhum problema foi encontrado; ``False`` caso contrário.
    """
    print("\n" + "="*60)
    print("VALIDAÇÃO DE DADOS")
    print("="*60)

    erros = []

    # Validar CodPN obrigatório
    if 'CodPN' in df.columns:
        nulos = df['CodPN'].isnull().sum()
        if nulos > 0:
            erros.append(f"⚠ {nulos} registros com CodPN nulo")

    # Validar Valor
    if 'Valor' in df.columns:
        negativos = (df['Valor'] < 0).sum()
        if negativos > 0:
            erros.append(f"⚠ {negativos} registros com Valor negativo")
        nulos = df['Valor'].isnull().sum()
        if nulos > 0:
            erros.append(f"⚠ {nulos} registros com Valor nulo")

    # Validar Datas
    if 'DataCotacao' in df.columns:
        nulos = df['DataCotacao'].isnull().sum()
        if nulos > 0:
            erros.append(f"⚠ {nulos} registros com DataCotacao nula")

    # Validar Duplicatas por chave
    if 'CodPN' in df.columns and 'DataCotacao' in df.columns:
        duplicatas = df.duplicated(subset=['CodPN', 'DataCotacao']).sum()
        if duplicatas > 0:
            erros.append(f"⚠ {duplicatas} duplicatas por (CodPN, DataCotacao)")

    if erros:
        for erro in erros:
            print(erro)
    else:
        print("✓ Dados validados com sucesso!")

    return len(erros) == 0


# ============================================================================
# 7. EXEMPLO DE PIPELINE COMPLETO
# ============================================================================

def pipeline_completo(df: pd.DataFrame) -> pd.DataFrame:
    """Encadeia limpeza, transformação, filtragem, validação e relatório.

    Args:
        df: DataFrame de entrada.

    Returns:
        DataFrame após todas as etapas do pipeline.
    """
    print("\nEXECUTANDO PIPELINE COMPLETO...")
    print("="*60)

    # 1. Limpeza
    print("1. Limpando dados...")
    df = limpar_dados(df)
    df = transformar_tipos(df)
    df = normalizar_campos_texto(df)

    # 2. Transformação
    print("2. Transformando dados...")
    df = adicionar_colunas_calculadas(df)

    # 3. Filtragem
    print("3. Filtrando dados...")
    df = filtrar_por_data(df, dias_retroativos=90)
    df = filtrar_por_status(df, status_validos=['A', 'P', 'C'])

    # 4. Validação
    print("4. Validando dados...")
    validar_dados(df)

    # 5. Relatório
    print("5. Gerando relatório...")
    gerar_relatorio_resumo(df)

    print(f"\nRegistros finais: {len(df)}")
    print("="*60)

    return df


if __name__ == "__main__":
    # DataFrame de exemplo para demonstrar o pipeline
    data = {
        'Cotacao': [1, 2, 3],
        'CodPN': ['PN001', 'PN002', 'PN003'],
        'Valor': [1000.50, 2500.75, 5000.00],
        'DataCotacao': pd.date_range('2024-01-01', periods=3),
        'StatusWBC': ['A', 'P', 'C'],
        'UF': ['SP', 'RJ', 'MG'],
        'PctComissao': [5.0, 7.5, 10.0]
    }

    df = pd.DataFrame(data)

    print("DataFrame Original:")
    inspecionar_dados(df)

    # Executar transformações
    df_transformado = pipeline_completo(df)
