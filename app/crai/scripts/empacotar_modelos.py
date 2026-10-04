"""
crai/scripts/empacotar_modelos.py — Empacota os modelos de produção num .zip para download.

    python -m crai.scripts.empacotar_modelos          (de dentro de `app/`)

POR QUE EXISTE. `app/models/` não é versionada: num clone limpo o serviço sobe sem
modelo nenhum e responde por heurística. O pacote é o que o README manda baixar.

O QUE FAZ.
  1. Confere que todos os arquivos de `ARQUIVOS_DO_PACOTE` existem em `app/models/` e que o
     risco voluntário em produção é o v3 promovido (meta com `contrato: "v3"`).
  2. Procura dado pessoal evidente nos arquivos de texto do pacote (e-mail, CPF, telefone,
     caminho de pasta de usuário) e RECUSA empacotar se achar.
  3. Grava `docs/interno/release/crai-modelos.zip` com os arquivos na RAIZ do zip, para
     `Expand-Archive ... -DestinationPath app\\models` deixá-los no lugar. O zip é
     reprodutível: mesma ordem, mesma data nos cabeçalhos, mesma compressão — os mesmos
     arquivos dão sempre o mesmo sha256.
  4. Grava `docs/modelos/MANIFESTO_MODELOS.json` (versionado): nome, tamanho e sha256 de
     cada arquivo, o sha256 do zip, a data e de qual treino veio cada modelo.
  5. Imprime o sha256 do zip.

O QUE ENTRA: só o que o sistema CARREGA para funcionar, mais a evidência de treino que
acompanha cada modelo. A lista saiu da leitura dos carregadores (`FailureClassifier.load`,
`AnomalyDetector.load`, `PaydayInference.load`, `risk_scorer.carregar_modelo`), não de um
`ls` da pasta.

O QUE FICA DE FORA, de propósito: `bandit_state.json` e os `.bak` (estado operacional que o
serviço aprende, não modelo); `v2/`, `v3/`, `v3_exp_*/` e o candidato v2 (histórico e
experimentos); `calibracao.json` (já é versionado no repositório); qualquer coisa de
`vault/` ou `data/`. A lista do pacote é FECHADA: arquivo novo na pasta não entra sozinho.

O ZIP NUNCA VAI PARA O GIT: `docs/interno/` é ignorada. Quem publica a release é uma pessoa,
pelo site do GitHub. Este script não usa rede.

`--modelos`, `--saida` e `--manifesto` existem para os testes (tmp_path).
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
RAIZ = APP.parent
MODELOS_PADRAO = APP / "models"
SAIDA_PADRAO = RAIZ / "docs" / "interno" / "release" / "crai-modelos.zip"
MANIFESTO_PADRAO = RAIZ / "docs" / "modelos" / "MANIFESTO_MODELOS.json"

NOME_DO_ZIP = "crai-modelos.zip"
VERSAO_DO_MANIFESTO = 1

CARREGADO = "carregado"      # o serviço lê este arquivo para funcionar
EVIDENCIA = "evidencia"      # métricas e curvas do treino que produziu o modelo

# (arquivo, modelo, papel, quem lê). A ORDEM é a do zip.
ARQUIVOS_DO_PACOTE = (
    ("xgb_failure_classifier.joblib", "classificador_de_falha", CARREGADO, "FailureClassifier.load"),
    ("rf_failure_classifier.joblib", "classificador_de_falha", CARREGADO, "FailureClassifier.load"),
    ("label_encoders.joblib", "classificador_de_falha", CARREGADO, "FailureClassifier.load"),
    ("feature_names.joblib", "classificador_de_falha", CARREGADO, "FailureClassifier.load"),
    ("failure_classifier_meta.json", "classificador_de_falha", CARREGADO, "FailureClassifier.load"),
    ("train_metrics.json", "classificador_de_falha", EVIDENCIA, "métricas do treino"),
    ("curva_limiar_classificador.json", "classificador_de_falha", EVIDENCIA, "curva do limiar"),
    ("autoencoder.pt", "detector_de_anomalia", CARREGADO, "AnomalyDetector.load"),
    ("autoencoder_scaler.pkl", "detector_de_anomalia", CARREGADO, "AnomalyDetector.load"),
    ("autoencoder_meta.json", "detector_de_anomalia", CARREGADO, "AnomalyDetector.load"),
    ("curva_limiar_anomalia.json", "detector_de_anomalia", EVIDENCIA, "curva do limiar"),
    ("payday_lstm.pt", "inferencia_de_liquidez", CARREGADO, "PaydayInference.load"),
    ("payday_meta.json", "inferencia_de_liquidez", CARREGADO, "PaydayInference.load"),
    ("payday_prophet_CLT.json", "inferencia_de_liquidez", CARREGADO, "PaydayInference.load"),
    ("payday_prophet_PJ.json", "inferencia_de_liquidez", CARREGADO, "PaydayInference.load"),
    ("payday_prophet_freelancer.json", "inferencia_de_liquidez", CARREGADO, "PaydayInference.load"),
    ("voluntary_risk.joblib", "risco_voluntario_v3", CARREGADO, "risk_scorer.carregar_modelo"),
    ("voluntary_risk_meta.json", "risco_voluntario_v3", CARREGADO, "risk_scorer.carregar_modelo"),
)

# O meta de cada modelo: de onde sai "de qual treino veio".
META_DO_MODELO = {
    "classificador_de_falha": "failure_classifier_meta.json",
    "detector_de_anomalia": "autoencoder_meta.json",
    "inferencia_de_liquidez": "payday_meta.json",
    "risco_voluntario_v3": "voluntary_risk_meta.json",
}

# Nunca entram, mesmo que alguém os acrescente à lista por engano.
PROIBIDOS = ("bandit_state", ".bak", "candidato", "calibracao.json", "pix_keys")

# Data fixa dos cabeçalhos do zip: o sha256 do zip depende só do conteúdo.
DATA_FIXA_DO_ZIP = (2026, 1, 1, 0, 0, 0)

_SUSPEITOS = (
    ("e-mail", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CPF", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")),
    ("telefone", re.compile(r"\+55\s?\(?\d{2}\)?\s?9?\d{4}-?\d{4}")),
    ("pasta de usuário", re.compile(r"[\\/]Users[\\/]|[\\/]home[\\/]", re.IGNORECASE)),
)


def sha256_do_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def nomes_do_pacote() -> list:
    return [nome for nome, _, _, _ in ARQUIVOS_DO_PACOTE]


def conferir_lista() -> None:
    """A lista é fechada e não pode conter o que fica de fora."""
    nomes = nomes_do_pacote()
    if len(set(nomes)) != len(nomes):
        raise SystemExit("[PACOTE] Lista com arquivo repetido.")
    for nome in nomes:
        if "/" in nome or "\\" in nome or any(p in nome for p in PROIBIDOS):
            raise SystemExit(f"[PACOTE] {nome} não pode entrar no pacote "
                             "(estado operacional, histórico ou subpasta).")


def faltando(modelos: Path) -> list:
    return [nome for nome in nomes_do_pacote() if not (modelos / nome).is_file()]


def dado_pessoal_evidente(modelos: Path) -> list:
    """`[(arquivo, tipo)]` para cada padrão de dado pessoal achado nos arquivos de TEXTO do
    pacote (.json). Os binários (.joblib, .pt, .pkl) não são texto: o que os mantém sem
    dado pessoal é o treino, que só usa dado sintético — declarado no meta de cada um."""
    achados = []
    for nome in nomes_do_pacote():
        if not nome.endswith(".json"):
            continue
        texto = (modelos / nome).read_text(encoding="utf-8", errors="replace")
        for tipo, padrao in _SUSPEITOS:
            if padrao.search(texto):
                achados.append((nome, tipo))
    return achados


def treino_do_modelo(modelos: Path, modelo: str) -> dict:
    """De qual treino veio o modelo, lido do meta dele. Só campos de proveniência."""
    with open(modelos / META_DO_MODELO[modelo], encoding="utf-8") as f:
        meta = json.load(f)
    proveniencia = meta.get("proveniencia") if isinstance(meta.get("proveniencia"), dict) else {}
    treino = {
        "meta": META_DO_MODELO[modelo],
        "algoritmo": meta.get("algoritmo"),
        "treinado_em": meta.get("treinado_em"),
        "fonte": meta.get("fonte_usada") or proveniencia.get("fonte"),
        "n_amostras": meta.get("n_amostras"),
        "seed": meta.get("seed"),
    }
    if modelo == "risco_voluntario_v3":
        origem = meta.get("origem") if isinstance(meta.get("origem"), dict) else {}
        treino.update({
            "fonte": "base v3 sintética (rótulo latente desenhado pelo projeto; não é churn observado)",
            "contrato": meta.get("contrato"),
            "promovido_em": meta.get("promovido_em"),
            "base_sha256": proveniencia.get("base_sha256"),
            "base_hash_canonico": proveniencia.get("base_hash_canonico"),
            "joblib_de_origem_sha256": origem.get("joblib_sha256"),
        })
    return {k: v for k, v in treino.items() if v is not None}


def gravar_zip(modelos: Path, saida: Path) -> None:
    saida.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(saida, "w") as z:
        for nome in nomes_do_pacote():
            info = zipfile.ZipInfo(nome, date_time=DATA_FIXA_DO_ZIP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3          # o mesmo cabeçalho em qualquer sistema
            z.writestr(info, (modelos / nome).read_bytes(), compresslevel=6)


def montar_manifesto(modelos: Path, zip_sha256: str, zip_bytes: int, gerado_em: str) -> dict:
    arquivos = [{"nome": nome, "modelo": modelo, "papel": papel, "lido_por": quem,
                 "bytes": (modelos / nome).stat().st_size,
                 "sha256": sha256_do_arquivo(modelos / nome)}
                for nome, modelo, papel, quem in ARQUIVOS_DO_PACOTE]
    return {
        "versao_do_manifesto": VERSAO_DO_MANIFESTO,
        "gerado_em": gerado_em,
        "pacote": {"nome": NOME_DO_ZIP, "bytes": zip_bytes, "sha256": zip_sha256,
                   "instalar_em": "app/models/"},
        "modelos": {modelo: treino_do_modelo(modelos, modelo) for modelo in META_DO_MODELO},
        "arquivos": arquivos,
        "fora_do_pacote": [
            "bandit_state.json e *.bak (estado operacional, não modelo)",
            "voluntary_risk_candidato.* (candidato v2, não promovido)",
            "v2/, v3/, v3_exp_*/ (histórico e experimentos)",
            "calibracao.json (já versionado no repositório)",
            "vault/ e data/",
        ],
        "aviso": "Arquivos .joblib e .pkl executam código ao serem carregados. Baixe só do "
                 "endereço do README e confira o sha256 antes de instalar.",
    }


def empacotar(modelos: Path, saida: Path, manifesto: Path) -> int:
    conferir_lista()
    ausentes = faltando(modelos)
    if ausentes:
        print(f"[PACOTE] RECUSADO: faltam {len(ausentes)} arquivo(s) em {modelos}: "
              f"{', '.join(ausentes)}. Treine ou promova os modelos antes de empacotar.")
        return 2
    with open(modelos / "voluntary_risk_meta.json", encoding="utf-8") as f:
        contrato = json.load(f).get("contrato")
    if contrato != "v3":
        print(f"[PACOTE] RECUSADO: o risco voluntário em {modelos} não é o v3 promovido "
              f"(contrato={contrato!r}). Rode `python -m crai.scripts.promover_voluntario_v3 "
              "--promover` antes.")
        return 2
    achados = dado_pessoal_evidente(modelos)
    if achados:
        print("[PACOTE] RECUSADO: padrão de dado pessoal em arquivo do pacote: "
              + "; ".join(f"{nome} ({tipo})" for nome, tipo in achados)
              + ". Nada foi gravado.")
        return 2

    gravar_zip(modelos, saida)
    zip_sha256 = sha256_do_arquivo(saida)
    dados = montar_manifesto(modelos, zip_sha256, saida.stat().st_size,
                             datetime.now().isoformat(timespec="seconds"))
    manifesto.parent.mkdir(parents=True, exist_ok=True)
    with open(manifesto, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")

    total = sum(a["bytes"] for a in dados["arquivos"])
    print(f"[PACOTE] {len(dados['arquivos'])} arquivos, {total} bytes, de {modelos}")
    print(f"[PACOTE] zip: {saida} ({saida.stat().st_size} bytes)")
    print(f"[PACOTE] sha256 do zip: {zip_sha256}")
    print(f"[PACOTE] manifesto: {manifesto}")
    print("[PACOTE] O zip NÃO vai para o git. Publique-o na release pelo site do GitHub.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Empacota os modelos de produção num zip.")
    ap.add_argument("--modelos", default=str(MODELOS_PADRAO))
    ap.add_argument("--saida", default=str(SAIDA_PADRAO))
    ap.add_argument("--manifesto", default=str(MANIFESTO_PADRAO))
    args = ap.parse_args(argv)
    return empacotar(Path(args.modelos), Path(args.saida), Path(args.manifesto))


if __name__ == "__main__":
    sys.exit(main())
