# Resources/Solda.txt

Este diretório é onde `controleproducao/modules/pedidos_wbc/listas_fixas.carregar_solda()` procura o
arquivo `Solda.txt` (um código por linha) — equivalente ao `Resources/Solda.txt` do addon
C# original, usado por `CriaItem`/`UpdateItem` para decidir `ItemsGroupCode = 332`.

Esse arquivo **não veio no projeto enviado em 15/09/2026** (ver seção 7.4/9 do
`migration_guide.md`). Assim que o Anderson enviar o conteúdo real, basta colocar o
arquivo aqui como `Solda.txt` — nenhum código precisa mudar, `carregar_solda()` já lê
deste caminho automaticamente.

Até lá, o sistema se comporta como se a lista estivesse vazia (nenhum item cai em 332 por
engano — todos os itens fora da lista de prefixos de grupo 333 vão para o grupo 358,
mesmo efeito de uma lista vazia no C# original).
