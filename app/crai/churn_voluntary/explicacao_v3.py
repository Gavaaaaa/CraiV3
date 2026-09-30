"""
crai/churn_voluntary/explicacao_v3.py — Por que o modelo v3 decidiu: TreeSHAP.

Promoção do voluntário v3, Bloco 1 (P3, 30/09/2026). Quando o modelo v3 decide
o risco de um cliente, a trilha do Art. 20 recebe as `K_CONTRIBUICOES` features
de maior peso NESTA decisão, com a direção, na coluna `contribuicoes` que o
schema já tem. `retention_log.frase_da_decisao` transforma isso em
"Pesaram, nesta ordem: 12 chamados de suporte nos últimos 30 dias (aumentou a
chance), ...", com os rótulos de `retention_log.ROTULOS_DE_FEATURE`.

Três regras:

- As três flags de evento viram UM item, `event` — um cliente não "abriu a
  página de cancelamento" e "não clicou em downgrade" como dois motivos.
- Só entram features PRESENTES. Coluna que o cliente não mandou (NaN) não é
  motivo sobre ele; o modelo usa o NaN, mas a explicação não diz "pesou o que
  você não informou".
- Nunca levanta e nunca inventa. Falhou o SHAP (biblioteca ausente, modelo que
  o TreeExplainer não aceita), devolve None e a trilha grava NULL.

Nenhum dado pessoal: o vetor é o do contrato, só números e o nome do evento.
"""

import math

K_CONTRIBUICOES = 3
FLAGS_DE_EVENTO = ("evento_cancelamento", "evento_downgrade", "evento_sessao")

_explicadores: dict = {}


def _explicador(modelo):
    """Um `shap.TreeExplainer` por modelo, por processo. Import preguiçoso: o
    shap é pesado e só é preciso quando o modelo v3 está ativo."""
    chave = id(modelo)
    if chave not in _explicadores:
        import shap
        _explicadores.clear()
        _explicadores[chave] = shap.TreeExplainer(modelo)
    return _explicadores[chave]


def contribuicoes_shap(modelo, features: list, vetor: list, event: str,
                       k: int = K_CONTRIBUICOES) -> list | None:
    """As k maiores contribuições desta decisão, ou None.

    Cada item: `{"feature", "valor", "direcao" ("+"/"-"), "peso"}`, com `peso`
    o valor SHAP em log-odds (positivo = aumentou a chance de cancelamento).
    """
    try:
        import numpy as np

        valores = np.asarray(_explicador(modelo).shap_values(
            np.asarray([vetor], dtype=float)))
        if valores.ndim == 3:
            valores = valores[..., 1]
        valores = valores.reshape(-1)
        if len(valores) != len(features):
            return None

        itens = []
        peso_evento = 0.0
        for nome, x, peso in zip(features, vetor, valores):
            if nome in FLAGS_DE_EVENTO:
                peso_evento += float(peso)
                continue
            if x is None or (isinstance(x, float) and math.isnan(x)):
                continue
            itens.append({"feature": nome, "valor": float(x), "peso": float(peso)})
        itens.append({"feature": "event", "valor": str(event), "peso": peso_evento})

        itens = [i for i in itens if i["peso"] != 0.0]
        itens.sort(key=lambda i: abs(i["peso"]), reverse=True)
        return [{**i, "peso": round(i["peso"], 4),
                 "direcao": "+" if i["peso"] > 0 else "-"} for i in itens[:k]] or None
    except Exception as e:  # noqa: BLE001 - explicação nunca derruba a decisão
        print(f"[RISK-VOL] TreeSHAP falhou ({e}); a trilha grava contribuicoes NULL")
        return None
