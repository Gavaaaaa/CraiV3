"""crai/churn_voluntary/offer_bandit.py — Módulo 4: Thompson Sampling (MAB).

Cada par (perfil, oferta) mantém um posterior Beta(α, β) sobre a taxa de
aceite. A cada decisão o bandit AMOSTRA uma taxa de cada posterior e escolhe
a oferta que maximiza o e-Profit com a taxa amostrada:

    escolha = argmax_o  p_amostrado(o) × LTV_retido − custo(o)

A incerteza faz a exploração sozinha: braços pouco testados têm posteriores
largos e às vezes amostram alto; braços ruins saem de cena. Não há epsilon
para calibrar. O aprendizado é contínuo: cada aceite/recusa real atualiza o
posterior e é persistido em crai/models/bandit_state.json.

Warm start: posteriores da simulação de 6.000 rodadas do modulo_04_offer_bandit/.
Cold start (sem arquivo): priors de benchmarks de mercado.

AUTONOMIA TOTAL: não existe braço de escalação humana. A CRAI decide e age
sozinha — risco crítico muda o TOM da mensagem (ver `is_critical_risk`), nunca
o destinatário da decisão.
"""

import json
from pathlib import Path

import numpy as np

# ── Diretório de persistência (mesmo padrão dos módulos 1-3) ─────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"
STATE_PATH = MODELS_DIR / "bandit_state.json"

OFFERS = ["desconto_10", "desconto_20", "pausa_1_mes", "pix_boleto_flash"]
PROFILES = ["CLT", "PJ", "freelancer"]

# Tenant usado quando o evento não declara um. MVP: a CRAI ainda é operada para
# um cliente por instalação, e exigir tenant em todo evento quebraria os
# webhooks já integrados. O nome é literal e reservado — ver
# `_tenant_da_requisicao` em `api/app.py`, que recusa este valor vindo de fora
# para que ninguém escreva no balde do "não declarado" de propósito.
TENANT_PADRAO = "default_tenant"

MESES_LTV_RETIDO = 6          # valor retido em caso de aceite (consistente com o Módulo 1)
MRR_TIPICO = {"CLT": 300.0, "PJ": 550.0, "freelancer": 220.0, "default": 350.0}

# Limiar de criticidade. NÃO é desvio de fluxo: só sinaliza que a mensagem deve
# sair com tom de alto cuidado. O braço `consulta_cs`, que era escolhido acima
# deste limiar, foi removido.
CRITICAL_RISK_THRESHOLD = 0.90

# Priors de cold start (benchmarks de mercado), em pseudo-observações (aceites, recusas)
SEED_PRIORS = {
    "CLT":        {"desconto_10": (9, 11),  "desconto_20": (14, 6), "pausa_1_mes": (8, 12),
                   "pix_boleto_flash": (2, 8)},
    "PJ":         {"desconto_10": (5, 10),  "desconto_20": (8, 7),  "pausa_1_mes": (13, 7),
                   "pix_boleto_flash": (3, 7)},
    "freelancer": {"desconto_10": (9, 11),  "desconto_20": (9, 11), "pausa_1_mes": (6, 9),
                   "pix_boleto_flash": (5, 5)},
}


def is_critical_risk(risk_score: float) -> bool:
    """Sinaliza criticidade só para ajustar o TOM da mensagem, nunca para
    desviar a oferta ou acionar humano."""
    return risk_score >= CRITICAL_RISK_THRESHOLD


def offer_cost(offer: str, mrr: float) -> float:
    """Custo da intervenção em R$ (descontos custam % do MRR por 3 meses)."""
    return {
        "desconto_10": 0.10 * 3 * mrr,
        "desconto_20": 0.20 * 3 * mrr,
        "pausa_1_mes": 1.0 * mrr,
        "pix_boleto_flash": 2.0,
    }[offer]


def _priors_de_benchmark() -> dict:
    """Estado completo de cold start: PROFILES × OFFERS a partir de SEED_PRIORS."""
    return {
        p: {o: {"alpha": 1.0 + a, "beta": 1.0 + b} for o, (a, b) in SEED_PRIORS[p].items()}
        for p in PROFILES
    }


def _posterior_valido(valor) -> bool:
    """Um posterior só serve se for Beta(α>0, β>0) com números reais."""
    if not isinstance(valor, dict):
        return False
    try:
        alpha, beta = float(valor["alpha"]), float(valor["beta"])
    except (KeyError, TypeError, ValueError):
        return False
    return alpha > 0 and beta > 0


def _sanear_perfis(bruto: dict, rotulo: str, descartes: dict) -> dict:
    """Um tenant: `{profile: {offer: {alpha,beta}}}` normalizado.

    Duas coisas exigem isto, e as duas são reais neste repositório:

    1. `consulta_cs` foi um braço do bandit e tem observações acumuladas no
       arquivo persistido. Ele não existe mais — a CRAI não escala para humano.
       As observações desse braço são DESCARTADAS, não redistribuídas: elas
       mediam a aceitação de uma oferta que ninguém mais pode receber, e
       diluí-las nos outros braços inventaria evidência que nunca houve.

    2. `record_outcome` aceita qualquer string como perfil e persiste. Rodadas
       de teste de borda (fuzz dos webhooks) gravaram perfis como "", "abc" e
       "299,90" no arquivo real. Chave fora de PROFILES é ruído de teste, não
       aprendizado: descartada.

    O que sobra é sobreposto aos priors de benchmark, então o resultado tem
    SEMPRE a forma completa — nenhum consumidor lida com perfil ou oferta
    faltando.
    """
    perfis = _priors_de_benchmark()

    for perfil, ofertas in bruto.items():
        if perfil not in PROFILES or not isinstance(ofertas, dict):
            descartes["perfis"].append(f"{rotulo}/{perfil}")
            continue
        for oferta, posterior in ofertas.items():
            if oferta not in OFFERS:
                descartes["ofertas"].append(f"{rotulo}/{perfil}/{oferta}")
                continue
            if not _posterior_valido(posterior):
                descartes["posteriores"].append(f"{rotulo}/{perfil}/{oferta}")
                continue
            perfis[perfil][oferta] = {
                "alpha": float(posterior["alpha"]),
                "beta": float(posterior["beta"]),
            }

    return perfis


def _e_formato_antigo(bruto: dict) -> bool:
    """Detecta o formato pré-tenant `{profile: {offer: {alpha,beta}}}`.

    A DETECÇÃO É ESTRUTURAL, não por nome. O plano do sprint sugeria olhar se
    as chaves do topo são `"CLT"`/`"PJ"`/`"freelancer"`, e isso quebra no dia em
    que um tenant se chamar `CLT` — improvável, mas o custo de errar é fundir o
    aprendizado de uma empresa cliente inteira com o de outra, que é justamente
    o que este sprint existe para impedir.

    O discriminador é o SEGUNDO nível de chaves, e ele é exato porque os dois
    vocabulários são fechados e DISJUNTOS:

        formato antigo   bruto[perfil] -> chaves em OFFERS
        formato novo     bruto[tenant] -> chaves em PROFILES

    A primeira versão disto olhava se o terceiro nível tinha `alpha`/`beta`, e
    os testes de posterior malformado mostraram o furo: `{"CLT": {"desconto_10":
    {"alpha": 1.0}}}` (sem `beta`) e `{"CLT": {"desconto_10": [10, 12]}}` eram
    lidos como formato NOVO, criando um tenant chamado `CLT`. Ou seja, um
    arquivo antigo levemente corrompido dividiria o aprendizado do cliente em
    dois baldes em silêncio — o defeito exato que a camada de tenant existe para
    impedir, causado por quem devia impedi-lo. Contar chaves de um vocabulário
    fechado sobrevive a qualquer lixo abaixo delas.

    Sem sinal nenhum nas chaves internas (dicionário vazio, ou só chaves
    desconhecidas), decide pelo nome do topo. Sem sinal nem ali, é formato novo
    — não há o que migrar de qualquer jeito.
    """
    ofertas = perfis = 0
    for valor in bruto.values():
        if not isinstance(valor, dict):
            continue
        for chave in valor:
            if chave in OFFERS:
                ofertas += 1
            elif chave in PROFILES:
                perfis += 1

    if ofertas or perfis:
        return ofertas > perfis
    return any(chave in PROFILES for chave in bruto)


def sanear_estado(bruto) -> tuple[dict, dict]:
    """Normaliza o JSON persistido para `{tenant: {profile: {offer: {α,β}}}}`.

    RETROCOMPATIBILIDADE: um arquivo no formato pré-tenant é migrado inteiro
    para dentro de `default_tenant`, sem erro e sem perder observação. É o
    caminho que todo deploy existente vai percorrer uma vez, e ele acontece na
    leitura — o arquivo só é reescrito no formato novo quando algo muda.

    Tenant NÃO tem vocabulário fechado (é identificador de empresa cliente, não
    enumeração), então a validação aqui é de FORMA: chave string não-vazia com
    valor dicionário. Quem restringe o conteúdo é a borda da API, onde o
    tenant_id chega — ver `_tenant_da_requisicao` em `api/app.py`.

    Devolve (estado, descartes) para o chamador logar.
    """
    descartes = {"tenants": [], "perfis": [], "ofertas": [], "posteriores": []}

    if not isinstance(bruto, dict):
        descartes["tenants"].append("<raiz do arquivo não é um objeto JSON>")
        return {TENANT_PADRAO: _priors_de_benchmark()}, descartes

    if _e_formato_antigo(bruto):
        print(f"[BANDIT] Estado no formato pré-tenant — migrando para "
              f"'{TENANT_PADRAO}'")
        return ({TENANT_PADRAO: _sanear_perfis(bruto, TENANT_PADRAO, descartes)},
                descartes)

    estado = {}
    for tenant, perfis in bruto.items():
        if not isinstance(tenant, str) or not tenant.strip() or not isinstance(perfis, dict):
            descartes["tenants"].append(repr(tenant))
            continue
        estado[tenant] = _sanear_perfis(perfis, tenant, descartes)

    if not estado:
        estado[TENANT_PADRAO] = _priors_de_benchmark()

    return estado, descartes


class OfferBandit:
    """Thompson Sampling (Beta-Bernoulli) otimizando e-Profit por tenant e perfil.

    O estado é `{tenant: {profile: {offer: {"alpha","beta"}}}}`. Antes do
    Sprint 5 a camada de tenant não existia e TODAS as empresas clientes
    dividiam o mesmo posterior: uma SaaS de nicho jurídico ensinava a CRAI sobre
    a base de uma SaaS de e-commerce. Isolar não é preferência de arquitetura —
    é a diferença entre um bandit por cliente e um bandit médio que não serve a
    ninguém.
    """

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)
        self.is_fitted = False
        self.state = {TENANT_PADRAO: _priors_de_benchmark()}

    def _perfis_do_tenant(self, tenant_id: str) -> dict:
        """O sub-dicionário do tenant, criado a partir dos priors na 1ª vez.

        Tenant novo começa do benchmark de mercado, não do aprendizado de outro:
        herdar posterior alheio é o mesmo vazamento que a camada veio impedir,
        só que disfarçado de warm start.
        """
        return self.state.setdefault(tenant_id or TENANT_PADRAO,
                                     _priors_de_benchmark())

    # ── Carregamento do warm start ───────────────────────────────────────
    def load(self) -> bool:
        """Carrega posteriores aprendidos de crai/models/bandit_state.json.

        O arquivo é saneado na entrada (ver `sanear_estado`): braço extinto e
        perfil de ruído não voltam para a memória do bandit. Havendo descarte, o
        arquivo é reescrito já limpo — senão o mesmo lixo seria relido e
        relogado em toda inicialização.
        """
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                bruto = json.load(f)
        except FileNotFoundError:
            print("[BANDIT] Estado não encontrado — usando priors de benchmark (cold start)")
            return False
        except Exception as e:
            print(f"[BANDIT] Erro ao carregar estado: {e}")
            return False

        self.state, descartes = sanear_estado(bruto)
        self.is_fitted = True

        n_obs = sum(s["alpha"] + s["beta"] - 2
                    for t in self.state.values()
                    for p in t.values()
                    for s in p.values())
        print(f"[BANDIT] Posteriores carregados de {STATE_PATH} "
              f"({n_obs:.0f} observações em {len(self.state)} tenant(s))")

        if any(descartes.values()):
            print(f"[BANDIT] Saneamento descartou: {len(descartes['tenants'])} tenant(s), "
                  f"{len(descartes['perfis'])} perfil(is) fora de {PROFILES}, "
                  f"{len(descartes['ofertas'])} oferta(s) fora de {OFFERS}, "
                  f"{len(descartes['posteriores'])} posterior(es) malformado(s)")
            self._persist()

        return True

    # ── Interface consumida pelo agente (Módulo 5) ───────────────────────
    #
    # ATENÇÃO: as três assinaturas abaixo ganharam `tenant_id` como PRIMEIRO
    # parâmetro no Sprint 5. É posicional de propósito — um default silencioso
    # deixaria uma chamada esquecida escrevendo no tenant errado sem erro, que é
    # o pior desfecho possível numa camada de isolamento.
    def classificar_ofertas(self, tenant_id: str, profile: str, risk_score: float,
                            mrr: float | None = None) -> list[dict]:
        """UMA rodada de Thompson Sampling, com todos os braços à vista.

        É o mesmo sorteio de `choose_offer` — uma amostra Beta por oferta e o
        ranking pelo e-Profit com a taxa amostrada —, só que devolve a rodada
        inteira em vez de só o vencedor. Cada linha traz:

            offer              o braço
            p_amostrado        a taxa sorteada do posterior NESTA rodada (é o
                               número que decidiu)
            p_estimado         a média do posterior — a probabilidade de aceite
                               que o bandit aprendeu até aqui
            alpha, beta        os parâmetros do posterior Beta de onde saiu
                               `p_estimado` (informativos, para o painel)
            eprofit_amostrado  p_amostrado * LTV_retido - custo (o critério)
            custo              custo da oferta em R$ para este MRR

        A lista vem ordenada do maior para o menor e-Profit amostrado, e o [0]
        é exatamente o que `choose_offer` devolve com a mesma semente: os dois
        consomem o gerador na mesma ordem. Não existe braço "sem oferta" nem
        braço humano — a lista é sempre OFFERS, e nada além dela.

        `risk_score` não desvia a decisão em ponto nenhum da faixa — quem lê a
        criticidade é o tom da mensagem, via `is_critical_risk`.
        """
        if mrr is None:
            mrr = MRR_TIPICO.get(profile, MRR_TIPICO["default"])
        ltv_retido = MESES_LTV_RETIDO * mrr

        perfis = self._perfis_do_tenant(tenant_id)
        perfil = perfis.get(profile, perfis["CLT"])

        linhas = []
        for o, s in perfil.items():
            p = float(self.rng.beta(s["alpha"], s["beta"]))
            custo = offer_cost(o, mrr)
            linhas.append({
                "offer": o,
                "p_amostrado": round(p, 4),
                "p_estimado": round(s["alpha"] / (s["alpha"] + s["beta"]), 3),
                # Os dois parâmetros do posterior, só para o painel mostrar que
                # `p_estimado` foi aprendido (alpha/(alpha+beta)) e com que
                # peso. Informativos: não entram na ordenação nem na escolha.
                "alpha": round(float(s["alpha"]), 3),
                "beta": round(float(s["beta"]), 3),
                "eprofit_amostrado": round(p * ltv_retido - custo, 2),
                "custo": round(custo, 2),
                "_criterio": p * ltv_retido - custo,
            })
        # Ordenação estável sobre o critério SEM arredondar: em empate, vence a
        # primeira na ordem do dicionário — o mesmo desempate do `max` antigo.
        linhas.sort(key=lambda l: l["_criterio"], reverse=True)
        for l in linhas:
            del l["_criterio"]
        return linhas

    def choose_offer(self, tenant_id: str, profile: str, risk_score: float,
                     mrr: float | None = None) -> str:
        """Thompson Sampling em TODA a faixa de risco: amostra um posterior por
        oferta e devolve a que maximiza o e-Profit com a taxa amostrada.

        É `classificar_ofertas(...)[0]`. Continua existindo para quem só quer
        a decisão; quem precisa mostrar as candidatas usa a rodada inteira.
        """
        return self.classificar_ofertas(tenant_id, profile, risk_score, mrr=mrr)[0]["offer"]

    def record_outcome(self, tenant_id: str, profile: str, offer: str, accepted: bool):
        """Atualiza o posterior após saber se o cliente aceitou e persiste."""
        perfis = self._perfis_do_tenant(tenant_id)
        s = perfis.setdefault(profile, {o: {"alpha": 1.0, "beta": 1.0} for o in OFFERS})
        s = s.setdefault(offer, {"alpha": 1.0, "beta": 1.0})
        if accepted:
            s["alpha"] += 1.0
        else:
            s["beta"] += 1.0
        self._persist()

    def conversion_rates(self, tenant_id: str, profile: str) -> dict:
        """Média do posterior por oferta, dentro do tenant."""
        perfis = self._perfis_do_tenant(tenant_id)
        perfil = perfis.get(profile, perfis["CLT"])
        return {
            o: round(s["alpha"] / (s["alpha"] + s["beta"]), 3)
            for o, s in perfil.items()
        }

    def _persist(self):
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[BANDIT] Falha ao persistir estado: {e}")
