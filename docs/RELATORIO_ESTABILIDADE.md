# RELATÓRIO DE ESTABILIDADE — Blocos A e B

**Data:** 27/09/2026 · **Working tree, sem commit, add, push ou stash.**
`crai/churn_voluntary/retention_log.py` **intocado** (é o Bloco C).

## Sumário

Duas causas distintas, uma por bloco.

**A.** `app/test_pipeline.py` é um script de demonstração, mas o nome casa com
`python_files = test_*.py`: o pytest o **importava** na coleta, e o import
rodava `os.environ.setdefault("CRAI_SIMULATE_OUTCOMES", "1")` em nível de
módulo. A env ficava ligada pela sessão inteira, `build_voluntary_churn_graph`
montava a topologia da demo (com `track_outcome`, que sorteia o desfecho e
fecha o ciclo na hora), e a suíte passava a exercitar um grafo que não é o de
produção. Corrigido, com catraca para a próxima vez e os quatro estados globais
do pipeline voluntário isolados por teste.

**B.** As duas rotas do painel montavam o identificador com o relógio em
segundos. Um id de resolução grosseira erra dos dois lados: **funde** duas
chamadas no mesmo segundo e **parte** um lote que cruza a virada. Trocado por
um id explícito, sorteado uma vez e propagado.

Depois dos dois: **20 execuções seguidas da suíte cheia, todas verdes, 0
skipped** (10 ao fim do Bloco A, 10 ao fim do Bloco B).

---

# BLOCO A — a env vazada e o estado global

## A.1 — o script

`app/test_pipeline.py:35`. **Escolhi mover a chamada para dentro de
`if __name__ == "__main__":`**, via uma função `ligar_modo_simulacao()`.

**Por que essa, e não tirar o arquivo de lá.** O critério era preservar o uso
do script rodado direto. O arquivo é chamado pelo nome em `app/README.md` (3
vezes, incluindo o primeiro comando do arquivo),
`crai/churn_voluntary/README_treino.md:229` e `:283`, `docs/ESTRUTURA.md:29` e
nas auditorias. Renomeá-lo ou movê-lo quebra `python test_pipeline.py` em todos
esses lugares — ou seja, é justamente a opção que **não** preserva o uso
direto. Com a chamada no `__main__`, o comando documentado roda idêntico e o
import pelo pytest deixa de ter efeito colateral.

Verificado depois da mudança — o script continua rodando em modo simulação:

```
$ PYTHONIOENCODING=utf-8 python test_pipeline.py
...
✅ Pipeline CRAI v2 (involuntário + voluntário + HubSpot) funcionando!
EXIT=0
$ ... | grep -c "Resultado: (✅ ACEITOU|❌ recusou)"
4          <- os 4 cenários voluntários fecharam o ciclo, como a demo exige
```

O que o arquivo **continua** fazendo no import é `load_dotenv()`. Se alguém
puser `CRAI_SIMULATE_OUTCOMES=1` num `.env`, o vazamento volta por outra porta
— e é exatamente o caso que a catraca A.2 pega. (Conferido: o `.env` desta
máquina tem 17 bytes e só `ENV`.)

### Diff — `app/test_pipeline.py`

```diff
@@ -22,18 +22,6 @@ from dotenv import load_dotenv
 load_dotenv()

-# A DEMO RODA EM MODO SIMULAÇÃO, e isso é uma escolha, não um descuido.
-#
-# Em produção o grafo termina no envio e o desfecho chega depois, por
-# `POST /webhooks/retention-outcome` (Sprint 4). Uma demo assim mostraria quatro
-# clientes "aguardando retorno" e nenhum resultado — não dá para demonstrar
-# retenção sem o desfecho. Com a env ligada, `track_outcome` sorteia o aceite
-# pela taxa histórica do bandit e a demo fecha o ciclo na hora.
-#
-# `setdefault` e não atribuição: quem quiser ver o comportamento de produção
-# roda `CRAI_SIMULATE_OUTCOMES=0 python test_pipeline.py` e o script respeita.
-os.environ.setdefault("CRAI_SIMULATE_OUTCOMES", "1")
-
 from crai.agent.main_agent import crai_agent
@@ -373,5 +361,36 @@ async def main():
+def ligar_modo_simulacao() -> None:
+    """A DEMO RODA EM MODO SIMULAÇÃO, e isso é uma escolha, não um descuido.
+    ...(o comentário acima, preservado inteiro)...
+
+    POR QUE ISTO NÃO ESTÁ MAIS NO NÍVEL DO MÓDULO (27/09/2026). O nome deste
+    arquivo casa com `python_files = test_*.py`, então o pytest o IMPORTA na
+    coleta — e o import ligava a env para a sessão inteira, sem que nenhuma
+    fixture desfizesse. (...) Chamar daqui, e não lá em cima, preserva
+    `python test_pipeline.py` EXATAMENTE como os READMEs o documentam — o
+    efeito só existe quando o script é o programa, que é quando ele foi
+    pedido. A catraca que impede a volta do vazamento, por este arquivo ou
+    por outro `test_*` qualquer, é
+    `tests/conftest.py::a_env_de_simulacao_nao_vaza_para_a_sessao`.
+    """
+    os.environ.setdefault("CRAI_SIMULATE_OUTCOMES", "1")
+
+
 if __name__ == "__main__":
+    ligar_modo_simulacao()
     asyncio.run(main())
```

## A.2 — a catraca

`app/tests/conftest.py::a_env_de_simulacao_nao_vaza_para_a_sessao`, autouse de
sessão. Três decisões que valem registro:

- usa `va.modo_simulacao()`, a **mesma função do produto**, para que catraca e
  comportamento não possam divergir;
- roda depois da coleta e antes do primeiro teste, que é exatamente a janela em
  que o import de um script coletado já agiu;
- chama `pytest.exit(returncode=1)` e **não** `pytest.fail` — numa fixture de
  sessão, um `fail` viraria um erro por teste, 1469 linhas para dizer uma coisa
  só. Medido: com `UsageError` saíam 21 erros num arquivo de 21 testes.

Não barra `=0`, não barra ausente, e não barra `monkeypatch.setenv` dentro de
um teste (`test_retention_outcome.py:133`,
`test_tenant_isolation_voluntary.py:336` e `:351`,
`test_webhook_security.py:956` continuam funcionando).

**Testada ligando a env de propósito:**

```
$ CRAI_SIMULATE_OUTCOMES=1 python -m pytest app/tests/test_canal_escolhido.py -q
EXIT=1
no tests ran in 5.45s
! _pytest.outcomes.Exit:
CRAI_SIMULATE_OUTCOMES está LIGADA no início da sessão (valor '1').

Com ela ligada, `build_voluntary_churn_graph` monta outra topologia: entra o
nó `track_outcome`, que sorteia o desfecho e fecha o ciclo na hora. A suíte
deixa de exercitar o grafo de produção e dois testes passam a falhar de forma
intermitente.

Se foi um script coletado pelo pytest que a ligou no import, mova a chamada
para dentro de `if __name__ == "__main__":` — foi o que se fez em
`app/test_pipeline.py` (ver `docs/RELATORIO_ESTABILIDADE.md`).

Se foi você, de propósito: um teste que PRECISA do modo simulação liga a env
com `monkeypatch.setenv` DENTRO do próprio teste, não no ambiente da sessão.

Módulos `test_*` já importados nesta sessão: ['tests.test_canal_escolhido'] !
```

## A.3 — os quatro estados globais isolados

`estado_global_do_voluntario_isolado`, autouse por teste. Entra **antes** das
fixtures por arquivo (conftest é mais alto na hierarquia), então as que já
existem continuam vencendo onde existem.

| estado | onde vivia | como é isolado |
|---|---|---|
| `CRAI_SIMULATE_OUTCOMES` | processo | `monkeypatch.delenv` |
| `_channel_history` | `voluntary_agent.py:75` | `monkeypatch.setattr(va, "_channel_history", {})` |
| `MemorySaver` dos 2 grafos | `voluntary_agent.py:878` | `.storage` e `.writes` limpos **antes e depois** |
| `_bandit` | `voluntary_agent.py:66` | `MODELS_DIR`/`STATE_PATH` → `tmp_path`; `state` restaurado de um snapshot; `rng` → `default_rng(42)` |

Duas escolhas que não eram óbvias:

**O snapshot restaura o warm start REAL**, não priors frios. Assim cada teste
vê o mesmo bandit que veria antes desta fixture existir — o que muda é só que
ele não leva embora o que aprendeu.

**O `rng` entrou na lista** porque *qual* oferta o Thompson Sampling amostra
dependia de quantos sorteios os testes anteriores tinham feito. Sem isso, a
"isolação" seria parcial e a ordem de execução continuaria decidindo.

Também atualizei o docstring do módulo `conftest.py`, que passara a se
contradizer: dizia "MODELS_DIR/STATE_PATH do bandit **NÃO** é isolado aqui, e é
deliberado". O parágrafo novo separa as duas coisas — `crai.ml.MODELS_DIR`
continua livre (é o gate de honestidade do README);
`offer_bandit.MODELS_DIR` é constante do próprio módulo e não encosta nele.

## A.4 — o `bandit_state.json` real

Backup em `app/models/bandit_state.json.bak-2026-09-27` (e cópia no scratchpad).
`app/models/` é gitignored (`.gitignore:12`), então nada disso entra no repo.

```
--- ANTES ---                      --- DEPOIS ---
  default_tenant    obs~=1051.0      default_tenant    obs~=1051.0
  empresa-exemplo   obs~= 231.0
  painel_avaliacao  obs~= 314.0
  empresa-a         obs~= 219.0
  empresa-b         obs~= 219.0
```

Carrega, e os posteriores legítimos estão intactos ponto a ponto:

```
[BANDIT] Posteriores carregados de E:\Crai\app\models\bandit_state.json
         (1027 observações em 1 tenant(s))
load() -> True | is_fitted: True | tenants: ['default_tenant']
posteriores de default_tenant == backup: OK

  CLT         {'desconto_10': 0.455, 'desconto_20': 0.934, 'pausa_1_mes': 0.409, 'pix_boleto_flash': 0.25}
  PJ          {'desconto_10': 0.353, 'desconto_20': 0.474, 'pausa_1_mes': 0.606, 'pix_boleto_flash': 0.747}
  freelancer  {'desconto_10': 0.455, 'desconto_20': 0.455, 'pausa_1_mes': 0.412, 'pix_boleto_flash': 0.5}

choose_offer ainda funciona: pix_boleto_flash
```

E não volta a sujar — sha256 idêntico antes e depois de uma suíte inteira:

```
sha antes : 4a3718c180e525ad53d6685308ac4adbfe5a74160f6d25f9c22e8aa9e3c0f3ca
1469 passed in 51.33s
sha depois: 4a3718c180e525ad53d6685308ac4adbfe5a74160f6d25f9c22e8aa9e3c0f3ca
tenants apos a suite: ['default_tenant']
```

**Ressalva declarada:** `painel_avaliacao` é o tenant do painel de demonstração
(`TENANT_PAINEL`), não só de testes. Removê-lo faz o painel recomeçar dos
priors de benchmark — o cold start documentado, e mais honesto do que
posteriores construídos por fuzz de teste. Os 314 "observações" dele eram
indistinguíveis entre demo e suíte.

**Sujeira que eu mesmo criei, e desfiz.** Rodei `python test_pipeline.py` duas
vezes para verificar A.1; cada rodada escreve no estado real (é o que a demo
faz por projeto). Apareceu um `demo_tenant` no `bandit_state.json`, que
**removi**. As mesmas rodadas deixaram **8 ciclos `demo_tenant`** em
`app/data/retention_cycles.db`, que **não** removi: são saída legítima da demo,
não fuzz, e apagar linha de dataset não é decisão minha. Ao lado deles há 67
ciclos `painel_avaliacao` que já estavam lá antes.

## PORTÃO A

- [x] **10 execuções, todas verdes, 0 skipped** — comando exato do enunciado,
      de `E:\Crai\app`:

```
1469 passed in 50.01s    1469 passed in 50.43s
1469 passed in 49.76s    1469 passed in 50.86s
1469 passed in 49.92s    1469 passed in 50.28s
1469 passed in 49.58s    1469 passed in 50.29s
1469 passed in 50.12s    1469 passed in 50.29s
```

- [x] a catraca reprova com a env ligada (acima, `EXIT=1`)
- [x] nenhum arquivo de `app/crai/` alterado no Bloco A

---

# O FIO SOLTO DO BLOCO A

> *"por que as falhas apareciam também rodando `pytest tests` de dentro de
> app/, se `app/test_pipeline.py` não é coletado por ali?"*

**Não apareciam.** A premissa está errada, e verifiquei em vez de argumentar:
restaurei `conftest.py` e `test_pipeline.py` ao HEAD (sem nenhuma correção do
Bloco A) e rodei as duas formas no **mesmo código**, alternadamente.

De `E:\Crai\app`, com `python -m pytest tests` — **4/4 verdes**:

```
1469 passed in 49.42s
1469 passed in 50.36s
1469 passed in 51.71s
1469 passed in 49.37s
```

Da raiz `E:\Crai`, com `python -m pytest` — **4/4 vermelhas**:

```
FAILED ...test_tenant_isolation_voluntary.py::...::test_desfecho_de_um_tenant_nao_fecha_o_ciclo_do_outro
1 failed, 1468 passed in 50.80s
FAILED ...test_canal_escolhido.py::TestRotaDoPainel::test_dados_diferentes_canais_diferentes
FAILED ...test_tenant_isolation_voluntary.py::...::test_desfecho_de_um_tenant_nao_fecha_o_ciclo_do_outro
2 failed, 1467 passed in 51.48s
FAILED ...test_tenant_isolation_voluntary.py::...::test_desfecho_de_um_tenant_nao_fecha_o_ciclo_do_outro
1 failed, 1468 passed in 51.14s
FAILED ...test_canal_escolhido.py::TestRotaDoPainel::test_dados_diferentes_canais_diferentes
FAILED ...test_tenant_isolation_voluntary.py::...::test_desfecho_de_um_tenant_nao_fecha_o_ciclo_do_outro
2 failed, 1467 passed in 50.70s
```

Ou seja: a fronteira entre verde e vermelho é **exatamente** se o comando
coleta `app/test_pipeline.py`. Isso não enfraquece nada do Bloco A — reforça:
era mesmo o import do script, e nada mais.

**O que isso implica para o portão.** O comando do enunciado
(`python -m pytest tests`, de dentro de `app/`) **nunca teria detectado o
defeito** — nem antes nem depois. Por isso o portão do Bloco B usa a forma da
raiz, e é a que vale.

**Hipóteses que testei e descartei**, para não ficarem como dúvida:
`crai/api/app.py:37` chama `load_dotenv()` no import, então um `.env` com a env
ligada produziria o mesmo vazamento por ali — mas o `.env` desta máquina tem 17
bytes e contém só `ENV`.

---

# BLOCO B — o id de resolução de segundos

## B.1 — identificador de lote explícito

Novo `crai/api/app.py::id_de_lote(prefixo, pedido=None)`:

```python
limpo = re.sub(r"[^A-Za-z0-9_-]", "", pedido or "")[:48]
return f"{prefixo}_{limpo}" if limpo else f"{prefixo}_{uuid4().hex[:16]}"
```

Aplicado nos dois pontos que derivavam do relógio, e um campo opcional
`lote_id` em cada modelo de payload para que o id possa ser **gerado uma vez
onde o lote começa e propagado**:

| antes | depois |
|---|---|
| `app.py:1725` `id_rec = f"RN_painel_{int(datetime.now(timezone.utc).timestamp())}"` | `id_rec = id_de_lote("RN_painel", payload.lote_id)` |
| `app.py:1839` `user_id = f"painel_{int(datetime.now(timezone.utc).timestamp())}"` | `user_id = id_de_lote("painel", payload.lote_id)` |

Três pontos que decidiram o desenho:

**Não aumentei a resolução.** Trocar segundos por milissegundos ou `%f` muda a
probabilidade da colisão, não a natureza do defeito: continua sendo o relógio
decidindo quem é quem, e continua partindo o lote que cruza a virada.

**O `lote_id` é saneado.** Ele vem do navegador e vira `thread_id` de
checkpoint, chave de log e componente de chave por tenant. Passa pelo mesmo
`re.sub(r"[^A-Za-z0-9_-]", "", ...)[:48]` que o campo `cliente` de
`PainelCobranca` já usava. Lixo puro cai no sorteio, não numa chave vazia que
agruparia todo mundo.

**Escopo real do defeito, dito com precisão.** As duas rotas são
`/simulate/painel/*`, bloqueadas fora de `ENV=development|demo` por
`_require_simulation_env()`. O caminho de produção do involuntário usa
`_thread_id(evento)`, derivado do *payload*, não do relógio — esse nunca teve o
problema. O que estava em risco é o painel de avaliação, que é o que a banca vê,
e ali o efeito é concreto: duas cobranças no mesmo segundo compartilhariam o
contador de tentativas do BACEN.

### Diff — `app/crai/api/app.py`

```diff
@@ -23,6 +23,7 @@ import weakref
 from datetime import datetime, timezone
 from pathlib import Path
 from typing import Optional
+from uuid import uuid4

@@ -1692,6 +1693,42 @@ async def painel_ambiente():
+def id_de_lote(prefixo: str, pedido: str | None = None) -> str:
+    """Identificador de lote EXPLICITO. NAO derivado do relogio.
+
+      FUNDE   duas chamadas no mesmo segundo recebem a MESMA string. (...)
+      PARTE   um lote cujas chamadas cruzam a virada do segundo se quebra
+              em dois ids. (...)
+    """
+    limpo = re.sub(r"[^A-Za-z0-9_-]", "", pedido or "")[:48]
+    return f"{prefixo}_{limpo}" if limpo else f"{prefixo}_{uuid4().hex[:16]}"
+
 class PainelCobranca(BaseModel):
@@ -1709,6 +1746,9 @@ class PainelCobranca(BaseModel):
     cliente: str | None = None
+    # Agrupa varias chamadas no MESMO lote. Opcional: sem ele, cada chamada e
+    # um lote proprio, com id sorteado. Ver `id_de_lote`.
+    lote_id: str | None = None

@@ -1722,7 +1762,7 @@ async def painel_cobranca_falhada(payload: PainelCobranca):
-    id_rec = f"RN_painel_{int(datetime.now(timezone.utc).timestamp())}"
+    id_rec = id_de_lote("RN_painel", payload.lote_id)

@@ -1826,6 +1866,9 @@ class PainelEvento(BaseModel):
     on_site_now: bool = True
+    # Agrupa varias chamadas no MESMO lote. Opcional: sem ele, cada chamada e
+    # um lote proprio, com id sorteado. Ver `id_de_lote`.
+    lote_id: str | None = None

@@ -1836,7 +1879,7 @@ async def painel_evento_risco(payload: PainelEvento):
-    user_id = f"painel_{int(datetime.now(timezone.utc).timestamp())}"
+    user_id = id_de_lote("painel", payload.lote_id)
```

## B.2 — varredura: todo id montado a partir de timestamp

Varri `app/crai/`, `app/tests/`, `app/*.py` e `app/painel/` (JS/HTML: nenhuma
ocorrência de `Date.now()` / `getTime()`). A lista é completa; a coluna
**Risco** diz se o valor é usado como chave de agrupamento, correlação ou
deduplicação.

### Corrigidos neste bloco — eram chave, e de resolução grosseira

| arquivo:linha | construção | usado como | risco |
|---|---|---|---|
| `crai/api/app.py:1725` | `f"RN_painel_{int(...timestamp())}"` | `id_recorrencia`, `thread_id` do checkpoint involuntário, `e2e_id`, fallback de `customer_id` | **ALTO.** Duas cobranças no mesmo segundo dividiam o checkpoint — e com ele o contador de tentativas da janela do BACEN |
| `crai/api/app.py:1839` | `f"painel_{int(...timestamp())}"` | `user_id`, `thread_id`, chave de `_channel_history`, componente da chave `(tenant, user_id, offer_type)` do ciclo | **ALTO.** É a causa direta da intermitência de `test_dados_diferentes_canais_diferentes` |

### Não corrigidos — varridos, avaliados, e não correm o mesmo risco

| arquivo:linha | construção | por que não é o mesmo caso |
|---|---|---|
| `crai/ml/failure_classifier.py:774` | `f"shap_audit_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"` | É **nome de arquivo** de auditoria, não chave de agrupamento, e tem resolução de **microssegundo**. Colisão exigiria duas escritas no mesmo µs — e o efeito seria sobrescrever um arquivo de auditoria, não fundir dois clientes. **Risco residual baixo, declarado.** Se algum dia virar chave, entra na regra do `id_de_lote` |
| `crai/security/webhook_verification.py:82` | `abs(int(time.time()) - timestamp)` | Uso **correto** do relógio: mede a IDADE da assinatura para recusar replay. Não é identidade de nada |
| `crai/agent/workflow.py:299`, `:480` | `strftime('%d/%m %H:%M')` | Formatação para `print`. Não é chave |
| `crai/dunning/pix_automatico_retry.py:168,336,338,343,344,355,356,369,372` | `strftime('%d/%m/%Y %H:%M')` | Idem — mensagens de erro e log do plano de tentativas |
| `crai/dunning/legacy_card/card_retry.py:37` | `strftime('%d/%m %H:%M')` | Idem, e fora do pipeline ativo (Fase 3) |
| `crai/api/app.py:1820` | `strftime("%d/%m/%Y as %H:%M")` | Texto do plano na resposta do painel |
| `crai/churn_voluntary/retention_log.py:728` | `_data_legivel` → `strftime("%d/%m/%Y")` | Formatação de leitura. **Arquivo intocado (Bloco C)** |
| `crai/scripts/relatorio.py:257,544` | `strftime` | Cabeçalho de relatório |
| `crai/scripts/gerar_bases_v2.py:103` | `dt.strftime("%Y-%m-%dT%H:%M:%S.%f")` | Serialização de coluna de dados, com microssegundo |
| `test_pipeline.py:280` | `strftime("%d/%m")` | Saída da demo |
| `tests/supabase_falso.py:45`, `tests/test_supabase_auth.py:118,124,199,209` | `int(time.time())` | `iat`/`exp` de JWT — uso correto do relógio |
| `tests/test_payment_gateway.py:134,357`, `test_payment_isolation.py:68,328,437,534,641,864`, `test_pix_confirmacao.py:46`, `test_recovery_log.py:54`, `test_servir_v2.py:63`, `test_tenant_involuntary.py:40,278`, `test_webhook_security.py:37,97` | `int(time.time())` | Timestamp de **assinatura de webhook** — é o que a verificação de idade espera receber |

### Achado adjacente, fora do critério de timestamp (registrado, não corrigido)

`crai/integrations/hubspot_crm.py:63` — `f"sim_deal_{int(digest, 16) % 100000}"`.
Não deriva do relógio, mas é um id de agrupamento com espaço de apenas 100.000
valores: duas negociações distintas podem colidir por aniversário bem antes
disso. É do caminho simulado do HubSpot. **Não corrigi** — está fora do que
B.1/B.2 pedem; anotado aqui para não se perder.

## B.3 — o teste é determinístico? Falta semente?

**Não falta.** E a resposta não é "porque o Bloco A desligou a env": é mais
forte que isso.

Onde mora o `random.random() < prob`: em `track_outcome`
(`voluntary_agent.py:682`), nó que só existe na topologia de simulação. Com o
Bloco A, a suíte roda em produção por padrão, então a linha nem executa. Mas
isso seria uma resposta frágil — basta alguém ligar a env num teste para ela
voltar.

Então testei a **condição adversária**: modo simulação LIGADO, `_channel_history`
e bandit **sem nenhum isolamento**, 30 repetições das três requisições do teste.

Com o id novo:

```
modo_simulacao(): True
30 repeticoes; triplas distintas observadas: [('whatsapp', 'email', 'popup')]
todas iguais ao esperado ('whatsapp', 'email', 'popup') -> True
chaves em _channel_history ao fim: 65
```

As 65 chaves provam que os sorteios aconteceram e escreveram histórico — e o
resultado não mudou nenhuma vez, porque cada requisição tem id próprio.

O mesmo script, com **só** a linha 1839 revertida ao relógio:

```
30 repeticoes; triplas distintas observadas: [('whatsapp', 'email', 'email'),
                                              ('whatsapp', 'email', 'popup'),
                                              ('whatsapp', 'popup', 'popup')]
todas iguais ao esperado -> False
chaves em _channel_history ao fim: 7
```

Sete chaves para 90 requisições: é a fusão. E `('whatsapp','email','email')` é
literalmente o `assert 'email' == 'popup'` que falhava.

**Conclusão:** o determinismo vem do id, não do `rng` reposto nem da env
desligada — os dois ajudam, nenhum é o que sustenta. Não acrescentei semente ao
teste: seria esconder com sorte controlada um acoplamento que deixou de
existir. (O `rng` do bandit continua reposto pelo Bloco A, e é o que torna
determinística a *oferta* escolhida, coisa diferente do canal.)

## B.4 — o teste que reprova a volta do id por relógio

Novo arquivo `app/tests/test_id_de_lote.py`, 8 testes.

O instrumento é a fixture `relogio_parado`, que substitui `app_module.datetime`
por uma subclasse com `now()` fixo. Isso importa: sem congelar, um teste que só
exija "dois ids diferentes" passaria por acaso mesmo com o id antigo, porque
duas chamadas rápidas *podem* cair em segundos diferentes. Com o relógio
parado, qualquer id derivado dele vira constante e a reprovação é
determinística. Há inclusive uma catraca do próprio instrumento
(`test_o_relogio_falso_realmente_congela_a_rota`), para que o congelamento não
possa deixar de funcionar em silêncio.

Cobertura, nas duas direções que o enunciado pede:

| teste | o que trava |
|---|---|
| `test_dois_lotes_no_mesmo_instante_recebem_ids_diferentes` | relógio parado, dois ids da função → distintos |
| `test_o_mesmo_lote_id_atravessa_a_virada_do_segundo` | relógio **anda** 37 s entre as chamadas, `lote_id` igual → mesmo id |
| `test_lote_id_de_fora_e_saneado` | injeção pelo `lote_id`, truncagem em 48, lixo puro cai no sorteio |
| `test_duas_chamadas_no_mesmo_instante_sao_dois_clientes` | ponta a ponta: duas linhas de ciclo com `user_id` distintos |
| `test_com_lote_id_explicito_as_chamadas_ficam_juntas` | ponta a ponta, relógio andando 41 s → mesmo `user_id` |
| `test_a_cobranca_falhada_segue_a_mesma_regra` | a outra rota, observada nos `thread_id` do checkpoint do involuntário |

**Reprovação verificada, reintroduzindo o defeito de propósito.** Nas duas
formas em que ele poderia voltar:

Revertendo os dois pontos de uso para o relógio:

```
FAILED test_id_de_lote.py::TestRotaDoPainelNaoAgrupaPeloRelogio::test_duas_chamadas_no_mesmo_instante_sao_dois_clientes
FAILED test_id_de_lote.py::TestRotaDoPainelNaoAgrupaPeloRelogio::test_com_lote_id_explicito_as_chamadas_ficam_juntas
FAILED test_id_de_lote.py::TestRotaDoPainelNaoAgrupaPeloRelogio::test_a_cobranca_falhada_segue_a_mesma_regra
3 failed, 5 passed in 5.74s
```

Fazendo o **próprio `id_de_lote`** voltar ao relógio (o sorteio trocado por
`int(datetime.now().timestamp())`):

```
FAILED test_id_de_lote.py::TestIdDeLote::test_dois_lotes_no_mesmo_instante_recebem_ids_diferentes
FAILED test_id_de_lote.py::TestIdDeLote::test_lote_id_de_fora_e_saneado
FAILED test_id_de_lote.py::TestRotaDoPainelNaoAgrupaPeloRelogio::test_duas_chamadas_no_mesmo_instante_sao_dois_clientes
FAILED test_id_de_lote.py::TestRotaDoPainelNaoAgrupaPeloRelogio::test_com_lote_id_explicito_as_chamadas_ficam_juntas
FAILED test_id_de_lote.py::TestRotaDoPainelNaoAgrupaPeloRelogio::test_a_cobranca_falhada_segue_a_mesma_regra
5 failed, 3 passed in 5.60s
```

Restaurado em seguida: `8 passed`.

## PORTÃO B

- [x] **10 execuções seguidas da raiz `E:\Crai`, todas verdes, 0 skipped:**

```
1477 passed in 51.39s    1477 passed in 52.09s
1477 passed in 51.68s    1477 passed in 51.42s
1477 passed in 52.26s    1477 passed in 51.25s
1477 passed in 51.80s    1477 passed in 52.37s
1477 passed in 51.66s    1477 passed in 52.33s
```

(1469 → 1477: os 8 testes novos do B.4.)

- [x] o teste do B.4 reprova quando o id por relógio volta — verificado nas
      duas formas, acima
- [x] `crai/churn_voluntary/retention_log.py` **intocado**

---

# Estado do working tree

```
 M .gitignore                                          (já estava, antes destes blocos)
 M app/README.md                                       (tarefa anterior: §4.6)
 M app/crai/api/app.py                                 B.1
 M app/test_pipeline.py                                A.1
 M app/tests/conftest.py                               A.2 + A.3
 M app/tests/test_populacao_compartilhada.py           (tarefa anterior)
?? app/tests/test_id_de_lote.py                        B.4
?? docs/evidencia_base_v2/curva_limiar_anomalia_v2_final_27_09_p81.json  (tarefa anterior)
```

Nada commitado, nada no índice.

# O que fica em aberto

1. **O `MemorySaver` do `crai_agent`** (pipeline involuntário) continua sem
   ninguém que o limpe entre testes — é a mesma dívida que A.3 fechou para os
   grafos do voluntário. Deixei de fora porque o Bloco A escopava o pipeline
   voluntário; `test_a_cobranca_falhada_segue_a_mesma_regra` contorna comparando
   o que *aquelas* duas chamadas acrescentaram, em vez do conteúdo inteiro.
2. **`docs/CONTRATO_PAINEL.md`** não documenta o campo opcional `lote_id`. Não
   há teste que confira o contrato contra as rotas, então nada reprova — mas o
   documento está incompleto a partir de agora.
3. **Os 8 ciclos `demo_tenant`** em `app/data/retention_cycles.db`, criados
   pelas minhas duas rodadas de verificação da demo (ver A.4).
4. **`hubspot_crm.py:63`**, o id de negociação com 100.000 valores possíveis
   (ver B.2).
5. **N-12** continua vivo e apareceu duas vezes durante este trabalho: o
   `✅`/`❌` de `track_outcome` derruba a rota com `UnicodeEncodeError` quando o
   stdout é um console cp1252 (`pytest -s`, ou script sem
   `PYTHONIOENCODING=utf-8`). Não é regressão — é a dívida já declarada no
   `app/README.md`.
