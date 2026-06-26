# Plano — Painel das Oportunidades (o que o `run_scheduler.bat` roda)

> **Status:** proposta para discussão (nada implementado ainda além do `run_all.bat`).
> **Autor:** Claude (Opus 4.8) — 2026-06-26

Espelhar a página de OS, mas para o **pipeline agendado de oportunidades** (o que sobe pelo
`run_scheduler.bat`): ver os **últimos sincronismos**, **limpar o log** e **forçar um
sincronismo** sob demanda.

---

## 1. O que o `run_scheduler.bat` executa (análise)

```text
run_scheduler.bat
  └─ scripts/scheduled_execution.py   (APScheduler: a cada 30 min, 07–18h, dias úteis)
       └─ extract_sap_to_supabase.main()
            • extrai a view SAP  VW_EVOL_OPORTUNIDADE_ALT  (últimos 6 meses por CreateDate)
            • enriquece com SQL Server (SITCOD, ORCALTDTH)
            • grava em  public.oportunidades   (modo SNAPSHOT — carrega-depois-poda)
            • loga em   public.sincronizacao_log  (mantém os 6 mais recentes)
```

**Dados disponíveis para o painel:**

| Fonte | Conteúdo | Uso no painel |
|---|---|---|
| `sincronizacao_log` | `data_hora_sincronizacao`, `duracao_segundos`, `status`, `qtd_registros` (**sem** `nped` — é carga total) | "Últimos sincronismos" |
| `oportunidades` | a carga atual (snapshot dos últimos 6 meses) | (opcional) total de linhas / última atualização |
| `config.py` | intervalo (30min), janela (7–18h), dias úteis | (opcional) mostrar a agenda configurada |

> Diferença-chave vs OS: aqui **não há NPED**. "Forçar sincronismo" roda a **carga inteira**
> (snapshot), que é mais pesada que a de um pedido (view maior + enriquecimento SQL Server)
> — pode levar dezenas de segundos.

---

## 2. Painel proposto (espelho da página de OS)

Um card "Oportunidades (agendado)" com, conforme seu rascunho:

- **Forçar sincronismo** — botão que dispara a carga completa agora (com spinner e aviso
  "pode demorar").
- **Últimos sincronismos** — lista do `sincronizacao_log` (status, nº de registros, duração,
  data/hora). Sem NPED.
- **Limpar log** — botão 🗑 (com confirmação), igual ao da página de OS.

**Endpoints novos na mesma API (`api.py`):**

| Rota | Método | O que faz |
|---|---|---|
| `/oportunidades/historico` | GET | Lê `sincronizacao_log`. Requer `X-API-Key`. |
| `/oportunidades/historico` | DELETE | Limpa o log. Requer `X-API-Key`. |
| `/oportunidades/sincronizar` | POST | Roda `extract_sap_to_supabase.main()` (carga total). Requer `X-API-Key`. |
| `/oportunidades` (ou seção em `/`) | GET | A página do painel. |

Reaproveita o que já existe: `_supabase()`, `_fetch_log`/`_clear_log` (generalizados p/
aceitar o nome da tabela), `_autorizado`, o lock de serialização e o padrão visual da página.

---

## 3. ⚠️ Ponto que precisa de decisão — concorrência

O agendador (`run_scheduler.bat`, **processo A**) e a API (`run_api.bat`, **processo B**)
são **dois processos**. Hoje o agendador tem um *lock* interno que impede duas cargas ao
mesmo tempo — **mas esse lock não vale entre processos**. Se você clicar **"Forçar
sincronismo"** (processo B) enquanto o agendador (processo A) está rodando a carga, as **duas
cargas de oportunidades rodam juntas** — e, no modo snapshot (carrega-depois-poda), uma pode
podar as linhas recém-inseridas da outra. Bagunça.

**Sugestões (escolha uma):**

1. **Lock de arquivo compartilhado (recomendado, mudança pequena).** Tanto o job do
   agendador quanto o `/oportunidades/sincronizar` adquirem o **mesmo** lock em disco antes
   de chamar `main()`. Se já houver uma carga rodando, o "forçar" responde **409 — já há uma
   sincronização em andamento** (e o agendado simplesmente espera/pula). Usa a lib
   `filelock` (pura Python, ~1 dependência). Ambos rodam no mesmo servidor → funciona.
2. **Um único processo** (Flask + APScheduler juntos no mesmo `python`): o lock interno
   passa a valer para os dois. Mais elegante, porém refatora como o serviço sobe (e você
   pediu o `run_all.bat` com dois processos).
3. **Não expor "forçar" na API** — só mostrar histórico/limpar, e deixar o disparo manual
   pelo próprio agendador. Mais seguro, menos prático.

> Minha recomendação: **opção 1** (file-lock). Mantém os dois `.bat` separados, é pouca
> mudança, e blinda contra a corrida. Aplico o lock também no agendador (no `job_execucao`).

---

## 4. Decisões a confirmar

1. **Layout:** página separada **`/oportunidades`** (foco) ou **um painel só** em `/` com
   dois cards lado a lado (OS | Oportunidades), como no seu rascunho? *(recomendo um painel
   unificado em `/` com 2 colunas, empilhando no celular — bate com o desenho.)*
2. **Concorrência:** topa a **opção 1 (file-lock + dependência `filelock`)**?
3. **"Forçar sincronismo":** mesma `OS_API_KEY` (sim) e com confirmação na tela (é pesado)?
4. **Extras opcionais:** mostrar o **total de oportunidades** na tabela e a **agenda
   configurada** (30min / 07–18h)? (leitura barata, dá contexto.)

---

## 5. Sugestões sinceras

- **Unificar numa página só** (`/`) com dois cards reduz URLs e bate com seu rascunho; o
  título vira "Painel de Sincronização" com as duas seções.
- **Reaproveitar código:** generalizo `_fetch_log/_clear_log` para receberem o nome da tabela
  (servem OS e oportunidades) e o front vira um componente reutilizado nas duas seções.
- **Feedback do "forçar":** como a carga total demora, mostrar spinner + "pode levar até ~1
  min" e **desabilitar** o botão durante a execução; ao terminar, recarregar o histórico.
- **Segurança:** o `/oportunidades/sincronizar` e o `DELETE` ficam atrás da `OS_API_KEY`
  (mesma da OS). Nada novo a expor.
- **`run_all.bat` (já entregue):** sobe os dois com `start`. Para **NSSM**, registre os dois
  `.bat` como serviços separados (o launcher encerra após iniciar, e o NSSM acharia que caiu).

---

## 6. Já entregue nesta rodada

- **`run_all.bat`** — launcher único que sobe `run_scheduler.bat` + `run_api.bat` (cada um no
  seu processo). Pronto para Task Scheduler (ONSTART) ou uso manual.
