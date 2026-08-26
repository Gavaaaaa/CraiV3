"""Arquitetura do Autoencoder denso para deteccao de anomalias comportamentais.

Encoder afunila 12 features de entrada ate um bottleneck de 4 dimensoes,
forcando a rede a aprender uma representacao compacta do comportamento
saudavel. Em inferencia, o erro de reconstrucao funciona como score de
anomalia: clientes que nao se parecem com a populacao saudavel sao
reconstruidos com erro alto.
"""
from __future__ import annotations

import torch
from torch import nn


class BehaviorAutoencoder(nn.Module):
    def __init__(self, input_dim: int = 12, bottleneck: int = 4, dropout: float = 0.1) -> None:
        super().__init__()
        self.input_dim = input_dim
        self.bottleneck = bottleneck

        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, bottleneck),
        )

        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, 16),
            nn.ReLU(),
            nn.Linear(16, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        return self.encoder(x)

    def reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            recon = self.forward(x)
            return ((recon - x) ** 2).mean(dim=1)
