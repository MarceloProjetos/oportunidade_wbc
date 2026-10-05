"""Read-only views of the .11 itself, for the Mira agent (F2 of docs/PLANO_MIRA_AGENTE_11.md).

Each module answers one question the agent could not answer without someone opening the
server: which services are up, whether a host/port answers FROM the .11, what changed in a
sales order and who changed it, what the worker logged about one quote, and which code is
running / how the last deploy went. Nothing here writes: no service control, no command,
no free-form host or file — closed lists only (rule 5 of the plan).
"""
