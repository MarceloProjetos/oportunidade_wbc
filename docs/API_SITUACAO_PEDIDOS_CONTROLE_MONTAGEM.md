# Novidade 21/10/2026 — `controle_montagem` na situação do pedido

Este guia é para quem já usa `GET /pedidos/<numero>/situacao` (contrato completo em
[`API_SITUACAO_PEDIDOS.md`](../API_SITUACAO_PEDIDOS.md)). Leia de cima para baixo; cada passo é curto.

## 1. O que mudou

A resposta de **um pedido** (`GET /pedidos/<numero>/situacao`) ganhou um bloco novo no **topo**,
ao lado de `status_pedido`:

```json
"controle_montagem": {
  "inicio_montagem": "2026-11-03",
  "fim_montagem": "2026-11-05",
  "fim_fonte": "orcaview",
  "obs_processo": "Montadora confirmou equipe de 3.",
  "atualizado_em": "2026-10-21T14:45:20.533708+00:00"
}
```

Quem preenche é o **Comercial**, na tela do pedido do OrçaView (bloco "Controle de montagem").
Esta API **só lê**. Não existe rota para gravar estes campos.

A lista (`GET /pedidos/situacao`) **não** traz o bloco. Para ver a montagem, peça o pedido.

## 2. Os campos

| Campo | Tipo | O que é |
| --- | --- | --- |
| `inicio_montagem` | `"AAAA-MM-DD"` ou `null` | Dia em que a montagem começa (ou começou) |
| `fim_montagem` | `"AAAA-MM-DD"` ou `null` | Dia em que a montagem termina (ou terminou) |
| `fim_fonte` | `"orcaview"` ou `"previsao"` | De onde veio o `fim_montagem` (passo 4) |
| `obs_processo` | texto (até 512) ou `null` | Observações do processo de montagem. Pode ter quebra de linha (`\n`) |
| `atualizado_em` | data e hora ISO ou `null` | Quando o Comercial gravou pela última vez |

Datas podem ser passadas ou futuras: começam como previsão e o Comercial corrige para o real.
Qualquer campo pode vir `null` (não preenchido).

## 3. Os três casos do bloco

**Caso A — tem dados.** O objeto vem com os campos. Use normalmente.

**Caso B — nada preenchido ainda.** O objeto vem com todos os campos `null`:

```json
"controle_montagem": {"inicio_montagem": null, "fim_montagem": null, "fim_fonte": "orcaview",
                      "obs_processo": null, "atualizado_em": null}
```

Mostre "sem datas de montagem".

**Caso C — não foi possível ler.** O bloco inteiro vem `null`:

```json
"controle_montagem": null
```

Isso quer dizer **"não sei agora"** (a base de dados ou o SAP não respondeu). **Não** mostre
"sem montagem". Mostre "indisponível, tente de novo". O resto da resposta (etapas, status)
continua válido.

## 4. `fim_fonte = "previsao"`

Nos pedidos de **montagem por conta da Altamira** (tipo 2) que têm **contrato de montagem** na
Previsão Financeira, o Fim da Montagem é a data que o Financeiro informou como "Montagem
concluída em" (ela decide quando a montadora é paga). Nesses pedidos:

- `fim_fonte` vem `"previsao"`;
- `fim_montagem` vem dessa data, ou `null` se o Financeiro ainda não informou.

Nos outros pedidos, `fim_fonte` é `"orcaview"` (o que o Comercial digitou).

## 5. Pedido fechado também traz

A montagem de terceiros termina **depois** da nota fiscal, quando o pedido já está **fechado**
no SAP. Por isso o bloco vem também nas respostas com `"fonte": "ordr"` (pedido fechado,
cancelado ou de entrega futura).

## 6. Exemplo completo (PowerShell)

```powershell
$h = @{ "X-API-Key" = "<sua chave>" }
$r = Invoke-RestMethod -Uri "http://192.168.7.11:8077/pedidos/84507/situacao" -Headers $h
if ($null -eq $r.controle_montagem) {
    "Controle de montagem indisponivel agora - tente de novo"
} else {
    "Inicio: $($r.controle_montagem.inicio_montagem)"
    "Fim:    $($r.controle_montagem.fim_montagem) (fonte: $($r.controle_montagem.fim_fonte))"
    "Obs:    $($r.controle_montagem.obs_processo)"
}
```

## 7. Exemplo completo (Python)

```python
import requests

r = requests.get("http://192.168.7.11:8077/pedidos/84507/situacao",
                 headers={"X-API-Key": "<sua chave>"}, timeout=30)
r.raise_for_status()
cm = r.json().get("controle_montagem")
if cm is None:
    print("Controle de montagem indisponível agora - tente de novo")
else:
    print("Início:", cm["inicio_montagem"] or "—")
    print("Fim:", cm["fim_montagem"] or "—", f"(fonte: {cm['fim_fonte']})")
    print("Obs:", cm["obs_processo"] or "—")
```

## 8. Erros comuns

- **Tratar `null` do bloco como "sem montagem".** Errado: `null` é "não sei agora" (passo 3, caso C).
- **Procurar o bloco dentro de `pedido`.** Ele fica no **topo** da resposta, ao lado de `status_pedido`.
- **Procurar na lista.** Só a rota de um pedido traz o bloco.
- **Tentar gravar por aqui.** Não há rota de escrita: quem grava é o Comercial, no OrçaView.
