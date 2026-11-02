# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

## [2026-11-02] — Organograma: reconciliado com o .90 (ficha da Mira, Lia, planos)

O repo do .90 entrou na sessão e mostrou que o escritório já existe lá (cadastro de agentes com dono por
ferramenta, tarefas entre agentes, Escritório com fila e feed). Marcelo escolheu reconciliar antes da F3.

- `operacao/organograma.toml`: a Mira passa a **esperar** `servico:reiniciar` e a pedir `reiniciar_servico` —
  no .90 ela encaminha o reinício que uma pessoa pediu e substitui o Téo; não é sobra (D8 resolvida). Entra a
  **Lia** (Marketing), sem credencial da .11, e o tema `marketing` (substituto Marcelo, a confirmar).
- `operacao/organograma.py`: agente sem `credencial` é aceito se não pede nada nem espera escopo; a conferência
  diz `sem_acesso_a_11` e não reprova. Card da Central mostra "Não usa a .11".
- `docs/PLANO_ORGANOGRAMA.md`: seção "Reconciliação com o .90"; F3 vira ferramenta de leitura da Mira + teste
  que confere os dois cadastros (sem mexer no prompt); **F4 e F5 canceladas** (o repasse é o `pedir_ao_teo`).
- `docs/PLANO_QUADRO_TAREFAS.md`: reescrito — o quadro **não** será construído na .11; sobram 4 lacunas,
  propostas para o plano do .90. `docs/ORGANOGRAMA_11.md`: quem é fonte de quê.
- Depois do deploy, a conferência da Mira fica verde na Central (era amarela).

## [2026-11-02] — Organograma do escritório, F2: a ficha pela API, pelo MCP e na Central

F2 do `docs/PLANO_ORGANOGRAMA.md`; contrato em `docs/ORGANOGRAMA_11.md`. Só leitura; o cargo continua sem dar
permissão.

- `GET /operacao/organograma` (`leitura`): membros, temas e a situação do arquivo. A conferência ficha ×
  credencial vai **só para administrador** (Central, chave-mestra) — as agentes e os outros clientes com
  `leitura` recebem `conferencia: null`. `?para=<id>` devolve a ficha de um membro; para agente, com o
  `texto_cargo` pronto para o prompt (a regra "o cargo não te dá poder" é sempre a última linha).
- `GET /operacao/organograma/temas/<tema>` (`leitura`): dono e substitutos; tema desconhecido vai para `outro`
  (`casou: false`), nunca 404. Arquivo inválido sem nenhuma versão boa → 503 com onde está o erro.
- MCP: ferramentas `organograma` e `quem_cuida_de` (`leitura`); o tema vai num segmento só da URL.
- Página inicial da Central: card "Organograma" com cada agente e a conferência (verde / amarelo "escopo a
  mais" / vermelho "falta escopo"), a lista de temas e o aviso quando o arquivo no disco estiver inválido.
- **Pede deploy da .11** (`deploy_update.bat`: API e MCP). Depois: abrir a Central e ver o card.

## [2026-11-02] — Organograma do escritório, F1: a ficha, a validação e a conferência (só leitura)

F1 do `docs/PLANO_ORGANOGRAMA.md`. **Regra: o cargo nunca dá permissão** — escopos, papéis de aprovação e
regras de agente continuam onde estavam; nada que autoriza lê o organograma (teste por AST).

- `operacao/organograma.toml`: uma ficha por agente (Mira, Téo), pessoa (Marcelo; Felipe e Gabriel como
  substitutos do diretor, D2; Anderson) e equipe (PCP, Comercial, Produção), e 10 temas com dono e
  substitutos. Repo público: só primeiro nome; `@` e telefone são recusados.
- `operacao/organograma.py`: carrega (cache por mtime; edição quebrada mantém a última versão válida e loga
  ERRO), valida tudo de uma vez (referências, laço de escalada entre agentes, toda escalada chega a uma
  pessoa, ação do `CATALOGO` com o escopo esperado, escopo proibido a agente, agente nunca `aprova`) e
  **confere** ficha × `state/credenciais.json` sem escrever: `faltam` e escopo proibido a agente reprovam, `sobram` só avisa (o
  `servico:reiniciar` da Mira aparece aqui; tirar é decisão do Marcelo, D8).
- CLI: `python -m operacao.organograma [conferir | tema <id>]` (conferir sai 1 se um agente vai ser recusado).
- `tests/test_organograma.py`: 83 testes, incluindo o 403 com a ficha prometendo um escopo que a credencial não tem
  e um interpretador limpo provando que nada que autoriza carrega o organograma. Revisor isolado: as falhas
  que ele achou (escopo proibido editado à mão passava como `ok`, BOM do Bloco de Notas, erro repetindo o
  telefone, testes que passavam com o código quebrado) foram corrigidas e conferidas por mutação.
- Não pede deploy nem restart: nada roda sozinho ainda (a API e o MCP chegam na F2). Na .11, depois do
  `git pull`: `python -m operacao.organograma conferir`.
- `CHANGELOG.md`: outubro arquivado em `docs/changelog/2026-10.md`.

