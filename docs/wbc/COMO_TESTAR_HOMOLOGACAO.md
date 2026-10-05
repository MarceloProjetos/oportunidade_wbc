# Como testar em homologação

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

Roteiro em ordem de risco: cada passo só acessa o que o anterior já provou que
funciona, e **nada escreve** até o passo 6. Rode tudo de dentro da pasta
`python/WBCPython`.

Antes de começar, garanta as dependências nativas (drivers e dashboard):

```bash
uv sync --all-groups --all-extras
```

## As duas regras que valem em qualquer passo

1. **Nunca escrever em `SBOALTAMIRAPROD`.** A trava está no código
   (`safety.assert_write_allowed`) e falha fechada — se a configuração estiver
   incompleta, ela bloqueia em vez de liberar.
2. **Nunca escrever no SQL Server do WBC**, em ambiente nenhum. Toda consulta
   passa por `assert_read_only_sql`, e o repositório não expõe método de
   escrita. Quem escreve no WBC é o WBC.

---

## 1. `wbcpython env` — para onde a aplicação está apontada

Não acessa rede. Confirme na saída:

```
company_db=SBOALTAMIRAHOMOLOG (homologação)   trava_de_escrita_em_producao=ATIVA
```

Se aparecer `produção`, **pare aqui** e corrija o `.env`.

## 2. `wbcpython doctor` — a instalação está completa?

Também local. Aponta configuração faltando e drivers ausentes antes de você
descobrir isso no meio de um ciclo.

## 3. `wbcpython check-sap` — o Service Layer responde?

Primeiro passo que usa a rede — e só faz `POST /Login`, `GET` e `POST /Logout`.
Erros de certificado e de credencial aparecem aqui, com orientação, em vez de
virarem traceback.

## 4. `wbcpython check-hana` — as views existem, e onde?

Diz em qual schema `VW_CLIENTE_MUNICIPIO_ALTA` e `VW_EVOL_OPORTUNIDADE_ALT`
existem. Desde agosto de 2026 elas existem nos **dois** schemas, e `HANA_SCHEMA`
acompanha a company que a integração escreve (`SBOALTAMIRAHOMOLOG` em
homologação). Se um dia faltarem no schema configurado, apontar para produção
volta a ser aceitável: **ler produção é permitido; escrever não.**

## 5. A suíte de testes

```bash
uv run pytest              # rápido, sem rede: domínio, segurança, mapeamento
uv run pytest --run-integration   # inclui os testes que tocam SAP/HANA de verdade
```

Os testes de integração são opt-in de propósito: sem a flag, a suíte roda em
qualquer máquina, inclusive sem driver nativo.

## 6. `wbcpython pendentes` — a prévia (ainda somente leitura)

**Este é o passo que faltava.** Mostra, orçamento a orçamento, qual regra o
domínio aplicaria e quais ações sairiam dela — sem criar, alterar ou cancelar
nada. É como ler o plano antes de executá-lo.

```bash
uv run wbcpython pendentes
uv run wbcpython pendentes --orcamento 00125535   # um caso só
uv run wbcpython pendentes --com-acao             # só o que resultaria em escrita
```

Cada linha traz `sit WBC`, `sit SAP`, revisão, se já existe cotação/pedido, a
regra decidida e as ações. `[ok]` = nada seria escrito; `[!]` = escreveria no
SAP. No fim, o resumo diz quantos dos avaliados resultariam em escrita.

Leia essa lista **antes** do passo 7. Se algum orçamento aparecer com uma ação
que você não esperava, é aqui que se descobre — de graça.

Numa janela típica (~950 oportunidades), a listagem completa passa de 2.900
linhas; `--com-acao` reduz para algumas dezenas. O filtro é de **exibição**:
todos continuam sendo avaliados, e o resumo do rodapé continua contando todos.
Problemas de dado — orçamento ausente no WBC, oportunidade sem `U_ORCNUM_WBC` —
aparecem mesmo com o filtro ligado.

### `ciclo --cancelados` — tratar só a leva dos cancelados

```bash
uv run wbcpython ciclo --cancelados
```

Restringe a execução aos orçamentos com SitCode 99 no WBC. Serve para confinar
o efeito de uma execução a um tipo de mudança — o que importa porque o
cancelamento da cotação é destrutivo e não se desfaz.

O filtro lê o SitCode do **WBC**, não o espelho `U_INO_StatusWBC` do SAP: o
espelho fica para trás justamente quando o orçamento acabou de ser cancelado,
que é o caso que se quer pegar. Orçamento sem situação no WBC fica de fora — na
dúvida, não entra numa execução destrutiva.

## 7. `wbcpython ciclo --orcamento XXXXX` — um caso, de verdade

O primeiro comando que escreve. Comece por **um** orçamento, escolhido da
prévia, e confira o resultado no SAP antes de ampliar.

```bash
uv run wbcpython ciclo --orcamento 00125535
```

## 8. `wbcpython ciclo` — a janela inteira, uma vez

Mesma coisa, sem filtro. Ainda é execução única: roda e sai.

## 9. `wbcpython worker` — o processo contínuo

Só depois que o passo 8 estiver limpo. Agenda o ciclo em intervalos
(`WORKER_INTERVAL_SECONDS`), sob trava de execução única — duas execuções nunca
se sobrepõem, ao contrário do console legado.

## 10. `wbcpython dashboard` — o que aconteceu

Painel em `http://localhost:8501`. Histórico de execuções, situação por
orçamento, erros e o log do worker na tela. Só lê o banco de tracking.

Confira as cinco abas e deixe a aba **Log** aberta durante um ciclo: as linhas
devem aparecer sozinhas, sem recarregar a página.

---

## Estado em homologação

O fluxo completo já roda: **cotação criada, vinculada à oportunidade e snapshot
do OrcDetalhe gravado**. Verificado no ambiente real com os orçamentos
`00125533` (atualização) e `00125531` (criação + vínculo).

Quatro exigências do SAP foram descobertas só rodando de verdade, e cada uma
está documentada no código onde importa:

| Erro do SAP | Causa | Onde está a correção |
|---|---|---|
| `-2028 Customer record not found` | faltava `CardCode` | `_payload_documento` |
| `-5002 Document total value must be zero or greater than zero` | faltava `DocumentLines` | `domain/linhas.py` |
| `-5002 Specify an active branch [OQUT.BPLId]` | faltava a filial | `FILIAL_PADRAO = 1` |
| `-5002 enter number greater than 0 [OOPR.MaxSumLoc]` | vínculo sem `MaxLocalTotal` | `vincular_documento` |

### Como o item do SAP é escolhido

**Não é pelo texto da linha.** O item vem do grupo de produto do WBC (`GRPCOD`)
através do de-para `@INO_GRP_PRODUTOS` no SAP — 11 grupos cadastrados, que
cobrem 100% das 20.997 linhas da tabela de integração. Grupo fora do de-para
cai em Porta-Paletes (grupo `2`) **com aviso no log e no acompanhamento**.

### Ponto de atenção conhecido

Se a criação do documento der certo mas o vínculo falhar, o documento fica
**criado e não vinculado** — e reexecutar não corrige, porque na execução
seguinte a cotação já existe e a regra muda para `espelha_status`. Aconteceu
uma vez com o `00125535` durante os testes. Vale decidir se a integração deve
detectar e completar vínculos pendentes.
