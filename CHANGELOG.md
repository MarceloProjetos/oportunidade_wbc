# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

## [2026-10-01] — Controle de Produção: "Cancelar as OPs" na Manutenção de OP; menu junto

Entra pelo `deploy_update.bat` (`OrcaView-ControleProducao`); só a tela.

- **"Cancelar as OPs de um pedido" mudou de página** (pedido do Marcelo): saiu de Pedidos WBC e
  fica na Manutenção de OP, abaixo de "Encerrar todas as OPs de um pedido". A rota continua
  `/pedidos-wbc/cancelar-ops/*` (a operação, a trava de execução e o comando da CLI são do módulo
  Pedidos WBC); só o "Voltar" da conferência e dos erros agora leva à Manutenção de OP.
- **Menu do topo**: "Painel WBC" e "Execuções" vieram para junto de "Manutenção de OP"; só "Sair"
  e o botão de tema ficam à direita.

