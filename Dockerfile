FROM python:3.11-slim

WORKDIR /app

# Copiar requirements
COPY requirements.txt .

# Instalar dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY config.py .
COPY sap_connection.py .
COPY extract_sap_to_supabase.py .
COPY test_connections.py .

# Criar diretório de logs
RUN mkdir -p logs

# Variáveis de ambiente (podem ser sobrescrita em tempo de execução)
ENV SAP_HOST=""
ENV SAP_PORT="30015"
ENV SAP_USER=""
ENV SAP_PASSWORD=""
ENV SAP_DATABASE=""
ENV SUPABASE_URL=""
ENV SUPABASE_KEY=""
ENV TABLE_NAME="oportunidades"

# Comando padrão
CMD ["python", "extract_sap_to_supabase.py"]
