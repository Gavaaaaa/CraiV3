"""crai/accounts — identidade da empresa cliente no fluxo self-service.

Validação do token de login: quem cadastra, loga e guarda a empresa é o
Supabase (Auth + Postgres); a CRAI recebe o JWT que ele emitiu e confere
assinatura, expiração e a claim `tenant_id`. Não existe tabela de empresa nem
senha aqui — ver `README.md` ao lado.

Desde a Rodada 2 a CRAI emite a CHAVE DE API da empresa (`chaves_api.py`): um
segundo caminho de autenticação, que vale só nas quatro rotas da API de
clientes. O banco guarda o hash da chave, nunca a chave.
"""

from .auth import get_conta, get_tenant_id
from .supabase_auth import (
    ConfiguracaoAusente,
    TokenInvalido,
    validar_token,
)

__all__ = ["get_tenant_id", "get_conta", "validar_token", "TokenInvalido",
           "ConfiguracaoAusente"]
