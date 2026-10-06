# Controle de Produção — guia do operador (1 página)

> Rascunho de 29/09/2026 (F7 do `PLANO_CONTROLE_PRODUCAO_11.md (removido em 2026-10-06; historico no git)`) — **o Anderson valida**.
> Grava em **PRODUÇÃO** (`SBOALTAMIRAPROD`). Não há desfazer automático.

## Entrar

1. `http://192.168.7.11:8079/` (painel WBC) → entre com a chave (a mesma da API).
2. Na barra do topo — a mesma nas três telas da **Central Integração SAP** — clique em
   **Pedidos WBC** ou **Manutenção de OP**. Não pede a chave de novo.
3. "Integração WBC" e "Sincronização" levam às outras telas; "Sair" desloga das três. O botão
   redondo troca o tema (escuro/claro) e a escolha vale nas três.
4. **Cancelar as OPs de um pedido** fica na **Manutenção de OP**, abaixo de "Encerrar todas as OPs".
   Desde 01/10/2026 ele faz o mesmo que a CLI: cancela as OPs planejadas e, se todas ficarem
   canceladas, **devolve o pedido a "Pedidos novos"** (pronto para processar de novo, sem
   "forçar"). Na hora de executar ele relê as OPs: se alguma tiver sido liberada depois da
   conferência, nada é cancelado; se alguma já tiver sido cancelada por outro caminho, ela é
   deixada como está.

## Pedidos WBC → OPs (criar as OPs de um pedido)

1. **Buscar** → lista os pedidos com `Integrar='Y'` ainda sem OP. Só leitura.
2. Antes de processar, confira:
   - o **PCP foi avisado** para não processar o mesmo pedido pelo addon do SAP — addon e tela
     no mesmo pedido = **OP em dobro** (aconteceu com o 84426 em 23/09);
   - no painel WBC, o orçamento **não** tem ação pendente (atualizar/cancelar pedido);
   - dentro do expediente, **nunca depois das 17:30**.
3. Marque o pedido → **Processar selecionados (N)…** (só habilita com pedido marcado) → a tela
   mostra o que vai gravar → confirme em até 10 min.
4. **Não marque "forçar"** (duplica OP).
5. Depois: confira as OPs no SAP; em **Execuções**, o resultado sem "SEM OP" nem "rateio".

**Reprocessar** (em "Pedidos integrados"): marque o pedido → **Reprocessar selecionados (N)…** →
confira → confirme. Ele **cancela todas as OPs planejadas** do pedido — de qualquer origem,
inclusive as do addon — e **não recria**: o pedido volta para "Pedidos novos" e precisa ser
processado de novo. OP liberada ou encerrada não é tocada. Use quando o orçamento mudou depois
do processamento e as OPs planejadas precisam sair.

**Interromper** um Processar/Reprocessar para **depois do pedido em curso**: ele termina
inteiro, e os próximos aparecem no log como "NÃO foram iniciados". No Cancelar OPs, para depois
da OP em curso. (Até 01/10/2026 o botão cortava a gravação no meio.)

**Caiu no meio?** Não processe de novo. Chame o Anderson: a retomada é `manutencao-op buscar` →
`cancelar-ops` → `processar-novos` **sem** `--force` — e o Cancelar OPs da tela já serve para
isso.

## Manutenção de OP

- **Buscar** pelo nº do pedido → mostra as OPs e o status.
- **Liberar selecionadas** — Planejada → Liberada. Grava no 1º clique.
- **Encerrar selecionadas…** — lança **saída dos insumos + entrada do produto** e encerra.
  **Irreversível** (estorno = cancelar a entrada e a saída no SAP, à mão). A tela mostra a ordem
  calculada (filha antes da mãe) e pede confirmação.
- **Replanejar selecionadas** (voltar para Planejada), desde 29/09: marque as OPs Liberadas e
  clique — grava no primeiro clique, como o Liberar, e roda em segundo plano. Só volta a OP
  **sem insumo baixado e sem produto apontado**: a tabela mostra o motivo em vermelho ao lado
  do status ("insumo baixado", "produto apontado"), e o botão fica desabilitado enquanto uma
  dessas estiver marcada, dizendo qual desmarcar. O lançamento se cancela no SAP antes. OP
  Planejada marcada junto não atrapalha (já está lá). Também pela CLI e pela API, com a mesma
  regra.
- **Interromper um Encerrar** para **depois da OP em curso**: ela termina saída, entrada e
  encerramento, e as próximas não começam (aparecem como "NÃO INICIADA" no log). Nunca sobra uma
  OP com o insumo baixado e o produto sem entrada por causa do botão.
- A API 8077 não encerra mais OP: encerrar é **só aqui** — ou pela API JSON desta tela
  (`/api/manutencao-op`), que outro sistema pode chamar. A regra é a mesma, e ela entra na
  mesma fila: se a tela disser "Já existe execução em andamento", pode ser uma execução da API.

## Execuções

- Lista as **30 últimas** execuções da tela (mais as que estão rodando) com o log e o resultado
  de cada uma. Ficam guardadas no Supabase: reiniciar o serviço **não** apaga a lista.
- Execução pedida por outro sistema (a API) aparece com **"por *fulano* · API"** embaixo da
  descrição — *fulano* é quem pediu lá. Sem essa linha, foi pela tela.
- A 31ª apaga a mais antiga. O que roda pela CLI (Replanejar, ou qualquer comando digitado) **não** entra aqui —
  fica em `C:\Python\ServidorIntegracaoSAP\logs\controleproducao_cli.log`, com o comando
  digitado.
- Aviso amarelo "Não foi possível ler o histórico guardado" = o Supabase não respondeu; a lista
  mostra só o que rodou desde o último reinício. As execuções continuam gravando no SAP normalmente.
- Os botões que agem sobre uma seleção (Processar, Liberar, Encerrar) e os "Conferir…" só
  habilitam quando há o que conferir; um clique duplo não reenvia. "Já existe execução em
  andamento" traz o link para acompanhar a que está rodando.

## Quando algo não abre

| Sintoma | O que é | O que fazer |
|---|---|---|
| `ERR_CONNECTION_REFUSED` na 8080 | serviço parado | Marcelo: `nssm restart OrcaView-ControleProducao` (espere até ~90 s) |
| Pede a chave de novo | cookie de outro host | abra o painel pelo mesmo endereço (sempre `192.168.7.11`) |
| "Escrita desabilitada" (503) | sem `OS_API_KEY` ou fora da .11 | Marcelo |
| Lista vazia | nenhum pedido pendente agora | normal; a lista muda ao longo do dia |

Log: `C:\Python\ServidorIntegracaoSAP\logs\controleproducao.log`. Nunca pare o serviço com
execução em andamento (`/health/ocupado` = 1): deixa OP pela metade.
