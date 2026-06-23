"""Testes da conexão SAP HANA compartilhada."""

from sap_connection import is_sap_tenant_error


def test_is_sap_tenant_error_matches_not_connected():
    assert is_sap_tenant_error(Exception('not connected'))
    assert is_sap_tenant_error(Exception('NOT  CONNECTED to database'))


def test_is_sap_tenant_error_ignores_other_errors():
    assert not is_sap_tenant_error(Exception('connection timeout'))
    assert not is_sap_tenant_error(Exception('authentication failed'))
