#!/bin/bash

# Script de instalação e teste

echo "=================================================="
echo "Setup - SAP B1 to Supabase Extractor"
echo "=================================================="

# 1. Criar estrutura
echo "1. Criando estrutura de diretórios..."
mkdir -p logs
echo "   ✓ Diretório logs criado"

# 2. Instalar dependências
echo ""
echo "2. Instalando dependências Python..."
pip install -r requirements.txt
if [ $? -eq 0 ]; then
    echo "   ✓ Dependências instaladas"
else
    echo "   ✗ Erro ao instalar dependências"
    exit 1
fi

# 3. Criar .env se não existir
if [ ! -f .env ]; then
    echo ""
    echo "3. Copiando template de configuração..."
    cp .env.example .env
    echo "   ⚠ Arquivo .env criado. Edite com suas credenciais:"
    echo "   nano .env"
    chmod 600 .env
fi

# 4. Testar conexões
echo ""
echo "4. Testando conexões..."
python scripts/test_connections.py

echo ""
echo "=================================================="
echo "Setup concluído!"
echo "=================================================="
echo ""
echo "Próximos passos:"
echo "1. Edite o arquivo .env com suas credenciais"
echo "2. Execute: python extract_sap_to_supabase.py"
echo ""
