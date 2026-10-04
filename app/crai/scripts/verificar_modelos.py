"""
crai/scripts/verificar_modelos.py — Confere os modelos instalados contra o manifesto.

    python -m crai.scripts.verificar_modelos                       (de dentro de `app/`)
    python -m crai.scripts.verificar_modelos --zip caminho\\crai-modelos.zip

Lê `docs/modelos/MANIFESTO_MODELOS.json` (versionado) e compara, arquivo por arquivo, o que
está em `app/models/`: existe? tem o tamanho certo? tem o sha256 certo? Diz, em português,
o que falta e o que está diferente. **Sai com código 0 só se tudo bater.**

Com `--zip`, confere ANTES o sha256 do arquivo baixado contra o do manifesto — é o que se
faz depois de um download e antes de extrair. Os `.joblib` e `.pkl` executam código ao
serem carregados: um pacote com sha256 diferente não deve ser instalado.

Este script só lê e calcula sha256. NÃO carrega nenhum modelo (não importa joblib nem
torch): conferir um arquivo suspeito não pode executá-lo.

Arquivos de `app/models/` que não estão no manifesto (o estado do bandit, o candidato v2,
as pastas de experimento) são ignorados: não fazem parte do pacote.

`--modelos` e `--manifesto` existem para os testes (tmp_path).
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
RAIZ = APP.parent
MODELOS_PADRAO = APP / "models"
MANIFESTO_PADRAO = RAIZ / "docs" / "modelos" / "MANIFESTO_MODELOS.json"

OK, FALTA, TAMANHO, CONTEUDO = "ok", "falta", "tamanho", "conteudo"


def sha256_do_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_manifesto(caminho: Path) -> dict:
    with open(caminho, encoding="utf-8") as f:
        manifesto = json.load(f)
    if not isinstance(manifesto.get("arquivos"), list) or not manifesto["arquivos"]:
        raise ValueError("manifesto sem a lista `arquivos`")
    for a in manifesto["arquivos"]:
        nome = a.get("nome")
        if (not isinstance(nome, str) or not nome or "/" in nome or "\\" in nome
                or nome.startswith(".")):
            raise ValueError(f"manifesto com nome de arquivo inválido: {nome!r}")
        if not isinstance(a.get("bytes"), int) or not isinstance(a.get("sha256"), str):
            raise ValueError(f"manifesto sem tamanho ou sha256 para {nome}")
    return manifesto


def conferir(modelos: Path, manifesto: dict) -> list:
    """`[(nome, situacao, detalhe)]`, na ordem do manifesto."""
    resultado = []
    for a in manifesto["arquivos"]:
        caminho = modelos / a["nome"]
        if not caminho.is_file():
            resultado.append((a["nome"], FALTA, "não está na pasta"))
            continue
        tamanho = caminho.stat().st_size
        if tamanho != a["bytes"]:
            resultado.append((a["nome"], TAMANHO,
                              f"tem {tamanho} bytes; o manifesto diz {a['bytes']}"))
            continue
        sha = sha256_do_arquivo(caminho)
        if sha != a["sha256"]:
            resultado.append((a["nome"], CONTEUDO,
                              f"sha256 {sha[:16]}...; o manifesto diz {a['sha256'][:16]}..."))
            continue
        resultado.append((a["nome"], OK, ""))
    return resultado


def conferir_zip(caminho: Path, manifesto: dict) -> tuple:
    """(bate?, sha256 calculado, sha256 esperado)."""
    esperado = (manifesto.get("pacote") or {}).get("sha256")
    calculado = sha256_do_arquivo(caminho)
    return calculado == esperado, calculado, esperado


def verificar(modelos: Path, caminho_manifesto: Path, zip_baixado=None) -> int:
    try:
        manifesto = ler_manifesto(caminho_manifesto)
    except FileNotFoundError:
        print(f"[MODELOS] Manifesto não encontrado: {caminho_manifesto}")
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[MODELOS] Manifesto ilegível ({caminho_manifesto}): {e}")
        return 2

    if zip_baixado is not None:
        if not Path(zip_baixado).is_file():
            print(f"[MODELOS] Arquivo não encontrado: {zip_baixado}")
            return 2
        bate, calculado, esperado = conferir_zip(Path(zip_baixado), manifesto)
        if not bate:
            print(f"[MODELOS] O ZIP NÃO CONFERE. sha256 calculado: {calculado}")
            print(f"[MODELOS]                    sha256 esperado:  {esperado}")
            print("[MODELOS] NÃO extraia este arquivo: ele não é o pacote do manifesto. "
                  "Baixe de novo do endereço do README.")
            return 1
        print(f"[MODELOS] O zip confere com o manifesto (sha256 {calculado}).")

    resultado = conferir(modelos, manifesto)
    faltam = [r for r in resultado if r[1] == FALTA]
    diferentes = [r for r in resultado if r[1] in (TAMANHO, CONTEUDO)]
    certos = [r for r in resultado if r[1] == OK]

    print(f"[MODELOS] Pasta conferida: {modelos}")
    print(f"[MODELOS] Manifesto de {manifesto.get('gerado_em', 'data não declarada')}, "
          f"{len(resultado)} arquivos.")
    for nome, _, _ in faltam:
        print(f"[MODELOS]   FALTA      {nome}")
    for nome, _, detalhe in diferentes:
        print(f"[MODELOS]   DIFERENTE  {nome}: {detalhe}")

    if not faltam and not diferentes:
        print(f"[MODELOS] Tudo certo: os {len(certos)} arquivos batem com o manifesto.")
        return 0
    print(f"[MODELOS] {len(certos)} certo(s), {len(faltam)} faltando, "
          f"{len(diferentes)} diferente(s).")
    if faltam:
        print("[MODELOS] Sem os arquivos que faltam, o módulo correspondente responde por "
              "heurística. Baixe o pacote (README, seção \"Modelos prontos\") e extraia em "
              "app\\models.")
    if diferentes:
        print("[MODELOS] Arquivo diferente pode ser um modelo retreinado nesta máquina (é "
              "esperado depois de um treino) ou um download corrompido ou adulterado. Se "
              "você não treinou, baixe o pacote de novo e confira o sha256 do zip.")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Confere app/models contra o manifesto.")
    ap.add_argument("--modelos", default=str(MODELOS_PADRAO))
    ap.add_argument("--manifesto", default=str(MANIFESTO_PADRAO))
    ap.add_argument("--zip", default=None, help="confere também o sha256 de um zip baixado")
    args = ap.parse_args(argv)
    return verificar(Path(args.modelos), Path(args.manifesto), args.zip)


if __name__ == "__main__":
    sys.exit(main())
