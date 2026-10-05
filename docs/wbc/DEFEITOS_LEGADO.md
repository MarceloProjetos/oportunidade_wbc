# Defeitos encontrados no sistema legado (WBCServConsole)

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

Levantados durante a Fase 2, ao extrair a máquina de estados do `Program.cs` e
do `Querys.resx` do projeto C# original.

Este documento existe por dois motivos: registrar decisões conscientes sobre o
que foi ou não portado para a nova solução, e sinalizar problemas que podem
estar **afetando a operação hoje**, independentemente da reescrita.

> **Situação:** as correções possíveis já foram aplicadas ao código C# legado.
> Os arquivos alterados (`Program.cs`, `Querys.resx`), os originais preservados
> (`*.original`) e o registro do que mudou estão em
> `WBCServConsole/CORRECOES_LEGADO.md`.
>
> ⚠️ Antes de compilar e publicar, leia o alerta sobre **volume de documentos**
> naquele arquivo: se a integração está restrita a um único orçamento desde
> dezembro, remover o filtro faz a primeira execução processar todo o acumulado.
>
> Os defeitos 3, 4, 5 e 7 **não** foram corrigidos — dependem de decisão de
> negócio ou são refatoração grande demais para um sistema que será substituído.

Legenda de severidade:

- 🔴 **Crítico** — pode estar causando problema em produção agora
- 🟠 **Relevante** — afeta corretude ou manutenção, sem urgência imediata
- 🟡 **Menor** — ruído, dívida técnica

---

## 🔴 1. A consulta principal está filtrada para UM único orçamento — ✅ CORRIGIDO

`Querys.resx` → `GetDocNumOportunidades` termina com:

```sql
and T0."U_ORCNUM_WBC" = '00121819'
```

Essa condição restringe **toda a integração** a um único orçamento. Com ela no
lugar, nenhum outro orçamento é processado — a varredura que o `Main` faz sobre
`oRs` retorna, no máximo, essa uma linha.

Tem toda a cara de código de depuração que acabou versionado: o `Program.cs`
tem, logo acima, blocos vazios do tipo `if (OportunidadeId == "00121819") { string teste = ""; }`,
usados para parar o depurador nesse orçamento específico.

**Evidência adicional (confirmada):** o executável
`bin/x64/Debug/WBCServConsole.exe`, compilado em dez/2025 — mesma data da última
alteração do `Program.cs` —, tem essa consulta embutida **com o filtro**. Não dá
para saber daqui qual binário está publicado no servidor, mas se for esse, a
integração está restrita a um orçamento desde então.

**Por que isso importa agora:** se essa é a versão que está rodando em produção,
a integração pode estar parada para todos os demais orçamentos. Vale conferir,
antes e independentemente da reescrita, qual versão está de fato publicada no
servidor e se essa linha está presente nela.

**Na nova solução:** o filtro por orçamento existe apenas como parâmetro
opcional de execução (útil para reprocessar um caso específico), nunca fixo na
consulta.

---

## 🔴 2. Um orçamento sem número aborta a execução inteira — ✅ CORRIGIDO

`Program.cs`, dentro do laço que percorre as oportunidades:

```csharp
if (OportunidadeId == "")
    return;
```

`return` sai do `Main` — ou seja, encerra o processo. O efeito pretendido era
quase certamente "pule este registro" (`continue`). Como está, um único registro
com número vazio **interrompe o processamento de todos os orçamentos seguintes**
daquela execução, silenciosamente (não há log nesse ponto).

**Na nova solução:** cada orçamento é processado de forma isolada; uma falha em
um registro é registrada e o laço segue para o próximo.

---

## 🟠 3. Duas condições são tautologias (sempre verdadeiras) — ❌ depende de decisão

```csharp
(item.SitCode != 30 || item.SitCode != 60 || item.SitCode != 61 ||
 item.SitCode != 70 || item.SitCode != 90 || item.SitCode != 99)
```

Um número não pode ser igual a dois valores diferentes ao mesmo tempo, então
pelo menos uma das desigualdades é sempre verdadeira — e, sendo `||`, a
expressão inteira é sempre verdadeira. O autor provavelmente queria `&&`.

Ocorre em dois pontos, e o efeito prático é que a condição desaparece:

| Local | Condição escrita | Efeito real |
|---|---|---|
| Criação de cotação | `cotação == 0 && (tautologia)` | `cotação == 0` |
| Espelhamento de status | `cotação != 0 && (tautologia)` | `cotação != 0` |

**Na nova solução:** implementado o comportamento **efetivo** (o que a
tautologia de fato produz hoje), não o que a intenção aparente sugeria — mudar
isso agora alteraria o comportamento em produção sem que ninguém tivesse pedido.
Os ramos correspondentes estão nomeados (`sem_cotacao_cria`, `espelha_status`) e
cobertos por teste, então a decisão fica visível e pode ser revista.

⚠️ **Pendente de decisão de negócio:** se a intenção era `&&`, o comportamento
correto seria diferente. Vale confirmar com quem conhece a regra.

---

## 🟠 4. Exceções de negócio fixas no código, por número de oportunidade — ❌ depende de decisão

```csharp
if (OportunidadeId != "00118376")
```

Um orçamento específico é excluído da atualização de pedido. Não há comentário
explicando o motivo — provavelmente um caso problemático contornado às pressas.

**Na nova solução:** **não portado.** Exceções pontuais de dados não são regra de
negócio e não devem viver no código. Se o caso ainda for real, deve ser tratado
como dado (uma lista de exclusões configurável) e com justificativa registrada.

---

## 🟠 5. A janela de datas da consulta mistura dois deslocamentos — ❌ depende de decisão

```sql
T0."OpenDate" >= TO_DATE(
  YEAR(ADD_MONTHS(CURRENT_DATE, -6)) || '/' || MONTH(ADD_MONTHS(CURRENT_DATE, -9)) || '/' || '1',
  'YYYY/MM/DD'
)
```

O **ano** vem de "hoje menos 6 meses" e o **mês** de "hoje menos 9 meses". A
data resultante varia de forma difícil de prever conforme o mês corrente — em
janeiro, por exemplo, os dois deslocamentos caem em anos diferentes, e a data
montada não corresponde nem a 6 nem a 9 meses atrás.

**Na nova solução:** a janela de busca é um parâmetro explícito e único, com
teste que verifica o intervalo calculado.

---

## 🟠 6. Não há registro de log funcionando — ✅ PARCIALMENTE CORRIGIDO

O método `AddLog` do `ServiceProcess` está com o corpo inteiramente comentado —
é uma função vazia. O que resta são chamadas a `EventLog.WriteEntry`, gravando
no Log de Eventos do Windows.

Além disso, há um `catch (Exception ex)` que faz `ex.InnerException.ToString()`:
se a exceção **não** tiver `InnerException` (o caso comum), isso lança um
`NullReferenceException` dentro do próprio tratador — e a causa original do erro
se perde por completo.

**Na nova solução:** logging estruturado desde o início, e nenhum tratador de
erro que possa mascarar a exceção que estava tratando.

---

## 🟡 7. SQL montado por concatenação de string — ❌ não corrigido no legado

Todas as consultas usam `String.Format` com valores interpolados diretamente.
Como os valores vêm do próprio banco, o risco prático de injeção é baixo — mas
é o padrão errado, e basta uma aspas simples num campo de texto para quebrar a
consulta.

**Na nova solução:** consultas parametrizadas, sempre.

---

## 🟡 8. Código morto e de depuração versionado — ✅ CORRIGIDO

- `correcOrc()` — lista de ~800 números de orçamento fixa no código, de uma
  correção pontual antiga.
- `CriaItem()` — criação automática de itens, fora do fluxo ativo.
- Blocos vazios `if (OportunidadeId == "00116496") { string teste = ""; }`.
- `if (true) { ... } else { ...log de erro... }` — o `else` é inalcançável, e o
  log de erro que estava lá dentro nunca é emitido.
- Variáveis `teste2`, `teste10`, `teste11`, `teste12` atribuídas e nunca usadas.
- `testeouterro = testeouterro` — atribuição de uma variável a si mesma.
- `static int contagem = 10000` — declarada e nunca usada.

**Na nova solução:** nada disso foi portado.

## A troca de PN deixou 7 pedidos cancelados num orçamento só

Descoberto ao investigar o `00124045`. O orçamento `00117039` tem, em produção
e em homologação, este histórico de pedidos — todos de **08/02/2024**:

| Pedido | Parceiro | Cancelado |
|---|---|---|
| 80500 | C007709 (o da oportunidade) | não |
| 80501 | C007709 | sim |
| 80502 a 80508 | C000678 (o `PN_Correc`) | **sim, os sete** |

Sete pedidos criados no parceiro corrigido e cancelados em seguida, no mesmo
dia. É o `cancelar-e-recriar` da troca de PN rodando em looping: a cada passada
o pedido novo era criado no parceiro certo e, na passada seguinte, a seleção do
"pedido vigente" voltava a apontar para o `80500` (não cancelado, no parceiro
antigo) — a troca parecia pendente de novo, e o ciclo recomeçava.

O `00117112` mostra a mesma coisa em menor escala: quatro pedidos cancelados,
e o vivo (`80686`) num **terceiro** parceiro, nem o da oportunidade nem o
`PN_Correc`.

**Na nova solução:** o risco de repetição desapareceu por dois caminhos
independentes. `troca_de_parceiro_pendente` compara o parceiro que o pedido já
tem (idempotência por construção, como o `ChecaPNPedido`), e a guarda
`pedido_corrigido_a_mao` impede que o ciclo sequer considere esses casos — ver
`DECISOES.md`.

**Nada foi feito nos documentos existentes.** Os cancelados de 2024 continuam
lá; a correção impede casos novos, não limpa os antigos.

---

## Resumo do que exige decisão humana

| # | Pergunta | Para quem |
|---|---|---|
| 1 | A versão em produção tem o filtro `= '00121819'`? A integração está processando todos os orçamentos? | TI / responsável pela publicação |
| 3 | As tautologias deveriam ser `&&`? Qual era a intenção da regra? | Quem conhece a regra de negócio |
| 4 | O orçamento `00118376` ainda precisa de tratamento especial? | Comercial / quem pediu a exceção |
| 5 | Qual deve ser a janela de busca correta: 6 meses, 9 meses, outra? | Comercial / TI |
| 6 | O WBC marca `U_INO_Update = 'Y'` ao preencher o `U_INO_PN_Correc`? Se não marcar, a troca de PN nunca acontece — ver `DECISOES.md`. | Equipe WBC |
