# Controle de Produção — guia do operador (1 página)

> Rascunho de 29/09/2026 (F7 do `docs/PLANO_CONTROLE_PRODUCAO_11.md`) — **o Anderson valida**.
> Grava em **PRODUÇÃO** (`SBOALTAMIRAPROD`). Não há desfazer automático.

## Entrar

1. `http://192.168.7.11:8079/` (painel WBC) → entre com a chave (a mesma da API).
2. Botão **Pedidos WBC → OPs** ou **Manutenção de OP** no topo. Não pede a chave de novo.
3. "⇄ Painel WBC" volta. "Sair" desloga das duas telas.

## Pedidos WBC → OPs (criar as OPs de um pedido)

1. **Buscar** → lista os pedidos com `Integrar='Y'` ainda sem OP. Só leitura.
2. Antes de processar, confira:
   - o **PCP foi avisado** para não processar o mesmo pedido pelo addon do SAP — addon e tela
     no mesmo pedido = **OP em dobro** (aconteceu com o 84426 em 23/09);
   - no painel WBC, o orçamento **não** tem ação pendente (atualizar/cancelar pedido);
   - dentro do expediente, **nunca depois das 17:30**.
3. Marque o pedido → **Processar selecionados…** → a tela mostra o que vai gravar → confirme
   em até 10 min.
4. **Não marque "forçar"** (duplica OP). Não existe Reprocessar na tela (só CLI, com o Anderson).
5. Depois: confira as OPs no SAP; em **Execuções**, o resultado sem "SEM OP" nem "rateio".

**Caiu no meio?** Não processe de novo. Chame o Anderson: a retomada é `manutencao-op buscar` →
`cancelar-ops` → `processar-novos` **sem** `--force`.

## Manutenção de OP

- **Buscar** pelo nº do pedido → mostra as OPs e o status.
- **Liberar selecionadas** — Planejada → Liberada. Grava no 1º clique.
- **Encerrar selecionadas…** — lança **saída dos insumos + entrada do produto** e encerra.
  **Irreversível** (estorno = cancelar a entrada e a saída no SAP, à mão). A tela mostra a ordem
  calculada (filha antes da mãe) e pede confirmação.
- **Replanejar** (voltar para Planejada) **não existe na tela** — só pela CLI, com o Anderson.
- A API 8077 não encerra mais OP: encerrar é **só aqui**.

## Quando algo não abre

| Sintoma | O que é | O que fazer |
|---|---|---|
| `ERR_CONNECTION_REFUSED` na 8080 | serviço parado | Marcelo: `nssm restart OrcaView-ControleProducao` (espere até ~90 s) |
| Pede a chave de novo | cookie de outro host | abra o painel pelo mesmo endereço (sempre `192.168.7.11`) |
| "Escrita desabilitada" (503) | sem `OS_API_KEY` ou fora da .11 | Marcelo |
| Lista vazia | nenhum pedido pendente agora | normal; a lista muda ao longo do dia |

Log: `C:\Python\ServidorIntegracaoSAP\logs\controleproducao.log`. Nunca pare o serviço com
execução em andamento (`/health/ocupado` = 1): deixa OP pela metade.
