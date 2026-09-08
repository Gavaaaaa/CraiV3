"""crai/churn_voluntary/importacao.py — da planilha da empresa à tabela `clientes_importados`.

O arquivo chega como bytes (CSV ou XLSX), com as colunas que a empresa usa —
que raramente são as que o motor de risco espera. Este módulo faz três coisas,
nesta ordem, e devolve um relatório do que aconteceu com cada linha:

    1. RESOLVE AS COLUNAS: pelo `mapeamento` explícito, senão por nome exato
       (case-insensitive, sem espaços nas pontas). O que não achou vai para
       `colunas_nao_encontradas` — obrigatória ausente impede a importação
       inteira; opcional ausente só é reportada.
    2. VALIDA LINHA A LINHA: `customer_id_externo`, `mrr` e `billing_profile`
       são obrigatórios; `days_since_last`, `features_used_30d` e `email` são
       opcionais. Uma linha torta entra em `rejeitados` com o número da linha
       DA PLANILHA (cabeçalho = 1) e o motivo, e NÃO derruba o resto do lote.
    3. GRAVA o que sobreviveu, numa transação, via `clientes_importados.gravar`.

NÚMERO EM TEXTO É pt-BR. Tudo é lido como texto (`dtype=str`) para CSV e
XLSX se comportarem igual, e `_numero` decide: "1.234,56" é mil duzentos e
trinta e quatro; "1,5" é um e meio; "1500.00" é mil e quinhentos. É a regra
oposta à do `mrr_utilizavel` do risk_scorer, que RECUSA texto — e as duas
estão certas no seu lugar: o Segment não declara locale e o erro lá é
silencioso, enquanto aqui o arquivo vem de uma empresa brasileira e cada
número recusado aparece nominalmente em `rejeitados`. "R$" e espaços são
ignorados.

`linhas_sem_dado_comportamental` conta as linhas IMPORTADAS sem
`days_since_last` E sem `features_used_30d`. Elas entram — cadastro é
cadastro — mas o Sprint 3 não vai inventar risco para elas, e é aqui, na
resposta da importação, que a empresa fica sabendo disso pela primeira vez.
"""

import io
import json
import logging
import math
import re

import pandas as pd

from .offer_bandit import PROFILES
from . import clientes_importados

logger = logging.getLogger(__name__)

OBRIGATORIAS = ("customer_id_externo", "mrr", "billing_profile")
OPCIONAIS = ("days_since_last", "features_used_30d", "email")
ESPERADAS = OBRIGATORIAS + OPCIONAIS
COMPORTAMENTAIS = ("days_since_last", "features_used_30d")

EXTENSOES = {".csv", ".xlsx"}
TAMANHO_MAXIMO_BYTES = 10 * 1024 * 1024        # 10 MB — ordens acima de uma base PME
LINHAS_MAXIMAS = 50_000

_PERFIS_POR_NOME = {p.lower(): p for p in PROFILES}
_ID_VALIDO = re.compile(r"^[^\s]{1,128}$")
_EMAIL_PLAUSIVEL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ArquivoInvalido(ValueError):
    """O arquivo inteiro não serve: extensão, tamanho, encoding, vazio, ilegível.

    `motivo` é curto e estável (vira o `detail` da resposta); `mensagem` explica.
    """

    def __init__(self, motivo: str, mensagem: str, status: int = 422):
        super().__init__(mensagem)
        self.motivo = motivo
        self.mensagem = mensagem
        self.status = status


# ── Leitura do arquivo ───────────────────────────────────────────────────

def _extensao(nome: str) -> str:
    nome = (nome or "").strip().lower()
    return nome[nome.rfind("."):] if "." in nome else ""


def ler_tabela(nome_arquivo: str, conteudo: bytes) -> pd.DataFrame:
    """Bytes → DataFrame de TEXTO (toda célula é str, vazio é "")."""
    ext = _extensao(nome_arquivo)
    if ext not in EXTENSOES:
        raise ArquivoInvalido(
            "extensao_nao_suportada",
            f"arquivo {nome_arquivo!r}: só .csv e .xlsx são aceitos", status=415)
    if not conteudo:
        raise ArquivoInvalido("arquivo_vazio", "o arquivo enviado está vazio")
    if len(conteudo) > TAMANHO_MAXIMO_BYTES:
        raise ArquivoInvalido(
            "arquivo_grande_demais",
            f"arquivo com {len(conteudo)} bytes; o máximo é {TAMANHO_MAXIMO_BYTES}",
            status=413)

    try:
        if ext == ".csv":
            df = _ler_csv(conteudo)
        else:
            df = pd.read_excel(io.BytesIO(conteudo), dtype=str, engine="openpyxl")
    except ArquivoInvalido:
        raise
    except Exception as e:                        # noqa: BLE001 — parser de terceiro
        raise ArquivoInvalido("arquivo_ilegivel",
                              f"não foi possível ler o arquivo: {e}") from e

    df = df.fillna("")
    df.columns = [str(c) for c in df.columns]
    if len(df) == 0:
        raise ArquivoInvalido("sem_linhas", "o arquivo tem cabeçalho mas nenhuma linha")
    if len(df) > LINHAS_MAXIMAS:
        raise ArquivoInvalido(
            "linhas_demais",
            f"{len(df)} linhas; o máximo por importação é {LINHAS_MAXIMAS}", status=413)
    return df


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    """UTF-8 (com ou sem BOM), senão Latin-1 — os dois encodings que o Excel
    brasileiro exporta. Separador detectado (`;` é o comum em pt-BR)."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            texto = conteudo.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:                                          # pragma: no cover — latin-1 nunca falha
        raise ArquivoInvalido("encoding_desconhecido", "não foi possível decodificar o CSV")
    if not texto.strip():
        raise ArquivoInvalido("arquivo_vazio", "o CSV está vazio")
    return pd.read_csv(io.StringIO(texto), dtype=str, sep=None, engine="python",
                       keep_default_na=False)


# ── Colunas ──────────────────────────────────────────────────────────────

def interpretar_mapeamento(bruto) -> dict:
    """`mapeamento` do multipart (JSON em string) → {coluna_no_arquivo: campo}.

    Ausente/vazio → {}. Torto → `ArquivoInvalido`, porque um mapeamento que não
    foi aplicado deixaria a empresa achando que mapeou.
    """
    if bruto is None or (isinstance(bruto, str) and not bruto.strip()):
        return {}
    if isinstance(bruto, dict):
        obj = bruto
    else:
        try:
            obj = json.loads(bruto)
        except (TypeError, ValueError) as e:
            raise ArquivoInvalido("mapeamento_invalido",
                                  f"`mapeamento` não é JSON válido: {e}") from e
    if not isinstance(obj, dict):
        raise ArquivoInvalido("mapeamento_invalido",
                              "`mapeamento` deve ser um objeto {coluna_no_arquivo: campo}")
    resultado = {}
    for coluna, campo in obj.items():
        if not isinstance(coluna, str) or not isinstance(campo, str):
            raise ArquivoInvalido("mapeamento_invalido",
                                  "`mapeamento`: chaves e valores devem ser texto")
        campo = campo.strip().lower()
        if campo not in ESPERADAS:
            raise ArquivoInvalido(
                "mapeamento_invalido",
                f"`mapeamento`: {campo!r} não é um campo esperado; "
                f"os campos são {', '.join(ESPERADAS)}")
        resultado[coluna.strip()] = campo
    return resultado


def resolver_colunas(colunas_do_arquivo: list, mapeamento: dict) -> tuple[dict, list]:
    """({campo: coluna_no_arquivo}, [campos esperados não encontrados]).

    O mapeamento explícito vence; para o resto, nome exato sem distinguir
    caixa nem espaços nas pontas. Um campo mapeado para uma coluna que não
    existe no arquivo conta como não encontrado.
    """
    por_nome = {}
    for c in colunas_do_arquivo:
        por_nome.setdefault(c.strip().lower(), c)
    por_nome_exato = {c.strip(): c for c in colunas_do_arquivo}

    encontradas = {}
    for coluna, campo in mapeamento.items():
        real = por_nome_exato.get(coluna) or por_nome.get(coluna.lower())
        if real is not None:
            encontradas[campo] = real
    for campo in ESPERADAS:
        if campo in encontradas:
            continue
        real = por_nome.get(campo)
        if real is not None:
            encontradas[campo] = real

    faltando = [campo for campo in ESPERADAS if campo not in encontradas]
    return encontradas, faltando


# ── Valores ──────────────────────────────────────────────────────────────

def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and math.isnan(valor):
        return ""
    return str(valor).strip()


def _numero(bruto: str):
    """Texto pt-BR → float ≥ 0, ou None se vazio. Levanta ValueError se torto."""
    s = _texto(bruto)
    if not s:
        return None
    s = s.replace("R$", "").replace("r$", "").replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")      # 1.234,56
    elif "," in s:
        s = s.replace(",", ".")                        # 1,5
    valor = float(s)                                   # 1500.00 / 1500
    if not math.isfinite(valor):
        raise ValueError("não é finito")
    if valor < 0:
        raise ValueError("negativo")
    return valor


def _perfil(bruto: str):
    s = _texto(bruto)
    return _PERFIS_POR_NOME.get(s.lower()) if s else None


def validar_linha(valores: dict) -> tuple:
    """(cliente | None, motivo | None). `valores` é {campo: texto} já resolvido."""
    cid = _texto(valores.get("customer_id_externo"))
    if not cid:
        return None, "customer_id_externo vazio"
    if not _ID_VALIDO.match(cid):
        return None, "customer_id_externo com espaços ou mais de 128 caracteres"

    try:
        mrr = _numero(valores.get("mrr"))
    except ValueError:
        return None, f"mrr {_texto(valores.get('mrr'))!r} não é um número válido"
    if mrr is None:
        return None, "mrr vazio"

    perfil = _perfil(valores.get("billing_profile"))
    if perfil is None:
        return None, (f"billing_profile {_texto(valores.get('billing_profile'))!r} "
                      f"não é um dos perfis aceitos ({', '.join(PROFILES)})")

    cliente = {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil}
    for campo in COMPORTAMENTAIS:
        try:
            cliente[campo] = _numero(valores.get(campo))
        except ValueError:
            return None, f"{campo} {_texto(valores.get(campo))!r} não é um número válido"

    email = _texto(valores.get("email"))
    if email and not _EMAIL_PLAUSIVEL.match(email):
        return None, f"email {email!r} não parece um e-mail"
    cliente["email"] = email or None
    return cliente, None


# ── O fluxo inteiro ──────────────────────────────────────────────────────

def importar(tenant_id: str, nome_arquivo: str, conteudo: bytes,
             mapeamento=None) -> dict:
    """Arquivo → relatório. Levanta `ArquivoInvalido` só quando NADA dá para
    importar por culpa do arquivo inteiro; problema de linha vira `rejeitados`.

    Resposta:
        importados                     linhas gravadas (upsert)
        rejeitados                     [{linha, motivo}] — linha da PLANILHA
        colunas_nao_encontradas        campos esperados sem coluna no arquivo
        linhas_sem_dado_comportamental importadas sem days_since_last E sem
                                       features_used_30d (o Sprint 3 não
                                       calcula risco para elas)
        mensagem                       só quando nada foi importado, com o porquê
    """
    mapa = interpretar_mapeamento(mapeamento)
    df = ler_tabela(nome_arquivo, conteudo)
    colunas, faltando = resolver_colunas(list(df.columns), mapa)

    obrigatorias_ausentes = [c for c in OBRIGATORIAS if c in faltando]
    if obrigatorias_ausentes:
        return {
            "importados": 0, "rejeitados": [],
            "colunas_nao_encontradas": faltando,
            "linhas_sem_dado_comportamental": 0,
            "mensagem": (f"nada importado: falta(m) a(s) coluna(s) obrigatória(s) "
                         f"{', '.join(obrigatorias_ausentes)}. Colunas do arquivo: "
                         f"{', '.join(map(str, df.columns))}. Use `mapeamento` "
                         "para apontar qual coluna corresponde a cada campo."),
        }

    validos: dict = {}                     # customer_id → cliente (última linha vence)
    rejeitados = []
    # Linha 1 da planilha é o cabeçalho; a primeira linha de dado é a 2. É esse
    # o número que a pessoa vai procurar no Excel, não o índice do pandas.
    series_por_campo = {campo: df[col].tolist() for campo, col in colunas.items()}
    for posicao in range(len(df)):
        valores = {campo: serie[posicao] for campo, serie in series_por_campo.items()}
        cliente, motivo = validar_linha(valores)
        indice = posicao + 2
        if motivo:
            rejeitados.append({"linha": indice, "motivo": motivo})
            continue
        validos[cliente["customer_id_externo"]] = cliente

    clientes = list(validos.values())
    sem_comportamento = sum(
        1 for c in clientes
        if c.get("days_since_last") is None and c.get("features_used_30d") is None)

    importados = clientes_importados.gravar(tenant_id, clientes)
    logger.info("[IMPORTACAO] tenant=%s arquivo=%s importados=%d rejeitados=%d "
                "sem_comportamento=%d faltando=%s", tenant_id, nome_arquivo,
                importados, len(rejeitados), sem_comportamento, faltando)

    resposta = {
        "importados": importados,
        "rejeitados": rejeitados,
        "colunas_nao_encontradas": faltando,
        "linhas_sem_dado_comportamental": sem_comportamento,
    }
    if importados == 0:
        resposta["mensagem"] = "nada importado: todas as linhas foram rejeitadas"
    return resposta
