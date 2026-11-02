# PLANO — Quadro de Tarefas do escritório

> **Status (02/11/2026): reescrito na reconciliação com o .90 — o quadro NÃO será construído na .11.**
> A 1ª versão (mesmo dia) propunha uma tabela `tarefas` aqui. Quando o repo do .90 entrou na sessão, apareceu
> que o .90 já tem o núcleo disso, mais maduro (`docs/PLANO_CENTRO_OPERACAO_ESCRITORIO.md` de lá, E0–E7). O que
> sobra são 4 lacunas, propostas abaixo para entrar **no plano do .90**, se o Marcelo quiser. A 1ª versão está
> no git (`git log -- docs/PLANO_QUADRO_TAREFAS.md`).

## O que a 1ª versão pedia × o que o .90 já tem

| Pedido da 1ª versão | No .90 (repo `web_orcaview`) |
| --- | --- |
| Tarefa com dono, estado, quem pediu, prazo, resultado | `services/mira_tarefas.py`: tarefas entre agentes em SQLite durável (`mira_tarefas_base`), gravadas antes da RAM, recuperadas no restart, trilha no diário |
| "Pegar" atômico / um dono por vez | `mira_agentes.CAPACIDADES` (uma ferramenta, um dono; `tarefa_so_do_dono`) + o portão (`mira_autorizar`) |
| A aprovação como passo da tarefa | E4 (V118.1407): estado da aprovação espelhado no episódio, contagem regressiva, lembrete aos 10 min |
| Alerta abre tarefa e fecha sozinho | O Téo abre e fecha **incidentes** na ronda de 3 min, com triagem sem modelo (E1c) e o quadro do incidente (`mira_zelador_quadro`) |
| Repasse Mira → Téo | `pedir_ao_teo` (investigar, acompanhar, reiniciar) e "Pedir ao Téo" pela tela (E3b); `pedir_a(agente)` previsto na E6 |
| Tela do quadro | Escritório: equipe, fila e feed de eventos reais (E3a), inspeção e despacho (E3b) |
| Resumo da manhã | Cartão da manhã com as metas do dia e alertas com limiar (E7, `mira_metas.py`) |
| Medir se funciona | Metas do §9 de lá, calculadas sem modelo todo dia |

**Conclusão honesta:** construir a tabela na .11 duplicaria o .90 e criaria dois lugares para a mesma pergunta.
As 4 ressalvas da 1ª versão (tabela própria, não copiar o SAP, criação automática, pequeno) o .90 já cumpre
do jeito dele.

## As 4 lacunas que sobram (propostas para o plano do .90)

Não achei estas peças no .90 (procura por nome e por docstring; **confirmar com quem mantém o .90 antes de
desenhar**):

1. **Tarefa cujo dono é uma PESSOA ou equipe** (PCP, Comercial, Produção), com "esperando quem / o quê".
   Hoje as tarefas são entre agentes; a pessoa aparece como quem aprova. O organograma da .11 já tem as pessoas
   e equipes (`GET /operacao/organograma`) — seria a lista de donos possíveis, sem cadastro novo.
2. **Conversa do WhatsApp sem desfecho vira pendência.** Depende da decisão 1 de lá (WhatsApp congelado).
3. **"Esse pedido está esperando quem?"** — uma leitura que junta, na hora, a situação do SAP, as aprovações
   abertas do pedido, as solicitações de data de entrega e as tarefas abertas que citam o pedido. Nada guardado
   de novo (a ressalva 2 continua valendo).
4. **"O que ficou pendente ontem?"** como pergunta da Mira, sobre o diário de tarefas que já existe.

Encaixe sugerido: a lacuna 1 junto da E6 (`pedir_a(agente)` → `pedir_a(membro)`, com o organograma da .11 dizendo
quem é pessoa); as lacunas 3 e 4 como leituras da Mira (efeito `leitura`, sem mudar o prompt); a 2 depois da
decisão 1.

## O que continua valendo da 1ª versão

- **Quem olha todo dia** (Q-D1 da 1ª versão): sem uma pessoa nomeada, nenhuma das lacunas compensa.
- **Critério de desistência:** medir 30 dias; se ninguém usa, não cresce.
- **O cargo não dá permissão:** dono de tarefa não ganha escopo por ser dono (`docs/PLANO_ORGANOGRAMA.md`).
