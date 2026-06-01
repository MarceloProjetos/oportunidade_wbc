# Guia de Uso - Script SAP B1 to Supabase

## 📋 Sumário Executivo

Este projeto fornece uma solução completa para:
- ✓ Extrair dados de uma view no SAP B1 (HANA)
- ✓ Transformar e validar dados com Pandas
- ✓ Popular uma tabela no Supabase
- ✓ Rastrear execuções com ID único e timestamp

## 🚀 Quick Start

### 1. Setup Inicial

```bash
# Clonar ou acessar o diretório
cd /home/amarques/python/oportunidade_wbc

# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com seus dados
nano .env

# Testar conexões
python test_connections.py
```

### 2. Primeira Execução

```python
from extract_sap_to_supabase import main

# Executa uma extração simples
main(view_name='SUA_VIEW_SAP', execution_mode='insert')
```

## 🔧 Configuração Detalhada

### Variáveis de Ambiente (.env)

```env
# SAP HANA
SAP_HOST=sap-server.example.com
SAP_PORT=30013
SAP_USER=seu_usuario
SAP_PASSWORD=sua_senha
SAP_DATABASE=SBODemoBR

# Supabase
SUPABASE_URL=https://seu_project.supabase.co
SUPABASE_KEY=sua_chave_anon

# Opcional
TABLE_NAME=oportunidades
```

### Encontrando suas Credenciais

**SAP HANA:**
- Host: IP ou hostname do servidor HANA
- Port: Geralmente 30013 (pode variar)
- User/Password: Suas credenciais do SAP
- Database: Nome da database (ex: "SBODemoBR")

**Supabase:**
- URL: Em Project Settings → API
- Key: Usar a chave "anon public"

## 📊 Exemplos de Uso

### Exemplo 1: Extração Simples

```python
from extract_sap_to_supabase import main

# Extrair tudo da view
main(view_name='V_OPORTUNIDADES', execution_mode='insert')
```

### Exemplo 2: Com Validações e Transformações

```python
import pandas as pd
from extract_sap_to_supabase import SAPExtractor, SupabaseLoader, prepare_data

# Conectar ao SAP
sap = SAPExtractor(
    host='seu_host',
    port=30013,
    user='seu_usuario',
    password='sua_senha',
    database='SUA_DATABASE'
)
sap.connect()

# Extrair com filtro
query = """
SELECT * FROM V_OPORTUNIDADES 
WHERE DataCotacao >= '2024-01-01'
  AND Valor > 1000
ORDER BY DataCotacao DESC
"""
df = sap.execute_query(query)
sap.close()

# Validar e limpar
df = df.drop_duplicates(subset=['CodPN', 'DataCotacao'], keep='last')
df = df[df['Valor'].notna()]
df = df[df['CodPN'].notna()]

# Inserir no Supabase
loader = SupabaseLoader(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
data, exec_id = prepare_data(df)
loader.insert_data('oportunidades', data)

print(f"✓ {len(data)} registros inseridos com ID: {exec_id}")
```

### Exemplo 3: Agrupamento e Análise

```python
import pandas as pd
from extract_sap_to_supabase import SAPExtractor

sap = SAPExtractor(...)
sap.connect()

df = sap.execute_query("SELECT * FROM V_OPORTUNIDADES")
sap.close()

# Análise por status
analise = df.groupby('StatusWBC').agg({
    'Valor': ['sum', 'count', 'mean'],
    'CodPN': 'nunique'
}).round(2)
print(analise)

# Análise por UF
top_ufs = df.groupby('UF')['Valor'].sum().sort_values(ascending=False).head(10)
print(top_ufs)
```

### Exemplo 4: Filtro por Período

```python
from datetime import datetime, timedelta
import pandas as pd
from extract_sap_to_supabase import SAPExtractor, SupabaseLoader, prepare_data

sap = SAPExtractor(...)
sap.connect()

# Últimos 30 dias
data_inicio = (datetime.now() - timedelta(days=30)).date()
query = f"""
SELECT * FROM V_OPORTUNIDADES 
WHERE DataCotacao >= '{data_inicio}'
"""

df = sap.execute_query(query)
sap.close()

# Processar e inserir
loader = SupabaseLoader(...)
data, exec_id = prepare_data(df, execution_id='30DIAS_2024')
loader.insert_data('oportunidades', data)
```

### Exemplo 5: Upsert (Atualização)

```python
from extract_sap_to_supabase import main

# Atualiza registros existentes com base na chave primária
main(view_name='V_OPORTUNIDADES', execution_mode='upsert')
```

## 🔄 Agendamento Automático

### Linux/Mac - Cron

```bash
# Editar crontab
crontab -e

# Executar diariamente às 8:00 AM
0 8 * * * cd /home/amarques/python/oportunidade_wbc && python extract_sap_to_supabase.py >> logs/execution.log 2>&1

# Executar a cada 6 horas
0 */6 * * * cd /home/amarques/python/oportunidade_wbc && python extract_sap_to_supabase.py >> logs/execution.log 2>&1

# Executar segundas a sextas às 9:00 AM
0 9 * * 1-5 cd /home/amarques/python/oportunidade_wbc && python extract_sap_to_supabase.py >> logs/execution.log 2>&1
```

### Python - APScheduler

```bash
pip install apscheduler

python scheduled_execution.py
```

### Windows - Task Scheduler

1. Abra Task Scheduler
2. Crie tarefa básica
3. Trigger: Diário
4. Action: `C:\Python\python.exe C:\path\to\extract_sap_to_supabase.py`

## 📈 Monitores e Logs

### Visualizar Logs

```bash
# Logs em tempo real
tail -f logs/execution.log

# Filtrar por erro
grep ERROR logs/execution.log

# Últimas 50 linhas
tail -50 logs/execution.log
```

### Recuperar ID de Execução

```sql
-- No Supabase
SELECT DISTINCT id_execucao, data_hora_extracao, COUNT(*) as registros
FROM oportunidades
GROUP BY id_execucao, data_hora_extracao
ORDER BY data_hora_extracao DESC
LIMIT 10;
```

## 🐛 Troubleshooting

### Erro: "Não conectado ao SAP HANA"
```
✓ Verifique: Host, Port, User, Password, Database
✓ Teste com: python test_connections.py
✓ Ping para o host: ping seu_host_sap
```

### Erro: "Table does not exist"
```
✓ Verifique: Nome da tabela no .env está correto?
✓ Verifique: Tabela existe no Supabase?
✓ Verifique: Você tem permissão de escrita?
```

### Erro: "Query retornou 0 linhas"
```
✓ Verifique: A view existe no SAP?
✓ Verifique: A view tem dados?
✓ Teste manualmente a query no SAP HANA Studio
```

### Performance Lenta
```python
# Se extraindo muitos dados, use limite
query = "SELECT * FROM V_OPORTUNIDADES LIMIT 10000"

# Ou por período
query = """
SELECT * FROM V_OPORTUNIDADES 
WHERE DataCotacao >= CURRENT_DATE - 30
"""
```

## 📝 Estrutura de Arquivos

```
oportunidade_wbc/
├── extract_sap_to_supabase.py  # Script principal
├── test_connections.py          # Teste de conectividade
├── exemplo_avancado.py          # Exemplos avançados
├── pandas_guide.py              # Guia de manipulação de dados
├── scheduled_execution.py       # Automação com scheduler
├── requirements.txt             # Dependências Python
├── .env.example                 # Template de configuração
├── config.json                  # Configurações (JSON)
├── README.md                    # Este arquivo
└── logs/                        # Diretório de logs
    └── execution.log
```

## 🔐 Segurança

### Proteção de Credenciais

```bash
# Nunca commitar .env
echo ".env" >> .gitignore

# Usar variáveis de ambiente em produção
export SAP_HOST="seu_host"
export SAP_USER="seu_usuario"
# etc...

# Ou em Docker/Kubernetes usar secrets
```

### Permissões

```bash
# Restringir permissões de arquivo .env
chmod 600 .env

# Executável apenas pelo proprietário
chmod 500 extract_sap_to_supabase.py
```

## 📞 Suporte

### Verificar versões

```bash
pip show hdbcli supabase pandas
python --version
```

### Modo debug

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Seu código aqui
```

## 📚 Referências

- [hdbcli Documentation](https://help.sap.com/viewer/0eec0714519e4652a5e0bacb77aeadb4/2.0/en-US/ce0a8b1f7a7a36f90e7cf48ac96f9d23.html)
- [Supabase Python SDK](https://github.com/supabase-community/supabase-py)
- [Pandas Documentation](https://pandas.pydata.org/docs/)
- [APScheduler](https://apscheduler.readthedocs.io/)

## 📋 Checklist de Implementação

- [ ] Instalar dependências (`pip install -r requirements.txt`)
- [ ] Configurar `.env` com credenciais SAP
- [ ] Configurar `.env` com credenciais Supabase
- [ ] Criar tabela no Supabase
- [ ] Testar conexões (`python test_connections.py`)
- [ ] Testar extração (`python extract_sap_to_supabase.py`)
- [ ] Validar dados no Supabase
- [ ] Configurar agendamento (cron/scheduler)
- [ ] Configurar monitoramento de logs
- [ ] Documentar view SAP utilizada

## 📄 Licença

Use livremente conforme necessário.
