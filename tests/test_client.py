import io
import json
import unittest
import urllib.error
from datetime import date

from pncp_consulta import IdCompra, PNCPClient, PNCPError
from pncp_consulta.cli import _escrever, filtrar_por_termos, formatar_reais, resumir


def contratacao(seq: int, objeto: str = "Aquisição de papel A4") -> dict:
    return {
        "numeroControlePNCP": f"45132495000140-1-{seq:06d}/2026",
        "orgaoEntidade": {"cnpj": "45132495000140", "razaoSocial": "MUNICIPIO DE TESTE"},
        "unidadeOrgao": {"municipioNome": "Teste", "ufSigla": "SP"},
        "anoCompra": 2026,
        "sequencialCompra": seq,
        "modalidadeNome": "Pregão - Eletrônico",
        "objetoCompra": objeto,
        "valorTotalEstimado": 1234.5,
        "dataAberturaProposta": "2026-10-01T09:00:00",
        "dataEncerramentoProposta": "2026-10-20T09:00:00",
    }


class TransporteFalso:
    """Simula a API: devolve respostas em sequência e registra as URLs pedidas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def pagina(registros, restantes):
    return {"data": registros, "empty": not registros, "paginasRestantes": restantes}


class TestIdCompra(unittest.TestCase):
    def test_numero_controle(self):
        i = IdCompra.de_numero_controle("45132495000140-1-000942/2024")
        self.assertEqual(i, IdCompra("45132495000140", 2024, 942))

    def test_numero_controle_invalido(self):
        with self.assertRaises(ValueError):
            IdCompra.de_numero_controle("lixo")


class TestPaginacao(unittest.TestCase):
    def test_percorre_todas_as_paginas(self):
        t = TransporteFalso([pagina([contratacao(1), contratacao(2)], 1), pagina([contratacao(3)], 0)])
        c = PNCPClient(transporte=t)
        regs = list(c.contratacoes_com_proposta_aberta(date(2026, 10, 31), modalidade=6, uf="sp"))
        self.assertEqual([r["sequencialCompra"] for r in regs], [1, 2, 3])
        self.assertIn("dataFinal=20261031", t.urls[0])
        self.assertIn("uf=SP", t.urls[0])
        self.assertIn("pagina=2", t.urls[1])

    def test_respeita_limite(self):
        t = TransporteFalso([pagina([contratacao(1), contratacao(2)], 5)])
        c = PNCPClient(transporte=t)
        regs = list(c.contratacoes_com_proposta_aberta("2026-10-31", limite=1))
        self.assertEqual(len(regs), 1)
        self.assertEqual(len(t.urls), 1)

    def test_resposta_vazia(self):
        t = TransporteFalso([None])
        self.assertEqual(list(PNCPClient(transporte=t).contratacoes_com_proposta_aberta("20261031")), [])


class TestTentativas(unittest.TestCase):
    def test_repete_em_falha_temporaria(self):
        erro = urllib.error.HTTPError("u", 503, "indisponível", None, None)
        esperas = []
        t = TransporteFalso([erro, erro, pagina([contratacao(1)], 0)])
        c = PNCPClient(transporte=t, dormir=esperas.append, espera_inicial=1)
        self.assertEqual(len(list(c.contratacoes_com_proposta_aberta("20261031"))), 1)
        self.assertEqual(esperas, [1, 2])

    def test_nao_repete_erro_de_parametro(self):
        t = TransporteFalso([urllib.error.HTTPError("u", 400, "ruim", None, None)])
        with self.assertRaises(PNCPError):
            list(PNCPClient(transporte=t, dormir=lambda s: None).contratacoes_com_proposta_aberta("20261031"))
        self.assertEqual(len(t.urls), 1)

    def test_desiste_apos_tentativas(self):
        t = TransporteFalso([TimeoutError()] * 3)
        c = PNCPClient(transporte=t, tentativas=3, dormir=lambda s: None)
        with self.assertRaises(PNCPError):
            c.compra(IdCompra("1", 2026, 1))


class TestCLI(unittest.TestCase):
    def test_resumir_monta_link(self):
        linha = resumir(contratacao(942))
        self.assertEqual(linha["link"], "https://pncp.gov.br/app/editais/45132495000140/2026/942")

    def test_busca_ignora_acentos(self):
        linhas = [resumir(contratacao(1, "Aquisição de PAPEL")), resumir(contratacao(2, "Obra de pavimentação"))]
        achados = list(filtrar_por_termos(linhas, ["aquisicao"]))
        self.assertEqual([l["numero_controle"][-11:] for l in achados], ["000001/2026"])

    def test_formato_reais(self):
        self.assertEqual(formatar_reais(1234567.891), "1.234.567,89")
        self.assertEqual(formatar_reais(None), "")

    def test_saida_json(self):
        buf = io.StringIO()
        _escrever([resumir(contratacao(1))], "json", buf)
        self.assertEqual(json.loads(buf.getvalue())[0]["uf"], "SP")


if __name__ == "__main__":
    unittest.main()
