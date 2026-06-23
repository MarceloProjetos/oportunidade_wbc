FROM python:3.12-slim

WORKDIR /app

# ODBC Driver 18 para SQL Server (enriquecimento SITCOD/ORCALTDTH)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg apt-transport-https unixodbc unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
        https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py sap_connection.py .
COPY extract_sap_to_supabase.py scheduled_execution.py test_connections.py .

RUN mkdir -p logs

ENV PYTHONUNBUFFERED=1 \
    SAP_HOST="" \
    SAP_PORT="30015" \
    SAP_USER="" \
    SAP_PASSWORD="" \
    SAP_DATABASE="" \
    SAP_SCHEMA="" \
    SAP_VIEW_NAME="" \
    SUPABASE_URL="" \
    SUPABASE_KEY="" \
    SUPABASE_SERVICE_ROLE_KEY="" \
    TABLE_NAME="oportunidades" \
    SQL_HOST="" \
    SQL_PORT="1433" \
    SQL_USER="" \
    SQL_PASSWORD="" \
    SQL_DATABASE="WBCCAD"

CMD ["python", "extract_sap_to_supabase.py"]
