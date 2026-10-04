"""Linha de comando: python -m pncp_consulta --help"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from datetime import date, timedelta
from typing import Iterable, Iterator

from .client import IdCompra, PNCPClient, PNCPError
from .modalidades import MODALIDADES, UFS

COLUNAS = [
    "numero_controle",
    "orgao",
    "municipio",
    "uf",
    "modalidade",
    "objeto",
    "valor_estimado",
    "abertura_propostas",
    "encerramento_propostas",
    "link",
]


def _sem_acento(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in normal if not unicodedata.combining(c)).lower()


def resumir(c: dict) -> dict:
    """Achata uma contratação da API em uma linha legível."""
    unidade = c.get("unidadeOrgao") or {}
    id_compra = IdCompra.de_contratacao(c)
    return {
        "numero_controle": c.get("numeroControlePNCP"),
        "orgao": (c.get("orgaoEntidade") or {}).get("razaoSocial"),
        "municipio": unidade.get("municipioNome"),
        "uf": unidade.get("ufSigla"),
        "modalidade": c.get("modalidadeNome"),
        "objeto": " ".join((c.get("objetoCompra") or "").split()),
        "valor_estimado": c.get("valorTotalEstimado"),
        "abertura_propostas": c.get("dataAberturaProposta"),
        "encerramento_propostas": c.get("dataEncerramentoProposta"),
        "link": f"https://pncp.gov.br/app/editais/{id_compra.cnpj}/{id_compra.ano}/{id_compra.sequencial}",
    }


def filtrar_por_termos(linhas: Iterable[dict], termos: list[str]) -> Iterator[dict]:
    """Mantém linhas cujo objeto contém qualquer um dos termos (sem diferenciar acentos)."""
    alvo = [_sem_acento(t) for t in termos]
    for linha in linhas:
        objeto = _sem_acento(linha["objeto"])
        if any(t in objeto for t in alvo):
            yield linha


def formatar_reais(valor: float | None) -> str:
    """1234.5 -> '1.234,50' (padrão brasileiro)."""
    if valor is None:
        return ""
    return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _escrever(linhas: list[dict], formato: str, destino) -> None:
    if formato == "json":
        json.dump(linhas, destino, ensure_ascii=False, indent=2)
        destino.write("\n")
    elif formato == "csv":
        # ';' e vírgula decimal: abre direto no Excel em português.
        escritor = csv.DictWriter(destino, fieldnames=COLUNAS, delimiter=";")
        escritor.writeheader()
        for l in linhas:
            escritor.writerow({**l, "valor_estimado": formatar_reais(l["valor_estimado"])})
    else:
        for l in linhas:
            valor = f"R$ {formatar_reais(l['valor_estimado'])}" if l["valor_estimado"] else "sigiloso/não informado"
            destino.write(
                f"{l['uf']} | {l['municipio']} | {l['orgao']}\n"
                f"  {l['objeto'][:110]}\n"
                f"  {valor} · encerra {(l['encerramento_propostas'] or '')[:16]} · {l['link']}\n\n"
            )
        destino.write(f"{len(linhas)} contratação(ões).\n")


def _data(texto: str) -> date:
    return date.fromisoformat(texto)


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pncp-consulta",
        description="Consulta contratações públicas na API aberta do PNCP.",
    )
    sub = p.add_subparsers(dest="comando", required=True)

    def saida(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--busca", nargs="+", metavar="TERMO", help="filtra o objeto por termos")
        sp.add_argument("--limite", type=int, help="máximo de registros consultados")
        sp.add_argument("--formato", choices=["tabela", "csv", "json"], default="tabela")
        sp.add_argument("-o", "--saida", help="arquivo de saída (padrão: tela)")

    ab = sub.add_parser("abertas", help="contratações com propostas abertas")
    ab.add_argument("--ate", type=_data, default=date.today() + timedelta(days=30),
                    help="data final AAAA-MM-DD (padrão: hoje + 30 dias)")
    ab.add_argument("--modalidade", type=int, default=6, choices=MODALIDADES,
                    help="código da modalidade (padrão: 6, pregão eletrônico)")
    ab.add_argument("--uf", choices=UFS, type=str.upper)
    saida(ab)

    pub = sub.add_parser("publicadas", help="contratações publicadas em um período")
    pub.add_argument("--de", type=_data, required=True, help="data inicial AAAA-MM-DD")
    pub.add_argument("--ate", type=_data, required=True, help="data final AAAA-MM-DD")
    pub.add_argument("--modalidade", type=int, required=True, choices=MODALIDADES)
    pub.add_argument("--uf", choices=UFS, type=str.upper)
    saida(pub)

    det = sub.add_parser("detalhe", help="detalhe, itens e documentos de uma contratação")
    det.add_argument("numero_controle", help="ex.: 45132495000140-1-000942/2024")

    sub.add_parser("modalidades", help="lista os códigos de modalidade")
    return p


def main(argv: list[str] | None = None) -> int:
    args = montar_parser().parse_args(argv)
    cliente = PNCPClient()

    if args.comando == "modalidades":
        for codigo, nome in MODALIDADES.items():
            print(f"{codigo:>2}  {nome}")
        return 0

    try:
        if args.comando == "detalhe":
            id_compra = IdCompra.de_numero_controle(args.numero_controle)
            resultado = {
                "compra": cliente.compra(id_compra),
                "itens": cliente.itens(id_compra),
                "arquivos": cliente.arquivos(id_compra),
            }
            json.dump(resultado, sys.stdout, ensure_ascii=False, indent=2)
            print()
            return 0

        if args.comando == "abertas":
            brutos = cliente.contratacoes_com_proposta_aberta(
                args.ate, modalidade=args.modalidade, uf=args.uf, limite=args.limite)
        else:
            brutos = cliente.contratacoes_publicadas(
                args.de, args.ate, modalidade=args.modalidade, uf=args.uf, limite=args.limite)

        linhas = (resumir(c) for c in brutos)
        if args.busca:
            linhas = filtrar_por_termos(linhas, args.busca)
        linhas = list(linhas)
    except PNCPError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    if args.saida:
        with open(args.saida, "w", encoding="utf-8-sig", newline="") as f:
            _escrever(linhas, args.formato, f)
        print(f"{len(linhas)} registro(s) gravado(s) em {args.saida}")
    else:
        _escrever(linhas, args.formato, sys.stdout)
    return 0
