"""Testes do `ServiceLayerClient` — as partes que não dependem de um SAP real.

Cobrem duas classes de bug encontradas testando contra a homologação em 15/09/2026, que
não davam erro em teste nenhum e só apareceram em ambiente real (ver seções 7.8 e 7.13 do
migration_guide.md):

1. `Decimal` (vindo de `hdbcli`/`pyodbc`) chegando ao corpo JSON da requisição;
2. chave de entidade com `#` no código do item, que trunca a URL no fragmento.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from controleproducao.core.service_layer_client import _formata_chave, _json_safe


# ---------------------------------------------------------------------------
# _json_safe — seção 7.8 do migration_guide.md
# ---------------------------------------------------------------------------
def test_json_safe_converte_decimal_para_float():
    """`hdbcli`/`pyodbc` devolvem Decimal para colunas numéricas, e o json.dumps do httpx
    não serializa Decimal (quebrava em `PlannedQuantity` ao criar a Ordem de Produção)."""
    assert _json_safe({"PlannedQuantity": Decimal("12.500")}) == {"PlannedQuantity": 12.5}


def test_json_safe_converte_datas_para_iso():
    assert _json_safe({"d": date(2026, 1, 2)}) == {"d": "2026-01-02"}
    assert _json_safe({"d": datetime(2026, 1, 2, 3, 4, 5)}) == {"d": "2026-01-02T03:04:05"}


def test_json_safe_é_recursivo_em_dicts_e_listas():
    """As linhas de documento vão aninhadas no corpo — a conversão precisa descer neles."""
    corpo = {"Lines": [{"Qty": Decimal("2")}, {"Qty": Decimal("3")}], "Sub": {"X": Decimal("1")}}
    assert _json_safe(corpo) == {"Lines": [{"Qty": 2.0}, {"Qty": 3.0}], "Sub": {"X": 1.0}}


def test_json_safe_preserva_tipos_que_o_json_ja_entende():
    corpo = {"s": "texto", "i": 7, "f": 1.5, "b": True, "n": None}
    assert _json_safe(corpo) == corpo


# ---------------------------------------------------------------------------
# _formata_chave — seção 7.13 do migration_guide.md
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "chave, texto, esperado",
    [
        # O bug real: código de item com '#'. Sem percent-encoding o '#' vira fragmento da
        # URL e o servidor recebe só `Items('ESCESP00000SUP000000` → "Unrecognized resource path".
        ("ESCESP00000SUP000000#1205#210#0", True, "'ESCESP00000SUP000000%231205%23210%230'"),
        # DocEntry: chave numérica, sem aspas.
        (19466, False, "19466"),
        ("19466", False, "19466"),
        # Code de UDO é o TEXTO "1" — `INO_SETUPOBJ(1)` sem aspas seria recusado.
        ("1", True, "'1'"),
        # Item cujo código é só de dígitos continua sendo chave de texto.
        ("1234567", True, "'1234567'"),
        # Chamador que já montou as aspas: desembrulha e reencoda, sem encodar as aspas.
        ("'1'", False, "'1'"),
        ("'ABC#1'", False, "'ABC%231'"),
        # Aspa simples dentro do literal é escapada duplicando (regra do OData).
        ("O'BRIEN", True, "'O''BRIEN'"),
        # Demais caracteres que quebrariam a URL.
        ("A B/C?D%E", True, "'A%20B%2FC%3FD%25E'"),
    ],
)
def test_formata_chave(chave, texto, esperado):
    assert _formata_chave(chave, texto) == esperado


def test_formata_chave_nao_gera_duplo_encoding_no_httpx():
    """Garante que o `%23` que produzimos chega ao servidor como `%23`, e não `%2523`
    (httpx re-encoda URLs; se ele escapasse o nosso '%', a chave iria errada)."""
    import httpx

    chave = _formata_chave("ESCESP00000SUP000000#1205#210#0", texto=True)
    req = httpx.Client(base_url="https://x:50000/b1s/v1").build_request(
        "PATCH", f"/Items({chave})"
    )
    assert req.url.raw_path.decode() == "/b1s/v1/Items('ESCESP00000SUP000000%231205%23210%230')"
    assert req.url.fragment == ""


def test_ca_bundle_e_timeout_do_env_sao_respeitados():
    """24/09/2026: `SL_CA_BUNDLE` e `SL_TIMEOUT_SECONDS` estavam no `.env.example` mas o
    cliente ignorava os dois (verify só por `SL_VERIFY_SSL`, timeout fixo em 60 s). No
    Windows o Python não enxerga a CA interna pelo repositório do sistema, então o
    `.pem` da CA é o jeito de manter a verificação ligada."""
    from types import SimpleNamespace

    from controleproducao.core.service_layer_client import _verificacao_tls

    base = dict(sl_verify_ssl=True, sl_ca_bundle=None)
    assert _verificacao_tls(SimpleNamespace(**base)) is True
    assert _verificacao_tls(SimpleNamespace(**{**base, "sl_ca_bundle": r"C:\ca\interna.pem"})) == r"C:\ca\interna.pem"
    # Desligar a verificação vence o bundle — é a escolha explícita de quem configurou.
    assert _verificacao_tls(SimpleNamespace(sl_verify_ssl=False, sl_ca_bundle=r"C:\ca.pem")) is False


# ---------------------------------------------------------------------------
# Trava pelo IP e /Logout (28/09/2026 — pacote dentro do SIS)
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402

import httpx  # noqa: E402

from controleproducao.config import Settings  # noqa: E402
from controleproducao.core.service_layer_client import ServiceLayerClient  # noqa: E402
from wbcpython.safety import ProductionWriteBlocked  # noqa: E402


def _cliente(company_db: str, chamadas: list, *, logout_falha: bool = False) -> ServiceLayerClient:
    """A client whose HTTP goes to a recorder instead of a Service Layer."""
    settings = Settings(
        _env_file=None, sl_base_url="http://sl.teste/b1s/v1", sl_company_db=company_db,
        sl_username="u", sl_password="p", sl_verify_ssl=False,
    )

    def responde(request: httpx.Request) -> httpx.Response:
        chamadas.append((request.method, request.url.path))
        if request.url.path.endswith("/Logout") and logout_falha:
            raise httpx.ConnectError("SL fora do ar")
        return httpx.Response(200, json={"value": []})

    cliente = ServiceLayerClient(settings)
    cliente._client = httpx.AsyncClient(base_url=settings.sl_base_url, transport=httpx.MockTransport(responde))
    return cliente


def test_escrita_em_producao_fora_da_11_e_recusada_antes_do_login():
    """The conftest pins PRODUCTION_MACHINE_IP to a never-local address: this is "another
    machine". The refusal happens before /Login — a refused write opens no session."""
    chamadas: list = []
    cliente = _cliente("SBOALTAMIRAPROD", chamadas)

    async def roda():
        async with cliente as sl:
            with pytest.raises(ProductionWriteBlocked):
                await sl.post("Orders", {"CardCode": "C1"})
            with pytest.raises(ProductionWriteBlocked):
                await sl.patch("Orders(1)", {"Comments": "x"})
            with pytest.raises(ProductionWriteBlocked):
                await sl.post("Items('A%2FB')", {"ItemCode": "A/B"})  # the $batch path too

    asyncio.run(roda())
    assert chamadas == []


def test_leitura_em_producao_continua_livre_de_qualquer_maquina():
    chamadas: list = []
    cliente = _cliente("SBOALTAMIRAPROD", chamadas)

    async def roda():
        async with cliente as sl:
            await sl.get("Orders(1)")

    asyncio.run(roda())
    assert [c[0] for c in chamadas] == ["POST", "GET", "POST"]  # Login, GET, Logout
    assert chamadas[1][1].endswith("/Orders(1)")


def test_homologacao_escreve_de_qualquer_maquina():
    chamadas: list = []
    cliente = _cliente("SBOALTAMIRAHOMOLOG", chamadas)

    async def roda():
        async with cliente as sl:
            await sl.post("Orders", {"CardCode": "C1"})

    asyncio.run(roda())
    assert ("POST", "/b1s/v1/Orders") in chamadas


def test_na_11_a_escrita_em_producao_passa(como_a_11):
    chamadas: list = []
    cliente = _cliente("SBOALTAMIRAPROD", chamadas)

    async def roda():
        async with cliente as sl:
            await sl.patch("Orders(1)", {"Comments": "x"})

    asyncio.run(roda())
    assert ("PATCH", "/b1s/v1/Orders(1)") in chamadas


def test_aclose_faz_logout_so_quando_logou():
    """One leaked session per task would eat the Service Layer's cap, shared with the worker."""
    chamadas: list = []
    cliente = _cliente("SBOALTAMIRAHOMOLOG", chamadas)

    async def roda():
        async with cliente as sl:
            await sl.get("Orders(1)")

    asyncio.run(roda())
    assert chamadas[-1] == ("POST", "/b1s/v1/Logout")
    assert cliente._logged_in is False

    nunca_logou: list = []
    asyncio.run(_cliente("SBOALTAMIRAHOMOLOG", nunca_logou).aclose())
    assert nunca_logou == []


def test_logout_que_falha_nao_derruba_o_aexit():
    chamadas: list = []
    cliente = _cliente("SBOALTAMIRAHOMOLOG", chamadas, logout_falha=True)

    async def roda():
        async with cliente as sl:
            await sl.get("Orders(1)")

    asyncio.run(roda())  # no exception: the session expires on the SAP side
    assert chamadas[-1] == ("POST", "/b1s/v1/Logout")
    assert cliente._logged_in is False
