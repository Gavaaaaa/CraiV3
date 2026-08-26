"""Arquitetura da LSTM de liquidez para o Modulo 3 do CRAI.

A rede recebe uma janela de 30 dias do historico do cliente (5 features
por dia) e devolve, de uma vez, a probabilidade de haver liquidez em cada
um dos 14 dias seguintes (seq2vec multi-rotulo).

Features por passo de tempo:
  0. has_liquidity   (0/1)        - o cliente tinha saldo naquele dia
  1. balance_norm    (clip 0-5)   - saldo em multiplos da mensalidade
  2. sin(dia_do_mes) / 3. cos(dia_do_mes) - posicao ciclica no mes
  4. is_business_day (0/1)

A codificacao ciclica evita a descontinuidade dia 31 -> dia 1, importante
porque toda a sazonalidade de pagamento brasileira e mensal.
"""
from __future__ import annotations

import torch
from torch import nn

INPUT_FEATURES = 5
WINDOW = 30
HORIZON = 14


class LiquidityLSTM(nn.Module):
    def __init__(
        self,
        input_size: int = INPUT_FEATURES,
        hidden_size: int = 64,
        num_layers: int = 2,
        horizon: int = HORIZON,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.horizon = horizon
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, horizon),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, WINDOW, INPUT_FEATURES) -> logits (batch, HORIZON)."""
        _, (h_n, _) = self.lstm(x)
        return self.head(h_n[-1])

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            return torch.sigmoid(self.forward(x))
