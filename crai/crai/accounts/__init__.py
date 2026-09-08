"""crai/accounts — identidade da empresa cliente no fluxo self-service.

Só VALIDAÇÃO de token. Quem cadastra, loga e guarda a empresa é o Supabase
(Auth + Postgres); a CRAI recebe o JWT que ele emitiu e confere assinatura,
expiração e a claim `tenant_id`. Não existe tabela de empresa, senha nem API
key própria aqui — ver `README.md` ao lado.
"""

from .auth import get_conta, get_tenant_id
from .supabase_auth import (
    ConfiguracaoAusente,
    TokenInvalido,
    validar_token,
)

__all__ = ["get_tenant_id", "get_conta", "validar_token", "TokenInvalido",
           "ConfiguracaoAusente"]
