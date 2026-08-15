#!/usr/bin/env bash
# Hook de pre-commit: bloqueia commit acidental de segredos reais.
# Ativação local (não roda automaticamente): ver README.md > "Proteção contra
# Commit de Segredos".

set -euo pipefail

# Bloqueia qualquer arquivo .env real (não .env.example) staged para commit.
env_files=$(git diff --cached --name-only --diff-filter=ACM | grep -E '(^|/)\.env$' || true)
if [ -n "$env_files" ]; then
    echo "[pre-commit] BLOQUEADO: arquivo .env real staged para commit:"
    echo "$env_files" | sed 's/^/  /'
    echo "[pre-commit] .env nunca deve ser commitado — use .env.example com placeholders."
    exit 1
fi

# Bloqueia padrões de chave real (não placeholders terminados em "...") no diff staged.
patterns='sk-ant-[a-zA-Z0-9_-]{10,}|sk_live_[a-zA-Z0-9]{10,}|whsec_[a-zA-Z0-9]{10,}|pat-na1-[a-zA-Z0-9-]{10,}'
hits=$(git diff --cached -U0 -- . ':!scripts/pre-commit-secrets.sh' | grep -E '^\+' | grep -vE '^\+\+\+' | grep -E "$patterns" || true)
if [ -n "$hits" ]; then
    echo "[pre-commit] BLOQUEADO: possível chave real detectada no diff staged:"
    echo "$hits" | sed 's/^/  /'
    echo "[pre-commit] se for falso positivo, revise o padrão em scripts/pre-commit-secrets.sh."
    exit 1
fi

exit 0
