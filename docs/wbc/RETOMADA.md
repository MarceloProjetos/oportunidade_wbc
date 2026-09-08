# Retomada — comece por aqui

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

Este arquivo existe para que uma sessão nova (ou outra pessoa) consiga continuar
sem depender do que ficou na cabeça de alguém. Leia este, depois
`APRENDIZADOS.md` e `DECISOES.md`. O `PROGRESS.md` é o diário completo — útil
para entender *como* se chegou aqui, não para começar.

## Onde tudo está

| O quê | Onde |
|---|---|
| Projeto Python | `python/WBCPython` (é a raiz do repositório git) |
| Especificação | `ai_spec/` (um nível acima, na pasta da solução) |
| Legado C# | `WBCServConsole/` — **não mexer**, decisão do usuário |
| Correções C# propostas | `correcoes_propostas_csharp/` (não aplicadas) |
| Credenciais | `.env` na raiz do projeto — **nunca versionado** |

`PAINEL_SENHA` no `.env` libera os comandos de escrita na aba "Executar" do
painel. Vazia, eles ficam desabilitados; em produção ficam desabilitados de
qualquer forma.

## Preparar a máquina

```bash
cd python/WBCPython
uv sync --all-groups --all-extras   # os extras não são opcionais na prática
uv run pytest -q                    # deve dar 857 passed
uv run wbcpython env                # confirma o ambiente-alvo
```

`--all-extras` importa: sem ele falta `pymssql` e nada que toque o WBC funciona.

## As duas regras que nunca podem ser quebradas

1. ~~**Nunca escrever em `SBOALTAMIRAPROD`.**~~ **REVOGADA na virada para
   produção** (ver `DECISOES.md`). A integração agora aponta para produção e
   escreve nela. A trava `safety.py` continua existindo e continua falhando
   fechada, mas está **desligada** por configuração
   (`WBC_BLOCK_PRODUCTION_WRITES=false`), e as duas recusas do `cli.py` foram
   removidas.
2. **Nunca escrever no SQL Server do WBC**, em ambiente nenhum. Só leitura.
   Quem escreve no WBC é o WBC. Toda consulta passa por `assert_read_only_sql`.

Credenciais nunca entram em arquivo, log, docstring ou commit.

## Estado atual

A integração **funciona ponta a ponta em homologação**: lê o orçamento no WBC,
decide pela máquina de estados do SitCode, cria ou atualiza a cotação no SAP,
vincula à oportunidade, espelha o status e grava o snapshot do OrcDetalhe.

Verificado no ambiente real:

| Orçamento | O que aconteceu |
|---|---|
| `00125533` | cotação atualizada + snapshot |
| `00125531` | cotação 101892 criada, vinculada à oportunidade 15145, snapshot |
| `00125530` | cotação 101894 criada, vinculada, **status espelhado para 40** |

857 testes passando, lint limpo, tudo commitado.

## Antes de produção — leia `RISCOS_PRODUCAO.md`

Relatório com o raio de impacto medido contra a produção (leitura apenas). Os
dois achados que mandam: **o legado está criando 26 cotações por dia em produção
neste momento**, e o primeiro ciclo cancelaria 27 cotações e criaria 4 pedidos
(R$ 170.973,41) — ambos irreversíveis. O documento tem o roteiro de virada.

## O que fazer a seguir

1. **Rodar o ciclo completo** sobre a janela inteira em homologação
   (`uv run wbcpython ciclo`) e conferir os documentos gerados. Até aqui só se
   processou um orçamento por vez.
2. **Exercitar o caminho de Pedido.** Tudo que foi validado no ambiente real
   até agora é Cotação; as regras de Pedido (`criar_pedido`,
   `atualizar_pedido`, `cancelar_e_recriar_pedido`) têm teste de unidade mas
   nunca rodaram contra o SAP.
3. **Exercitar troca de parceiro e encerramento** — mesma situação.
4. **Subir o worker contínuo** e o painel (`uv run wbcpython dashboard`, em
   `http://localhost:8501`), e observar por alguns ciclos com a aba "Log"
   aberta — as linhas aparecem sozinhas.
5. **Plano de virada para produção** — depende das decisões em aberto abaixo.

Antes de qualquer execução que escreva, use a prévia:

```bash
uv run wbcpython pendentes            # mostra o que o ciclo faria, sem fazer
uv run wbcpython pendentes --orcamento 00125535
```

O roteiro completo de teste está em `COMO_TESTAR_HOMOLOGACAO.md`.

## Em aberto — precisa de decisão humana

| # | Assunto | Quem decide |
|---|---|---|
| 1 | 🔴 A versão em produção tem o filtro fixo `= '00121819'`? Se tiver, a integração atual processa **um único orçamento**. | TI / usuário |
| 2 | As tautologias `\|\|` do legado deveriam ser `&&`? Ver `DEFEITOS_LEGADO.md` nº 3. | Negócio |
| 3 | O orçamento `00118376` ainda precisa de tratamento especial? | Negócio |
| 4 | Janela de busca: 6 meses, 9 meses, outra? Enquanto não há resposta, é ajustável por `MESES_DE_JANELA` no `.env` — sem mexer em código. | Negócio |
| 5 | Certificado TLS: aceitar autoassinado ou emitir um válido antes de produção? | TI |
| 6 | Nome de negócio de cada SitCode (a mecânica já está correta). | Equipe WBC |
| 6b | 🔴 O WBC marca `U_INO_Update = 'Y'` ao preencher o `U_INO_PN_Correc`? Com a guarda `pedido_corrigido_a_mao`, a troca de PN **só acontece com `Update = 'Y'`**. Se o WBC não marcar, nenhuma troca futura será aplicada. | Equipe WBC |
| 7 | `U_INO_VL_MT` sai de `ORCVALMON` na cotação e de `ORCBAS3` no pedido (`ServiceProcess.cs:281` vs `:1180`). Intencional ou defeito? Os dois comportamentos foram preservados. | Negócio |
| 8 | O pedido não recebe prazo, transporte, embalagem, contato, condição de pagamento nem montagem — só a cotação recebe. É o desejado? | Negócio |
| 9 | 🔴 O `Comments` gravado em produção é o `PGTCOD` cru, mas o C# que temos aplica `Distinct()` nas palavras (`:332-334`). O binário em produção **não é** este fonte. Qual build está rodando? | TI |

## Pendência operacional conhecida

O orçamento `00125535` tem **cotação criada e não vinculada** — a criação
passou e o vínculo falhou (foi assim que se descobriu a exigência do
`MaxLocalTotal`). Reexecutar não corrige: na volta seguinte a cotação já existe
e a regra passa a ser `espelha_status`.

Dados para o acerto manual: oportunidade `15151`, cotação DocEntry `101891` /
DocNum `77821`, total `45.212,81`, e o `U_INO_StatusWBC` da oportunidade
precisa ir de `'0'` para `40`. **O usuário optou por acertar isso na mão.**

Fica registrada a pergunta de projeto: a integração deveria detectar e
completar vínculos pendentes? Hoje não detecta.

## 10. As 22 cotações com `CardCode` diferente do da oportunidade

Em homologação **e** em produção, 22 cotações têm `OQUT.CardCode` diferente do
`OOPR.CardCode` da oportunidade correspondente. O número idêntico nos dois
ambientes sugere que vieram do mesmo histórico, não de testes.

Podem ser troca legítima de PN (aplicada quando `Update='Y'`) ou vítimas do
defeito do `PN_Correc` residual — ver `DECISOES.md`. A correção impede novos
casos, mas não toca nos existentes.

Consulta que as lista:

```sql
SELECT Q."DocEntry", Q."U_INO_COTWBC", Q."CardCode", O."CardCode", O."U_INO_PN_Correc"
FROM "<SCHEMA>"."OQUT" Q JOIN "<SCHEMA>"."OOPR" O ON O."U_ORCNUM_WBC" = Q."U_INO_COTWBC"
WHERE LENGTH(Q."U_INO_COTWBC") > 0 AND Q."CardCode" <> O."CardCode"
```

## 11. `U_INO_Update` — respondido

Quem grava o `'Y'` é o **operador, à mão**, na oportunidade do SAP, quando
precisa trocar o PN de um pedido. A integração baixa para `'N'` ao vincular o
pedido novo. Não é campo do WBC e não sinaliza mudança de valores.

Ver `DECISOES.md`, seção "`U_INO_Update` é um desvio, não uma autorização" —
inclui a árvore do legado e o congelamento que foi aplicado e revertido.

