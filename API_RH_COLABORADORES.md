# API — Colaboradores (Kairos)

Documento para **quem vai consumir** os dados. Escrito para ser lido sem nos perguntar
nada: tudo que você precisa saber está aqui, inclusive o que costuma dar errado.

Ele descreve como ler o **quadro de colaboradores das 3 empresas** — quem trabalha em
qual empresa e em qual setor, com cargo, matrícula e situação (ativo, desligado, sem
expediente):

- **API REST** (`192.168.7.11:8077`) — `GET /rh/colaboradores`, para código.
- **MCP** (`192.168.7.11:8078`) — para assistente de IA (Claude Desktop, Claude Code)
  responder em linguagem natural (§10).

As duas leem exatamente a mesma coisa; a MCP é uma camada fina sobre a REST.

> Os endpoints de **Ordens de Serviço**, **Ordens de Produção** e **Situação dos
> Pedidos** são outra coisa e estão em `API_OS_INTEGRACAO.md`,
> `API_ORDENS_PRODUCAO.md` e `API_SITUACAO_PEDIDOS.md`.

---

## 1. O que é, e o que **não** é

**É** um **espelho** do cadastro do Kairos (o sistema de ponto), gravado no Supabase
**uma vez por dia útil, às 12:40**, pelo OrçaView web (servidor `.90`). Esta API só lê
esse espelho — ela **não** fala com o Kairos.

**Não é:**

- **Não escreve nada.** O endpoint é somente leitura.
- **Não é tempo real.** Uma admissão de hoje de manhã aparece depois da carga das 12:40.
  O campo `atualizado_em` diz de quando é o dado, e `desatualizado` avisa quando a carga
  do dia não chegou (§5).
- **Não é a folha de pagamento.** Não há salário, CPF, endereço ou qualquer dado além do
  que a lista abaixo mostra.

## 2. Chamada

```bash
curl "http://192.168.7.11:8077/rh/colaboradores" -H "X-API-Key: SUA_CHAVE"
```

No navegador (que não envia header), use `?key=SUA_CHAVE`.

| Parâmetro | Valores | O que faz |
| --- | --- | --- |
| `empresa` | `altamira`, `tecnequip`, `proalta` | Restringe a uma empresa. Ausente = as três. |
| `somente_ativos` | `1` | Só quem está na ativa. Ausente = **todos**, com `status`. |

**Empresa desconhecida é recusada com 400** — de propósito. Um erro de digitação
devolveria o quadro de outra empresa com HTTP 200, e ninguém perceberia.

## 3. Resposta

```json
{
  "ok": true,
  "atualizado_em": "2026-08-31T15:40:12.254268+00:00",
  "atualizado_em_br": "2026-08-31T12:40:12-03:00",
  "desatualizado": false,
  "carga_esperada_em": "2026-08-31T12:40-03:00",
  "total": 56,
  "somente_ativos": true,
  "empresas": [
    {
      "empresa": "tecnequip",
      "total": 56,
      "setores": [
        {
          "setor": "PRODUÇÃO",
          "total": 32,
          "colaboradores": [
            {
              "person_id": 123,
              "nome": "Fulano da Silva",
              "matricula": "100204",
              "cargo": "Operador de Máquina",
              "status": "ativo",
              "em_ferias_ou_afastado": false,
              "sem_expediente_desde": null,
              "data_admissao": "2021-12-01",
              "data_desligamento": null
            }
          ]
        }
      ]
    }
  ]
}
```

O aninhamento é **empresa → setor → colaboradores**. **Cargo é campo da pessoa**, não um
nível: aninhar por cargo criaria grupos de uma pessoa só na maioria dos casos.

Quem está sem setor no Kairos entra no grupo `"SEM SETOR"` — a pessoa nunca é escondida
por causa de um campo em branco.

## 4. `status`: a linha do desligado **não some**

Esta é a regra mais importante do contrato. Quando alguém sai da empresa, a linha
**permanece** e muda de `status`:

| `status` | Significa |
| --- | --- |
| `ativo` | No quadro. |
| `desligado` | Desligamento registrado no Kairos (veja `data_desligamento`). |
| `ausente` | Sumiu do cadastro do Kairos sem registro de desligamento. |

Por que isso importa: se as linhas sumissem, um programa que guarda `person_id` quebraria
(ou perderia o histórico) no dia em que alguém fosse desligado. Filtre por
`status == "ativo"` — ou peça `?somente_ativos=1` — quando quiser só o quadro atual.

## 5. `em_ferias_ou_afastado` — leia esta seção antes de usar

O Kairos **não tem** evento de "Férias" nem de "Afastamento" na API dele. O sinal é
indireto: a **ausência** do evento "Horas a Trabalhar" em dias que a empresa já fechou.

- `true` = a pessoa está **sem expediente** há pelo menos 3 dias úteis processados. Pode
  ser férias, atestado, licença ou afastamento — **o dado não distingue**.
- `sem_expediente_desde` = o primeiro dia da ausência. **`null` com a flag ligada** quer
  dizer que a ausência começou **antes** da janela de 30 dias e não se sabe desde quando.
- Quem **nunca** bate ponto (isento de relógio) **não** é marcado.

Se o seu uso exige separar férias de afastamento, este campo não serve — a informação não
existe na API do Kairos.

## 6. `desatualizado`

`true` quer dizer que a carga do último **12:40 de dia útil** não chegou. O dado continua
sendo servido (é o último bom conhecido), mas você fica sabendo que ele envelheceu.

Não é "mais velho que N horas": na segunda de manhã o dado **é** de sexta e isso está
certo — a carga só roda em dia útil. A conta considera feriados nacionais.

**`carga_esperada_em`** mostra contra qual slot a comparação foi feita, para você não ter
de adivinhar o critério: se `atualizado_em` for anterior a ele, `desatualizado` é `true`.

**`atualizado_em_br`** é o mesmo instante de `atualizado_em`, em horário de Brasília. Os
dois estão sempre lá: o cru é UTC (`+00:00`) e quem bate o olho no JSON lê "15:40" como se
fosse hora local — foi para evitar exatamente esse erro que o campo em BRT existe.

## 7. Códigos de resposta

| Código | Quando |
| --- | --- |
| 200 | Sucesso (inclusive lista vazia). |
| 400 | `empresa` desconhecida. A resposta traz `empresas_validas`. |
| 401 | Sem `X-API-Key` (quando a chave está configurada no servidor). |
| 502 | Falha ao ler o espelho no Supabase. |

## 8. Armadilhas

1. **`matricula` não é `person_id`.** O `person_id` é o Id do cadastro no Kairos; a
   matrícula é o número do crachá (e é ela que casa com os eventos de ponto). Use
   `person_id` como chave estável.
2. **Nome não é chave.** Há homônimos e o Kairos aceita grafias diferentes ao longo do
   tempo.
3. **Setor vem do Kairos.** Se a pessoa mudou de setor lá, aqui muda na carga seguinte —
   não há como forçar antes das 12:40.
4. **`total` conta linhas, não pessoas ativas.** Sem `?somente_ativos=1` ele inclui
   desligados e ausentes.

## 9. MCP — para o assistente responder em linguagem natural

Duas *tools*, ambas somente leitura, sobre o mesmo endpoint:

| Tool | Para quê |
| --- | --- |
| `listar_colaboradores(empresa, setor, somente_ativos, limite)` | Os nomes. "Quem está na produção da Tecnequip?", "fulano ainda trabalha aqui?" |
| `resumo_colaboradores(empresa, somente_ativos)` | Só as contagens por setor. "Quantos na expedição?" — cabe na conversa mesmo com o quadro inteiro. |

Detalhes que valem saber:

- **O default das tools é `somente_ativos=True`** (diferente da REST, que traz todos):
  a pergunta comum é sobre quem está na ativa. Passe `False` para ver os desligados.
- **`setor` casa sem acento e por pedaço** — `"producao"` acha `"PRODUÇÃO"`. Errar o
  nome não devolve lista vazia calada: a resposta traz `setores_disponiveis`.
- **`limite` (default 200)** corta a lista de nomes, nunca as contagens: a resposta vem
  com `truncado`, `mostrando` e `omitidos` por setor.
- Recurso anexável `sap-integracao://colaboradores` — o quadro ativo em contagens
  (de propósito sem nomes: como contexto fixo, 251 pessoas custariam caro).

## 10. Por dentro (para quem mantém)

- Tabela: `kairos_colaboradores` no Supabase (chave `empresa` + `person_id`), escrita
  **só** pelo `web_orcaview_V117` com a service key; esta API lê com a mesma chave.
- Este repositório **não tem credencial do Kairos** e não deve ganhar uma: quem fala com
  o Kairos é o `.90`, onde já vive o cliente com todas as armadilhas de tenant tratadas.
- Leitura paginada de 1000 em 1000 — o PostgREST corta em 1000 linhas com HTTP 200, e a
  tabela só cresce (linha nunca é deletada).
- Plano e decisões: `web_orcaview_V117/docs/PLANO_ESPELHO_COLABORADORES_KAIROS.md`.
