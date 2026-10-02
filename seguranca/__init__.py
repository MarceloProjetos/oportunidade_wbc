"""Security base of the .11 (F1 of docs/PLANO_MIRA_AGENTE_11.md, 02/10/2026).

Shared by the API (8077), the Controle de Produção (8080) and the MCP (8078):

- ``credenciais`` — one credential per client, each with its scopes; the old ``OS_API_KEY``
  keeps working as the "chave-mestra" (admin) so no current client breaks.
- ``auditoria`` — one JSON line per call, per service, kept 30 days; no route deletes it.
- ``agente`` — the rules that apply only to credentials marked as an agent: the off switch
  and writes only during business hours.

Stdlib only: the MCP and the Flask API import it without pulling anything else.
"""
