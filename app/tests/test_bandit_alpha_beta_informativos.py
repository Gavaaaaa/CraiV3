"""tests/test_bandit_alpha_beta_informativos.py — alpha/beta não mudam a escolha.

`classificar_ofertas` passou a devolver `alpha` e `beta` (os parâmetros do
posterior de onde sai `p_estimado`), e `montar_candidatas` os repassa em cada
candidata, para o painel mostrar que a taxa de aceite foi aprendida e com que
peso. São campos INFORMATIVOS: a ordenação, o sorteio, o critério e a escolha
têm que ser exatamente os de antes.

Os valores-ouro abaixo foram MEDIDOS com o código anterior a esta mudança
(mesmas sementes, mesmos perfis, mesmos MRRs), não deduzidos: se algum deles
mudar, a escolha do bandit mudou.
"""

import pytest

from crai.churn_voluntary.offer_bandit import OfferBandit, TENANT_PADRAO
from crai.churn_voluntary import voluntary_agent as va

# (seed, perfil, mrr) -> (escolha de choose_offer, ordem da rodada, p_amostrado, eprofit)
OURO = {
    (1, "CLT", None):         ("desconto_20", ["desconto_20", "desconto_10", "pausa_1_mes", "pix_boleto_flash"], [0.7741, 0.4566, 0.4339, 0.1826], [1213.35, 731.84, 480.95, 326.64]),
    (1, "CLT", 400.0):        ("desconto_20", ["desconto_20", "desconto_10", "pausa_1_mes", "pix_boleto_flash"], [0.7741, 0.4566, 0.4339, 0.1826], [1617.81, 975.79, 641.27, 436.19]),
    (1, "PJ", 2450.0):        ("desconto_20", ["desconto_20", "pausa_1_mes", "desconto_10", "pix_boleto_flash"], [0.6493, 0.6604, 0.3566, 0.2749], [8074.33, 7257.17, 4506.66, 4039.31]),
    (1, "freelancer", 400.0): ("pix_boleto_flash", ["pix_boleto_flash", "desconto_20", "desconto_10", "pausa_1_mes"], [0.4708, 0.5625, 0.4566, 0.4396], [1127.99, 1110.01, 975.79, 655.08]),
    (7, "CLT", 400.0):        ("desconto_20", ["desconto_20", "desconto_10", "pix_boleto_flash", "pausa_1_mes"], [0.6557, 0.4734, 0.2488, 0.3349], [1333.64, 1016.28, 595.2, 403.77]),
    (7, "PJ", None):          ("pausa_1_mes", ["pausa_1_mes", "desconto_20", "pix_boleto_flash", "desconto_10"], [0.5671, 0.4856, 0.3379, 0.3666], [1321.41, 1272.49, 1113.22, 1044.75]),
    (7, "freelancer", 2450.0):("pix_boleto_flash", ["pix_boleto_flash", "desconto_10", "desconto_20", "pausa_1_mes"], [0.5141, 0.4734, 0.412, 0.3269], [7554.75, 6224.7, 4587.01, 2355.97]),
    (42, "CLT", None):        ("desconto_20", ["desconto_20", "desconto_10", "pix_boleto_flash", "pausa_1_mes"], [0.5451, 0.4247, 0.2153, 0.3492], [801.24, 674.4, 385.55, 328.53]),
    (42, "PJ", 400.0):        ("pausa_1_mes", ["pausa_1_mes", "pix_boleto_flash", "desconto_10", "desconto_20"], [0.5675, 0.2958, 0.3255, 0.338], [961.99, 707.82, 661.22, 571.26]),
    (42, "freelancer", None): ("pix_boleto_flash", ["pix_boleto_flash", "desconto_10", "desconto_20", "pausa_1_mes"], [0.4594, 0.4247, 0.2831, 0.3434], [604.45, 494.56, 241.65, 233.23]),
    (2026, "CLT", 2450.0):    ("desconto_20", ["desconto_20", "desconto_10", "pix_boleto_flash", "pausa_1_mes"], [0.7454, 0.5397, 0.2189, 0.339], [9486.69, 7199.13, 3215.16, 2533.78]),
    (2026, "PJ", 400.0):      ("desconto_20", ["desconto_20", "pausa_1_mes", "desconto_10", "pix_boleto_flash"], [0.6099, 0.5635, 0.4157, 0.3039], [1223.69, 952.41, 877.72, 727.34]),
    (2026, "freelancer", None):("desconto_10", ["desconto_10", "pix_boleto_flash", "desconto_20", "pausa_1_mes"], [0.5397, 0.4765, 0.5258, 0.3318], [646.45, 627.03, 562.05, 217.95]),
}


@pytest.mark.parametrize("chave", sorted(OURO, key=str))
def test_escolha_e_rodada_iguais_as_de_antes(chave):
    seed, perfil, mrr = chave
    escolha, ordem, p_amostrado, eprofit = OURO[chave]
    rodada = OfferBandit(seed=seed).classificar_ofertas(TENANT_PADRAO, perfil, 0.8, mrr=mrr)
    assert [l["offer"] for l in rodada] == ordem
    assert [l["p_amostrado"] for l in rodada] == p_amostrado
    assert [l["eprofit_amostrado"] for l in rodada] == eprofit
    assert OfferBandit(seed=seed).choose_offer(TENANT_PADRAO, perfil, 0.8, mrr=mrr) == escolha
    assert rodada[0]["offer"] == escolha


def test_alpha_beta_presentes_e_coerentes_com_p_estimado():
    rodada = OfferBandit(seed=1).classificar_ofertas(TENANT_PADRAO, "CLT", 0.8, mrr=400.0)
    for linha in rodada:
        assert linha["alpha"] > 0 and linha["beta"] > 0
        assert linha["p_estimado"] == round(linha["alpha"] / (linha["alpha"] + linha["beta"]), 3)
    # as chaves antigas continuam todas lá
    assert {"offer", "p_amostrado", "p_estimado", "eprofit_amostrado", "custo"} <= set(rodada[0])


def test_candidatas_repassam_alpha_beta():
    bandit = OfferBandit(seed=1)
    rodada = bandit.classificar_ofertas(TENANT_PADRAO, "CLT", 0.8, mrr=400.0)
    estado = {"tenant_id": TENANT_PADRAO, "profile": "CLT", "criticality": "alto",
              "offer_type": rodada[0]["offer"], "ofertas_consideradas": rodada[:va.N_CANDIDATAS]}
    candidatas = va.montar_candidatas(estado, "texto da vencedora", origem_texto_vencedora="template")
    assert len(candidatas) == va.N_CANDIDATAS
    for c, linha in zip(candidatas, rodada):
        assert c["alpha"] == linha["alpha"] and c["beta"] == linha["beta"]
        assert c["p_sucesso"] == linha["p_estimado"]
    assert sum(c["escolhida"] for c in candidatas) == 1


def test_caminho_sem_rodada_traz_alpha_beta_nulos():
    """Chamador antigo (sem `ofertas_consideradas`): uma candidata, com p do
    bandit e alpha/beta None — nada é inventado."""
    estado = {"tenant_id": TENANT_PADRAO, "profile": "CLT", "criticality": "padrao",
              "offer_type": "desconto_20"}
    (c,) = va.montar_candidatas(estado, "texto")
    assert c["alpha"] is None and c["beta"] is None and c["p_sucesso"] > 0
