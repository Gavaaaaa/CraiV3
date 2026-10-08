"""crai/scripts/semear_demo.py - dados de exemplo para o dashboard.

Cria ciclos SINTETICOS da empresa ficticia do login de desenvolvimento, pelas rotas da
propria API. Uso (a partir de app/, com o backend no ar em ENV=development; o passo a passo
esta no README.md da raiz):

    python -m crai.scripts.semear_demo
    python -m crai.scripts.semear_demo --api http://127.0.0.1:8000
    python -m crai.scripts.semear_demo --empresa demo_testes

DUAS EMPRESAS FICTICIAS. Sem `--empresa`, a semente vai para `demo_dashboard`, a da
demonstracao (a que aparece ao abrir o dashboard). Com `--empresa demo_testes`, vai para a
empresa dos testes ao vivo (`npm run test:vivo`), que geram chaves, escolhem mensagens e
gravam configuracao: assim os testes nao sujam a demonstracao.

O que ele faz, so pelas rotas que ja existem (nenhum acesso direto a banco):

  - pede o token de desenvolvimento (POST /dev/token, papel owner);
  - cadastra clientes ficticios na base da empresa (POST /clientes/lote);
  - simula cobrancas Pix que falharam (POST /simulate/pix-falhado) e uma que foi paga
    (POST /simulate/pix-pago), trocando o modo de mensagem pela rota de configuracao
    (PUT /configuracao) para chegar a cada situacao;
  - deixa a configuracao da empresa no padrao (modo escolha, 8 h, janela das 8 h as 20 h);
  - avisa dois eventos de comportamento (POST /eventos, Rodada 3): dois clientes ficticios
    abriram a pagina de cancelamento. O sistema avalia o risco e, se for o caso, faz uma
    oferta a cada um (a pagina Voluntario mostra). O aceite nao e semeado: ele so chega pelo
    webhook de desfecho, que exige um segredo proprio.

Situacoes criadas a cada execucao (os ids levam um sufixo novo, entao rodar de novo
acrescenta ciclos, nao repete):

  Em analise ................. cobranca com saldo insuficiente, tentativas agendadas
  Em processo ................ mensagem enviada (modo automatico), cliente com telefone
  Em processo ................ aguardando a escolha (modo escolha), cliente com telefone
  Em processo ................ aguardando a escolha (modo escolha), cliente com e-mail
  Em processo ................ sem canal: cliente fora da base, mensagem nao entregavel
  Recuperado ................. cobranca que falhou e foi paga depois
  Encerrado sem recuperacao .. cobranca de R$ 0,01, que o sistema descarta (nao vale a acao)

LGPD: tudo aqui e inventado. Nomes de fantasia, telefones do intervalo ficticio
+55 11 90000-00xx e e-mails @exemplo.com.br. Nada disso identifica uma pessoa real.

Este arquivo so tem caracteres ASCII (catraca N-12).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

EMPRESA_DA_DEMONSTRACAO = "demo_dashboard"
EMPRESA_DOS_TESTES = "demo_testes"
EMPRESAS = (EMPRESA_DA_DEMONSTRACAO, EMPRESA_DOS_TESTES)
CODIGO_SALDO = "AM04"        # saldo insuficiente: o sistema agenda novas tentativas
CODIGO_REVOGADA = "MD01"     # autorizacao revogada: nao ha nova tentativa, vai direto a mensagem
CONFIG_PADRAO = {"modo_mensagem_involuntario": "escolha", "prazo_escolha_horas": 8,
                 "janela_contato_inicio": "08:00", "janela_contato_fim": "20:00",
                 "canais_permitidos": ["whatsapp", "email"]}
JANELA_ABERTA = {"janela_contato_inicio": "00:00", "janela_contato_fim": "24:00"}
# Com R$ 0,01 o retorno esperado fica abaixo do custo da acao para QUALQUER cliente, e o
# sistema descarta o ciclo. Com R$ 0,05 dependia do perfil sorteado para o cliente: descartava
# quando a chance de recuperar ficava abaixo de uns 65% (medido em 04/10/2026: um cliente com
# chance de 75% ficou em analise, com retorno esperado de R$ 0,03).
VALOR_QUE_O_SISTEMA_DESCARTA = 0.01


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def chamar(self, metodo, caminho, corpo=None, com_token=True):
        dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
        req = urllib.request.Request(self.base + caminho, data=dados, method=metodo)
        if dados is not None:
            req.add_header("Content-Type", "application/json")
        if com_token and self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(texto)
            except ValueError:
                return e.code, {"detail": texto[:300]}

    def exigir(self, metodo, caminho, corpo=None, com_token=True):
        status, resposta = self.chamar(metodo, caminho, corpo, com_token)
        if status != 200:
            raise SystemExit("[SEMENTE] %s %s respondeu %s: %s" % (
                metodo, caminho, status, json.dumps(resposta, ensure_ascii=True)[:400]))
        return resposta


def entrar(api, empresa):
    corpo = {"papel": "owner"}
    if empresa != EMPRESA_DA_DEMONSTRACAO:
        corpo["empresa"] = empresa
    try:
        status, resposta = api.chamar("POST", "/dev/token", corpo, com_token=False)
    except urllib.error.URLError as e:
        raise SystemExit("[SEMENTE] Backend fora do ar em %s (%s). Suba o backend antes." % (api.base, e.reason))
    if status == 404:
        raise SystemExit("[SEMENTE] POST /dev/token nao existe neste servidor. "
                         "O backend precisa subir com ENV=development.")
    if status != 200:
        raise SystemExit("[SEMENTE] POST /dev/token respondeu %s: %s" % (status, resposta))
    api.token = resposta["token"]
    if resposta.get("tenant_id") != empresa:
        raise SystemExit("[SEMENTE] Empresa inesperada no token: %r" % resposta.get("tenant_id"))
    return resposta["tenant_id"]


def cadastrar_clientes(api, clientes):
    """Clientes ficticios na base da empresa, ligados a recorrencia de cada cobranca."""
    resposta = api.exigir("POST", "/clientes/lote", {"clientes": clientes})
    rejeitados = resposta.get("rejeitados") or []
    if rejeitados:
        raise SystemExit("[SEMENTE] Clientes rejeitados: %s" % json.dumps(rejeitados, ensure_ascii=True)[:400])
    return resposta


def falhar(api, tenant, recorrencia, valor, codigo):
    api.exigir("POST", "/simulate/pix-falhado",
               {"id_recorrencia": recorrencia, "valor": valor, "codigo_falha": codigo,
                "tenant_id": tenant}, com_token=False)


def pagar(api, tenant, recorrencia, valor):
    return api.exigir("POST", "/simulate/pix-pago",
                      {"id_recorrencia": recorrencia, "valor": valor, "tenant_id": tenant},
                      com_token=False)


def configurar(api, **chaves):
    api.exigir("PUT", "/configuracao", chaves)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cria ciclos sinteticos para o dashboard.")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="endereco do backend local")
    ap.add_argument("--sufixo", default=None, help="sufixo dos ids (padrao: o horario)")
    ap.add_argument("--empresa", default=EMPRESA_DA_DEMONSTRACAO, choices=EMPRESAS,
                    help="empresa ficticia: a da demonstracao (padrao) ou a dos testes ao vivo")
    args = ap.parse_args(argv)

    api = Api(args.api)
    tenant = entrar(api, args.empresa)
    s = args.sufixo or time.strftime("%m%d%H%M%S")
    # Os ids da empresa dos testes levam outro prefixo: nunca se confundem com os da demonstracao.
    marca = "demo" if tenant == EMPRESA_DA_DEMONSTRACAO else "teste"
    rec = {nome: "RN_%s_%s_%s" % (marca, nome, s) for nome in (
        "analise", "enviada", "escolha_tel", "escolha_email", "sem_canal", "recuperado", "descartado")}
    print("[SEMENTE] Empresa %s, sufixo %s" % (tenant, s))

    # Dias sem entrar e funcionalidades usadas em 30 dias, por cliente: com eles a pagina
    # Voluntario avalia o risco (sem eles, o cliente fica como "sem dado suficiente").
    uso = {"analise": (2, 9), "enviada": (41, 0), "escolha_tel": (24, 1),
           "escolha_email": (3, 7), "recuperado": (15, 3), "descartado": (60, 0)}

    def cliente(chave, nome, **contato):
        linha = {"customer_id_externo": "cli_%s_%s_%s" % (marca, chave, s), "mrr": 1290.0,
                 "billing_profile": "PJ", "id_recorrencia": rec[chave], "nome": nome,
                 "days_since_last": uso[chave][0], "features_used_30d": uso[chave][1]}
        linha.update(contato)
        return linha

    # O cliente de "sem_canal" fica de fora da base de proposito.
    cadastrar_clientes(api, [
        cliente("analise", "Studio Vetor (fict\u00edcio)", telefone="+5511900000011"),
        cliente("enviada", "Padaria Modelo (fict\u00edcia)", telefone="+5511900000012"),
        cliente("escolha_tel", "Cl\u00ednica Horizonte (fict\u00edcia)", telefone="+5511900000013"),
        cliente("escolha_email", "Escola Aurora (fict\u00edcia)", email="financeiro.aurora@exemplo.com.br"),
        cliente("recuperado", "Ag\u00eancia Norte (fict\u00edcia)", telefone="+5511900000015"),
        cliente("descartado", "Mercado Sol (fict\u00edcio)", telefone="+5511900000016"),
    ])
    print("[SEMENTE] 6 clientes ficticios cadastrados na base da empresa")

    # 1) Modo automatico, janela o dia todo: a mensagem sai na hora.
    configurar(api, modo_mensagem_involuntario="automatico", **JANELA_ABERTA)
    falhar(api, tenant, rec["enviada"], 349.90, CODIGO_REVOGADA)

    # 2) Modo escolha: as mensagens esperam a escolha da empresa.
    configurar(api, modo_mensagem_involuntario="escolha")
    falhar(api, tenant, rec["escolha_tel"], 4900.00, CODIGO_REVOGADA)
    falhar(api, tenant, rec["escolha_email"], 1890.00, CODIGO_REVOGADA)
    falhar(api, tenant, rec["sem_canal"], 799.00, CODIGO_REVOGADA)

    # 3) Saldo insuficiente: tentativas agendadas (em analise), uma paga (recuperado) e uma
    #    de valor baixo demais para valer a acao (encerrada sem recuperacao).
    falhar(api, tenant, rec["analise"], 1290.00, CODIGO_SALDO)
    falhar(api, tenant, rec["recuperado"], 3200.00, CODIGO_SALDO)
    pago = pagar(api, tenant, rec["recuperado"], 3200.00)
    falhar(api, tenant, rec["descartado"], VALOR_QUE_O_SISTEMA_DESCARTA, CODIGO_SALDO)

    # 4) A configuracao volta ao padrao da empresa.
    configurar(api, **CONFIG_PADRAO)

    # 5) Voluntario (Rodada 3): dois clientes ficticios abriram a pagina de cancelamento. O
    #    evento entra por POST /eventos, o mesmo caminho do servidor de uma empresa. O
    #    messageId leva o sufixo: rodar a semente de novo nao repete o mesmo evento, e o
    #    limite de contato (uma oferta por cliente a cada 30 dias) vale por cliente.
    avisados = 0
    for chave in ("enviada", "escolha_tel"):
        resposta = api.exigir("POST", "/eventos", {
            "userId": "cli_%s_%s_%s" % (marca, chave, s), "event": "Cancellation Page Viewed",
            "properties": {"mrr": 1290.0, "billing_profile": "PJ"},
            "messageId": "semente_%s_%s" % (chave, s)})
        avisados += 0 if resposta.get("duplicado") else 1
    print("[SEMENTE] %d eventos de comportamento avisados (POST /eventos)" % avisados)

    lista = api.exigir("GET", "/ciclos?limite=200")["ciclos"]
    desta_execucao = {c["id_recorrencia"]: c for c in lista if c["id_recorrencia"].endswith("_" + s)}
    print("[SEMENTE] Ciclos desta execucao:")
    faltando = []
    for nome in ("analise", "enviada", "escolha_tel", "escolha_email", "sem_canal", "recuperado", "descartado"):
        c = desta_execucao.get(rec[nome])
        if c is None:
            faltando.append(nome)
            print("  %-14s NAO CRIADO" % nome)
            continue
        print("  %-14s id %-4s status %-26s estado %s" % (nome, c["id"], c["status"], c["estado"]))
    por_status = {}
    for c in lista:
        por_status[c["status"]] = por_status.get(c["status"], 0) + 1
    print("[SEMENTE] Total da empresa: %d ciclos %s" % (len(lista), json.dumps(por_status, sort_keys=True)))
    print("[SEMENTE] Confirmacao do pagamento: %s" % json.dumps(
        {k: pago.get(k) for k in ("status", "ciclo") if k in pago}, ensure_ascii=True))

    esperado = {"analise": "em_analise", "enviada": "em_processo", "escolha_tel": "em_processo",
                "escolha_email": "em_processo", "sem_canal": "em_processo",
                "recuperado": "recuperado", "descartado": "encerrado_sem_recuperacao"}
    diferentes = [(n, desta_execucao[rec[n]]["status"]) for n in esperado
                  if rec[n] in desta_execucao and desta_execucao[rec[n]]["status"] != esperado[n]]
    if faltando or diferentes:
        print("[SEMENTE] ATENCAO: nao criados %s; com status diferente do esperado %s" % (faltando, diferentes))
        return 1
    if tenant == EMPRESA_DA_DEMONSTRACAO:
        print("[SEMENTE] Pronto. Abra o dashboard em http://localhost:5173/involuntario")
    else:
        print("[SEMENTE] Pronto. Empresa dos testes semeada: rode npm run test:vivo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
