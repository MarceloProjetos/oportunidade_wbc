"""Exceções próprias da aplicação."""


class ServiceLayerError(Exception):
    """Erro genérico ao falar com a SAP Service Layer.

    Guarda o status HTTP e o corpo da resposta de erro (a Service Layer devolve
    JSON com `error.code` e `error.message.value`) para facilitar diagnóstico —
    equivalente ao `Company.GetLastErrorDescription()` usado no addon C# original
    (ex.: `Form2.msgSAP = Program.getCompany().GetLastErrorDescription();`).
    """

    def __init__(self, message: str, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class ServiceLayerAuthError(ServiceLayerError):
    """Sessão expirada ou credenciais inválidas — deve disparar novo login."""


class WbcDatabaseError(Exception):
    """Erro ao consultar/gravar no SQL Server externo do sistema WBC."""
