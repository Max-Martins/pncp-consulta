"""Cliente HTTP para a API pública de consulta do PNCP.

Documentação oficial: https://pncp.gov.br/api/consulta/swagger-ui/index.html
Usa apenas a biblioteca padrão do Python.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterator

BASE_CONSULTA = "https://pncp.gov.br/api/consulta/v1"
BASE_PNCP = "https://pncp.gov.br/api/pncp/v1"
TAMANHO_PAGINA_MAX = 50  # limite imposto pela API

Transport = Callable[[str, float], Any]


class PNCPError(RuntimeError):
    """Falha ao consultar a API do PNCP depois de esgotar as tentativas."""


@dataclass(frozen=True)
class IdCompra:
    """Identifica uma contratação no PNCP: CNPJ do órgão, ano e sequencial."""

    cnpj: str
    ano: int
    sequencial: int

    @classmethod
    def de_numero_controle(cls, numero: str) -> "IdCompra":
        """Converte o número de controle do PNCP (ex.: 45132495000140-1-000942/2024)."""
        try:
            cnpj, _, resto = numero.split("-", 2)
            seq, ano = resto.split("/")
            return cls(cnpj=cnpj, ano=int(ano), sequencial=int(seq))
        except ValueError as exc:
            raise ValueError(f"Número de controle PNCP inválido: {numero!r}") from exc

    @classmethod
    def de_contratacao(cls, contratacao: dict) -> "IdCompra":
        return cls(
            cnpj=contratacao["orgaoEntidade"]["cnpj"],
            ano=int(contratacao["anoCompra"]),
            sequencial=int(contratacao["sequencialCompra"]),
        )


def _formatar_data(valor: date | datetime | str) -> str:
    """Aceita date, datetime, 'AAAA-MM-DD' ou 'AAAAMMDD' e devolve 'AAAAMMDD'."""
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%Y%m%d")
    texto = valor.replace("-", "")
    datetime.strptime(texto, "%Y%m%d")  # valida
    return texto


def _transporte_urllib(url: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pncp-consulta/0.1"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status == 204:
            return None
        corpo = resp.read()
        return json.loads(corpo) if corpo else None


class PNCPClient:
    """Cliente com novas tentativas, espera progressiva e paginação automática."""

    def __init__(
        self,
        timeout: float = 30.0,
        tentativas: int = 4,
        espera_inicial: float = 2.0,
        transporte: Transport | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.tentativas = tentativas
        self.espera_inicial = espera_inicial
        self._transporte = transporte or _transporte_urllib
        self._dormir = dormir

    # ------------------------------------------------------------------ HTTP

    def _get(self, base: str, caminho: str, params: dict | None = None) -> Any:
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{base}{caminho}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        ultimo_erro: Exception | None = None
        for tentativa in range(self.tentativas):
            try:
                return self._transporte(url, self.timeout)
            except urllib.error.HTTPError as exc:
                # Erros 4xx (exceto 429) indicam parâmetro inválido: não adianta repetir.
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise PNCPError(f"HTTP {exc.code} em {url}") from exc
                ultimo_erro = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                ultimo_erro = exc
            if tentativa < self.tentativas - 1:
                self._dormir(self.espera_inicial * (2**tentativa))
        raise PNCPError(f"Falha após {self.tentativas} tentativas em {url}: {ultimo_erro}")

    def _paginar(
        self, caminho: str, params: dict, limite: int | None
    ) -> Iterator[dict]:
        pagina, entregues = 1, 0
        while True:
            dados = self._get(
                BASE_CONSULTA,
                caminho,
                {**params, "pagina": pagina, "tamanhoPagina": TAMANHO_PAGINA_MAX},
            )
            if not dados or dados.get("empty") or not dados.get("data"):
                return
            for registro in dados["data"]:
                yield registro
                entregues += 1
                if limite is not None and entregues >= limite:
                    return
            if not dados.get("paginasRestantes"):
                return
            pagina += 1

    # ------------------------------------------------------------ Consultas

    def contratacoes_com_proposta_aberta(
        self,
        data_final: date | datetime | str,
        modalidade: int | None = None,
        uf: str | None = None,
        codigo_municipio_ibge: str | None = None,
        cnpj_orgao: str | None = None,
        limite: int | None = None,
    ) -> Iterator[dict]:
        """Contratações com recebimento de propostas aberto até `data_final`."""
        params = {
            "dataFinal": _formatar_data(data_final),
            "codigoModalidadeContratacao": modalidade,
            "uf": uf.upper() if uf else None,
            "codigoMunicipioIbge": codigo_municipio_ibge,
            "cnpj": cnpj_orgao,
        }
        return self._paginar("/contratacoes/proposta", params, limite)

    def contratacoes_publicadas(
        self,
        data_inicial: date | datetime | str,
        data_final: date | datetime | str,
        modalidade: int,
        uf: str | None = None,
        codigo_municipio_ibge: str | None = None,
        cnpj_orgao: str | None = None,
        limite: int | None = None,
    ) -> Iterator[dict]:
        """Contratações publicadas no PNCP entre duas datas (modalidade obrigatória)."""
        params = {
            "dataInicial": _formatar_data(data_inicial),
            "dataFinal": _formatar_data(data_final),
            "codigoModalidadeContratacao": modalidade,
            "uf": uf.upper() if uf else None,
            "codigoMunicipioIbge": codigo_municipio_ibge,
            "cnpj": cnpj_orgao,
        }
        return self._paginar("/contratacoes/publicacao", params, limite)

    def compra(self, id_compra: IdCompra) -> dict:
        """Detalhe de uma contratação."""
        return self._get(
            BASE_CONSULTA,
            f"/orgaos/{id_compra.cnpj}/compras/{id_compra.ano}/{id_compra.sequencial}",
        )

    def itens(self, id_compra: IdCompra) -> list[dict]:
        """Itens de uma contratação (todas as páginas)."""
        caminho = f"/orgaos/{id_compra.cnpj}/compras/{id_compra.ano}/{id_compra.sequencial}/itens"
        todos: list[dict] = []
        pagina = 1
        while True:
            lote = self._get(
                BASE_PNCP, caminho, {"pagina": pagina, "tamanhoPagina": TAMANHO_PAGINA_MAX}
            ) or []
            todos.extend(lote)
            if len(lote) < TAMANHO_PAGINA_MAX:
                return todos
            pagina += 1

    def arquivos(self, id_compra: IdCompra) -> list[dict]:
        """Documentos publicados (edital, termo de referência, anexos...)."""
        caminho = f"/orgaos/{id_compra.cnpj}/compras/{id_compra.ano}/{id_compra.sequencial}/arquivos"
        return self._get(BASE_PNCP, caminho) or []
