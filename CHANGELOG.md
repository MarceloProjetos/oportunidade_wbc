# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

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

