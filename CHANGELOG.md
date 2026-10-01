# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

## [2026-10-01] — Central Integração SAP, F1: a casca comum estreia no Controle de Produção

Entra pelo `deploy_update.bat` (`OrcaView-ControleProducao`); só aparência — nenhuma rota,
regra ou gravação mudou. Plano: `docs/PLANO_CASA_COMUM_11.md`.

- **Pasta nova `casa/`**, a casca que as três telas da .11 vão dividir: barra do topo, paleta,
  título de página e botão de tema. O Controle de Produção é o primeiro a usar; o painel WBC
  (F2) e a Sincronização (F3) vêm depois.
- **Barra:** marca "Central Integração SAP" e as telas na ordem do pedido — ← OrçaView ·
  Integração WBC · Pedidos WBC · Manutenção de OP · Sincronização · Execuções. A tela aberta
  fica destacada. Linha coral fina no topo, igual em toda tela.
- **Ambiente:** pílula com a company DB na barra, vermelha em produção. Fora de produção a faixa
  cinza continua.
- **Tema:** escuro por padrão, o botão troca para claro. A escolha agora fica num cookie, que vale
  nas três portas; quem tinha escolhido claro antes é migrado na primeira visita.
- **Título de página:** o ícone ganhou um ladrilho coral (vermelho na confirmação e no erro), o
  mesmo desenho da marca.
- Notebook de 1280 px: o menu esconde os ícones e mantém os nomes. Celular: as telas vão para uma
  segunda linha que rola de lado. `style.css?v=12`.

## [2026-10-01] — Controle de Produção: "Cancelar as OPs" na Manutenção de OP; menu junto

Entra pelo `deploy_update.bat` (`OrcaView-ControleProducao`); só a tela.

- **"Cancelar as OPs de um pedido" mudou de página** (pedido do Marcelo): saiu de Pedidos WBC e
  fica na Manutenção de OP, abaixo de "Encerrar todas as OPs de um pedido". A rota continua
  `/pedidos-wbc/cancelar-ops/*` (a operação, a trava de execução e o comando da CLI são do módulo
  Pedidos WBC); só o "Voltar" da conferência e dos erros agora leva à Manutenção de OP.
- **Menu do topo**: "Painel WBC" e "Execuções" vieram para junto de "Manutenção de OP"; só "Sair"
  e o botão de tema ficam à direita.
- **"Sincronização SAP → Supabase" no menu**, entre "Manutenção de OP" e "Painel WBC" — o mesmo
  botão do painel WBC. Passa por `/sincronizacao` (aberta, só redireciona): `SIS_PAINEL_URL` se
  houver, senão o mesmo host na `OS_API_PORT` (8077), em `/sincronizar`. Em tela de até ~1300 px
  o "Sair" e o tema descem para uma segunda linha.

