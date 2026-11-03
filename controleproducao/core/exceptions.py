"""Application-specific exceptions."""


class ServiceLayerError(Exception):
    """Generic error talking to the SAP Service Layer.

    Keeps the HTTP status and the error body (the SL answers JSON with `error.code` and
    `error.message.value`) for diagnosis — the equivalent of `Company.GetLastErrorDescription()`
    in the original C# addon (e.g. `Form2.msgSAP = Program.getCompany().GetLastErrorDescription();`).
    """

    def __init__(self, message: str, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class ServiceLayerAuthError(ServiceLayerError):
    """`/Login` rejected (bad credentials); session expiry is a 401 handled by the single re-login."""


class WbcDatabaseError(Exception):
    """Failure reading from the external WBC SQL Server or from HANA (both paths are read-only)."""
