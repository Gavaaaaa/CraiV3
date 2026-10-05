"""tests/test_rodada4_tela_e_operacao.py - Rodada 4, Fase 2: pendencias de tela e de operacao.

O QUE ESTE ARQUIVO MEDE (o lado do backend de cada pendencia):

  - a rota do mes devolve a PROXIMA ACAO do sistema (o que e quando);
  - `GET /ciclos?aguardando_escolha=true` e a lista do sino, e bate com o numero;
  - `GET /extrato/csv`: o extrato em arquivo, so para dono e administrador, no
    registro de acesso;
  - `GET /clientes/recentes` diz quem esta marcado como "nao contatar", e
    devolve um cliente pelo id;
  - `GET /busca`: clientes e ciclos da empresa, sem contato, sem vazar entre
    empresas;
  - `POST /clientes/importar` exige dono ou administrador;
  - os dois expurgos novos do relogio diario: os ciclos antigos sao
    anonimizados mantendo os agregados, e a simulacao parada e apagada.
"""

import os
import sqlite3
from datetime import timedelta

import pytest

from crai import ambiente, simulador
from crai.api import busca, datas, registro_acesso, relogio
from crai.api import visao_geral as vg
from crai.churn_voluntary import clientes_importados as ci
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao, recovery_log
from tests.test_visao_geral_api import (  # noqa: F401 - as fixtures entram pelo nome
    A, B, CONTATOS, _base, _chaves, _ciclo, _get, _mensagem, _recuperado_na_tentativa, _tentar,
    cenario, cliente)


def _agora():
    return datas.agora_local().replace(microsecond=0)


def _agendar(ciclo_id, numero, quando, valor=200.0):
    cc.agendar_tentativas(ciclo_id, [{"numero": numero, "quando": quando, "valor": valor}], _agora())


def _esperando_escolha(tenant, rec, horas_atras=1, escolhida=False):
    """Um ciclo com as 3 sugestoes gravadas, esperando a escolha (ou ja escolhido, esperando a janela)."""
    ciclo_id, _ = _ciclo(tenant, rec, 90.0, dias_atras=1)
    quando = _agora() - timedelta(hours=horas_atras)
    cc.transicionar(ciclo_id, cc.AGUARDANDO_ESCOLHA, quando)
    sugestoes = [{"abordagem": a, "texto": f"texto {a}", "recomendada": a == "lembrete_cordial",
                  "origem_texto": "template", "codigo_template": "t"} for a in cc.ABORDAGENS]
    cc.gravar_rodada(ciclo_id, sugestoes, "whatsapp", "contato_da_base", "insufficient_funds",
                     "pix_automatico", "cordial", quando)
    if escolhida:
        cc.registrar_escolha(ciclo_id, 1, "lembrete_cordial", "owner", quando)
    return ciclo_id


def _mes(cliente, tenant=A, **kw):
    r = _get(cliente, tenant, "/metrics/involuntario/mes", **kw)
    assert r.status_code == 200, r.text
    return r.json()


# == A proxima acao do sistema =============================================

class TestProximaAcao:
    def test_sem_nada_agendado_e_nulo(self, cliente):
        assert _mes(cliente)["proxima_acao"] is None
        _recuperado_na_tentativa(A, "RN_fechado", 1, dias_atras=3)
        assert _mes(cliente)["proxima_acao"] is None, "ciclo fechado nao tem proxima acao"

    def test_e_a_tentativa_agendada_mais_proxima(self, cliente):
        agora = _agora()
        longe, _ = _ciclo(A, "RN_longe", dias_atras=1)
        _agendar(longe, 1, agora + timedelta(days=4))
        perto, _ = _ciclo(A, "RN_perto", dias_atras=1)
        _agendar(perto, 1, agora + timedelta(days=2))
        _agendar(perto, 2, agora + timedelta(days=5))
        assert cc.ciclo_por_id(perto)["estado"] == cc.RECOBRANDO
        assert _mes(cliente)["proxima_acao"] == {
            "quando": datas.iso_com_fuso(agora + timedelta(days=2)), "tipo": "tentativa",
            "descricao": "Tentativa 1 de cobrança", "ciclo_id": perto}

    def test_tentativa_ja_disparada_nao_e_a_proxima(self, cliente):
        agora = _agora()
        ciclo_id, aberto = _ciclo(A, "RN_dois", dias_atras=2)
        _tentar(ciclo_id, 1, cc.FALHOU, aberto + timedelta(hours=1))
        _agendar(ciclo_id, 2, agora + timedelta(days=1))
        proxima = _mes(cliente)["proxima_acao"]
        assert proxima["descricao"] == "Tentativa 2 de cobrança" and proxima["ciclo_id"] == ciclo_id

    def test_ciclo_esperando_a_escolha_o_envio_automatico_no_fim_do_prazo(self, cliente):
        configuracao.gravar(A, {"modo_mensagem_involuntario": configuracao.MODO_ESCOLHA,
                                "prazo_escolha_horas": 8})
        ciclo_id = _esperando_escolha(A, "RN_espera", horas_atras=1)
        config = configuracao.ler(A)
        proxima = _mes(cliente)["proxima_acao"]
        assert proxima["tipo"] == "mensagem" and proxima["ciclo_id"] == ciclo_id
        assert proxima["descricao"] == "Envio automático da mensagem recomendada"
        quando = datas.com_fuso(proxima["quando"])
        limite = datas.com_fuso(_agora() + timedelta(hours=config["prazo_escolha_horas"] - 1))
        assert quando >= limite - timedelta(seconds=5), "nunca antes de o prazo de escolha vencer"

    def test_mensagem_ja_escolhida_esperando_a_janela(self, cliente):
        ciclo_id = _esperando_escolha(A, "RN_escolhida", escolhida=True)
        proxima = _mes(cliente)["proxima_acao"]
        assert (proxima["tipo"], proxima["descricao"], proxima["ciclo_id"]) == (
            "mensagem", "Envio da mensagem escolhida", ciclo_id)
        assert datas.com_fuso(proxima["quando"]) >= datas.com_fuso(_agora()) - timedelta(seconds=5)

    def test_no_modo_automatico_e_o_envio_da_recomendada(self, cliente):
        configuracao.gravar(A, {"modo_mensagem_involuntario": configuracao.MODO_AUTOMATICO})
        _esperando_escolha(A, "RN_auto")
        assert _mes(cliente)["proxima_acao"]["descricao"] == "Envio da mensagem recomendada"

    def test_entre_tentativa_e_mensagem_vence_a_mais_proxima(self, cliente):
        agora = _agora()
        _esperando_escolha(A, "RN_espera")
        com_tentativa, _ = _ciclo(A, "RN_t", dias_atras=1)
        _agendar(com_tentativa, 1, agora - timedelta(minutes=5))        # ja devida
        assert _mes(cliente)["proxima_acao"]["ciclo_id"] == com_tentativa
        assert _mes(cliente)["proxima_acao"]["tipo"] == "tentativa"

    def test_e_so_da_empresa_do_token_e_nao_traz_dado_do_cliente(self, cliente):
        _base(A, ("c-ana", 500.0, 3, 4, {"nome": "Ana Prado", "id_recorrencia": "RN_ana",
                                         "email": CONTATOS[0], "telefone": "+" + CONTATOS[1]}))
        ciclo_id, _ = _ciclo(A, "RN_ana", dias_atras=1)
        _agendar(ciclo_id, 1, _agora() + timedelta(days=1))
        assert _mes(cliente, B)["proxima_acao"] is None
        proxima = _mes(cliente, A)["proxima_acao"]
        assert set(proxima) == {"quando", "tipo", "descricao", "ciclo_id"}
        texto = _get(cliente, A, "/metrics/involuntario/mes").text
        for proibido in ("Ana Prado", "RN_ana", *CONTATOS):
            assert proibido not in texto

    def test_o_resto_da_resposta_do_mes_nao_mudou(self, cenario):
        corpo = _mes(cenario)
        assert set(corpo) == {"mes", "inicio", "fim", "valor_liquido_recuperado", "recuperados",
                              "encerrados_sem_recuperacao", "taxa_recuperacao", "estornos",
                              "ciclos_abertos_no_mes", "aguardando_escolha", "proxima_acao"}


# == O sino: a lista dos ciclos que esperam a escolha =======================

class TestListaDoSino:
    def test_a_lista_bate_com_o_numero_do_mes(self, cliente):
        esperando = {_esperando_escolha(A, f"RN_e{i}") for i in range(3)}
        _esperando_escolha(A, "RN_ja_escolhida", escolhida=True)
        _recuperado_na_tentativa(A, "RN_outro", 1, dias_atras=2)
        _esperando_escolha(B, "RN_da_b")
        r = _get(cliente, A, "/ciclos", aguardando_escolha="true").json()
        assert {c["id"] for c in r["ciclos"]} == esperando
        assert all(c["estado"] == cc.AGUARDANDO_ESCOLHA for c in r["ciclos"])
        assert _mes(cliente)["aguardando_escolha"] == len(esperando) == 3
        assert len(_get(cliente, A, "/ciclos").json()["ciclos"]) == 5, "sem o filtro, todos"
        da_b = _get(cliente, B, "/ciclos", aguardando_escolha="true").json()["ciclos"]
        assert len(da_b) == 1 and da_b[0]["id_recorrencia"] == "RN_da_b"

    def test_sem_ninguem_esperando_a_lista_e_vazia(self, cliente):
        _recuperado_na_tentativa(A, "RN_outro", 1, dias_atras=2)
        assert _get(cliente, A, "/ciclos", aguardando_escolha="true").json()["ciclos"] == []
        assert _mes(cliente)["aguardando_escolha"] == 0

    def test_o_filtro_combina_com_a_busca(self, cliente):
        _esperando_escolha(A, "RN_abc")
        _esperando_escolha(A, "RN_xyz")
        r = _get(cliente, A, "/ciclos", aguardando_escolha="true", q="RN_ab").json()["ciclos"]
        assert [c["id_recorrencia"] for c in r] == ["RN_abc"]


# == O extrato em CSV =======================================================

def _linhas_do_csv(texto: str) -> list:
    assert texto.startswith(vg.MARCA_DE_UTF8)
    return [l.split(";") for l in texto[1:].split("\r\n") if l]


class TestExtratoEmCsv:
    def test_dono_e_administrador_baixam_o_mesmo_extrato_da_tela(self, cenario):
        em_json = _get(cenario, A, "/extrato").json()
        for papel in ("owner", "admin"):
            r = _get(cenario, A, "/extrato/csv", papel=papel)
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "text/csv; charset=utf-8"
            assert r.headers["content-disposition"] == f'attachment; filename="crai-extrato-{em_json["mes"]}.csv"'
            linhas = _linhas_do_csv(r.text)
            assert tuple(linhas[0]) == vg.COLUNAS_DO_CSV
            assert len(linhas) == len(em_json["linhas"]) + 2, "o cabecalho, as linhas e o total"
            total = linhas[-1]
            assert total[1] == "Total"
            numero = lambda t: float(t.replace(",", "."))                   # noqa: E731
            assert numero(total[5]) == em_json["totais"]["valor_base"]
            assert numero(total[6]) == em_json["totais"]["fee"]
            assert numero(total[7]) == em_json["totais"]["liquido"]
            for linha_csv, linha in zip(linhas[1:-1], em_json["linhas"]):
                assert numero(linha_csv[7]) == linha["liquido"]
                assert linha_csv[2] == linha["id_cliente"]
            assert all("." not in l[5] and "." not in l[7] for l in linhas[1:]), "virgula decimal"

    def test_membro_recebe_403_e_sem_token_401(self, cenario):
        r = _get(cenario, A, "/extrato/csv", papel="membro")
        assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert cenario.get("/extrato/csv").status_code == 401
        assert "Taxa da CRAI" not in r.text

    def test_entra_no_registro_de_acesso_com_o_nome_proprio(self, cenario):
        _get(cenario, A, "/extrato/csv", papel="admin")
        _get(cenario, A, "/extrato/csv", papel="membro")
        registrados = [(a["rota"], a["papel"]) for a in registro_acesso.acessos(A)]
        assert registrados.count((registro_acesso.ROTA_EXTRATO_CSV, "admin")) == 1
        assert (registro_acesso.ROTA_EXTRATO_CSV, "membro") not in registrados, "o 403 nao leu nada"
        assert registro_acesso.ROTA_EXTRATO_CSV == "GET /extrato/csv"

    def test_e_so_da_empresa_do_token_e_nao_tem_contato(self, cenario):
        da_b = _linhas_do_csv(_get(cenario, B, "/extrato/csv").text)
        assert len(da_b) == 2 and da_b[1][5:8] == ["0,00", "0,00", "0,00"]
        texto = _get(cenario, A, "/extrato/csv").text
        assert "Ana Prado" in texto
        for contato in CONTATOS:
            assert contato not in texto

    def test_o_mes_pedido_e_o_do_arquivo(self, cenario):
        r = _get(cenario, A, "/extrato/csv", mes="2024-02")
        assert r.status_code == 200 and "crai-extrato-2024-02.csv" in r.headers["content-disposition"]
        assert len(_linhas_do_csv(r.text)) == 2
        assert _get(cenario, A, "/extrato/csv", mes="fevereiro").status_code == 422

    def test_nome_que_parece_formula_nao_e_executado_pela_planilha(self, cliente):
        _base(A, ("c-mal", 500.0, 3, 4, {"nome": "=HYPERLINK(\"http://x\";\"clique\")",
                                         "id_recorrencia": "RN_mal"}))
        _recuperado_na_tentativa(A, "RN_mal", 1, valor=200.0, dias_atras=0)
        texto = _get(cliente, A, "/extrato/csv").text
        assert "'=HYPERLINK" in texto and "\r\n=HYPERLINK" not in texto and ";=HYPERLINK" not in texto

    @pytest.mark.parametrize("valor, esperado", [
        (1234.5, "1234,50"), (-85.0, "-85,00"), (0, "0,00"), (True, "Sim"), (False, "Não"),
        (None, ""), ("Ana", "Ana"), ("a;b", '"a;b"'), ('diz "oi"', '"diz ""oi"""'),
        ("=1+1", "'=1+1"), ("+55", "'+55"), ("-x", "'-x"), ("@a", "'@a"), ("linha\nnova", '"linha\nnova"')])
    def test_a_celula(self, valor, esperado):
        assert vg._celula_do_csv(valor) == esperado


# == A lista de clientes: quem pediu para nao ser contatado =================

class TestNaoContatarNaLista:
    def _pedir(self, http, tenant=A, **params):
        # `cliente` e o nome de um parametro da rota: por isso o TestClient aqui se chama `http`.
        return http.get("/clientes/recentes", params=params,
                        headers=http.projeto.bearer(tenant, papel="owner", plano="premium"))

    def _clientes(self, http, tenant=A, **params):
        r = self._pedir(http, tenant, **params)
        assert r.status_code == 200, r.text
        return {c["id"]: c for c in r.json()["clientes"]}

    def test_a_lista_diz_quem_esta_marcado(self, cliente):
        _base(A, ("c-1", 500.0, 3, 4, {"nome": "Ana Prado"}), ("c-2", 300.0, 9, 1, {"nome": "Bia Lemos"}))
        ci.marcar_nao_contatar(A, "c-2", ci.ORIGEM_RESPOSTA_SAIR)
        lista = self._clientes(cliente)
        assert lista["c-1"]["nao_contatar"] is False and lista["c-2"]["nao_contatar"] is True
        ci.desmarcar_nao_contatar(A, "c-2")
        assert self._clientes(cliente)["c-2"]["nao_contatar"] is False

    def test_a_marca_de_outra_empresa_nao_aparece(self, cliente):
        _base(A, ("c-1", 500.0, 3, 4, {}))
        _base(B, ("c-1", 500.0, 3, 4, {}))
        ci.marcar_nao_contatar(B, "c-1", ci.ORIGEM_EMPRESA)
        assert self._clientes(cliente, A)["c-1"]["nao_contatar"] is False
        assert self._clientes(cliente, B)["c-1"]["nao_contatar"] is True

    def test_um_cliente_pelo_id_mesmo_fora_dos_mais_recentes(self, cliente):
        _base(A, *[(f"c-{i:02d}", 100.0 + i, i, 3, {"nome": f"Cliente {i}"}) for i in range(15)])
        assert len(self._clientes(cliente, limite=1)) == 1
        for cid in ("c-00", "c-07", "c-14"):
            so_ele = self._clientes(cliente, limite=1, cliente=cid)
            assert set(so_ele) == {cid} and so_ele[cid]["nome"].startswith("Cliente")
            assert so_ele[cid]["posicao_no_ranking"] is not None

    def test_id_de_outra_empresa_e_igual_ao_que_nao_existe(self, cliente):
        _base(A, ("c-a", 500.0, 3, 4, {"nome": "Ana Prado"}))
        _base(B, ("c-b", 500.0, 3, 4, {"nome": "Bia Lemos"}))
        de_fora = self._pedir(cliente, A, cliente="c-b")
        inexistente = self._pedir(cliente, A, cliente="nao-existe")
        assert de_fora.status_code == inexistente.status_code == 200
        assert de_fora.json()["clientes"] == inexistente.json()["clientes"] == []
        assert "Bia Lemos" not in de_fora.text


# == A busca do topo ========================================================

class TestBusca:
    @pytest.fixture
    def base(self, cliente):
        _base(A,
              ("cli-ana", 500.0, 3, 4, {"nome": "Ana Prado", "id_recorrencia": "RN_ana",
                                        "email": CONTATOS[0], "telefone": "+" + CONTATOS[1]}),
              ("cli-bia", 300.0, 9, 1, {"nome": "Bia Prado Lemos", "id_recorrencia": "RN_bia"}),
              ("cli-caio", 100.0, 0, 9, {"nome": "Caio Dias"}))
        _base(B, ("cli-dora", 700.0, 2, 5, {"nome": "Dora Prado", "id_recorrencia": "RN_dora"}))
        self.ciclo_ana, _ = _ciclo(A, "RN_ana", dias_atras=2)
        self.ciclo_solto, _ = _ciclo(A, "RN_solto", dias_atras=1)
        self.ciclo_dora, _ = _ciclo(B, "RN_dora", dias_atras=1)
        return cliente

    def _buscar(self, cliente, q, tenant=A, **kw):
        r = _get(cliente, tenant, "/busca", q=q, **kw)
        assert r.status_code == 200, r.text
        return r.json()

    def test_pelo_nome_sem_distinguir_maiuscula(self, base):
        r = self._buscar(base, "prado")
        assert [c["id"] for c in r["clientes"]] == ["cli-ana", "cli-bia"]
        assert r["clientes"][0] == {"id": "cli-ana", "nome": "Ana Prado", "mrr": 500.0,
                                    "cancelado": False, "nao_contatar": False}
        assert [c["id"] for c in r["ciclos"]] == [self.ciclo_ana], "o ciclo do cliente achado pelo nome"
        assert r["ciclos"][0]["cliente_nome"] == "Ana Prado"
        assert r["q"] == "prado" and r["limite"] == busca.LIMITE

    def test_pelo_id_do_cliente_e_pelo_id_da_recorrencia(self, base):
        assert [c["id"] for c in self._buscar(base, "cli-c")["clientes"]] == ["cli-caio"]
        r = self._buscar(base, "RN_so")
        assert r["clientes"] == [] and [c["id"] for c in r["ciclos"]] == [self.ciclo_solto]
        r = self._buscar(base, "rn_an")                                    # o id da recorrencia, na base
        assert [c["id"] for c in r["clientes"]] == ["cli-ana"]

    def test_nenhum_contato_e_nenhuma_fee_na_resposta(self, base):
        r = _get(base, A, "/busca", q="Ana")
        for contato in CONTATOS:
            assert contato not in r.text
        chaves = set(_chaves(r.json()))
        assert not {"email", "telefone", "phone", "cpf", "chave_pix", "fee"} & chaves
        assert chaves == {"q", "clientes", "ciclos", "limite", "id", "nome", "mrr", "cancelado",
                          "nao_contatar", "id_recorrencia", "cliente_nome", "status", "estado",
                          "valor_cobranca", "causa_legivel", "atualizado_em"}

    def test_uma_empresa_nao_acha_nada_de_outra(self, base):
        de_fora = self._buscar(base, "Dora")
        inexistente = self._buscar(base, "Zuleica")
        assert de_fora == {**inexistente, "q": "Dora"}
        assert de_fora["clientes"] == [] and de_fora["ciclos"] == []
        assert self._buscar(base, "RN_dora")["ciclos"] == []
        da_b = self._buscar(base, "prado", tenant=B)
        assert [c["id"] for c in da_b["clientes"]] == ["cli-dora"]
        assert [c["id"] for c in da_b["ciclos"]] == [self.ciclo_dora]

    def test_marcas_do_cliente(self, base):
        ci.marcar_nao_contatar(A, "cli-bia", ci.ORIGEM_EMPRESA)
        ci.cancelar(A, "cli-caio")
        assert self._buscar(base, "bia")["clientes"][0]["nao_contatar"] is True
        assert self._buscar(base, "caio")["clientes"][0]["cancelado"] is True

    def test_curinga_do_sql_e_texto_comum(self, base):
        assert self._buscar(base, "%%")["clientes"] == []
        assert self._buscar(base, "__")["clientes"] == []
        assert self._buscar(base, "a%")["clientes"] == []

    @pytest.mark.parametrize("q, motivo", [("", "busca_curta"), ("a", "busca_curta"),
                                           ("  a  ", "busca_curta"), ("x" * 65, "busca_longa")])
    def test_texto_curto_ou_longo_e_422(self, base, q, motivo):
        r = _get(base, A, "/busca", q=q)
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == motivo

    def test_sem_token_e_401_e_a_chave_de_api_nao_vale(self, base):
        assert base.get("/busca", params={"q": "ana"}).status_code == 401
        r = base.post("/integracao/chaves", json={"nome": "Servidor"},
                      headers=base.projeto.bearer(A, papel="owner", plano="premium"))
        chave = r.json()["chave_inteira"]
        com_chave = base.get("/busca", params={"q": "ana"}, headers={"Authorization": f"Bearer {chave}"})
        assert com_chave.status_code == 401
        assert com_chave.json()["detail"]["motivo"] == "chave_nao_vale_nesta_rota"

    def test_qualquer_papel_busca_e_fica_no_registro_de_acesso(self, base):
        for papel in ("owner", "admin", "membro"):
            assert _get(base, A, "/busca", q="ana", papel=papel).status_code == 200
        registrados = [(a["rota"], a["papel"]) for a in registro_acesso.acessos(A)]
        assert [r for r in registrados if r[0] == registro_acesso.ROTA_BUSCA] == [
            ("GET /busca", "membro"), ("GET /busca", "admin"), ("GET /busca", "owner")]

    def test_fora_do_premium_vem_so_os_ciclos(self, base):
        r = self._buscar(base, "prado", plano="essencial")
        assert r["clientes"] == []
        assert [c["id"] for c in r["ciclos"]] == [self.ciclo_ana]

    def test_no_maximo_oito_de_cada(self, cliente):
        _base(A, *[(f"cli-{i:02d}", 100.0, 1, 1, {"nome": f"Loja {i}", "id_recorrencia": f"RN_l{i:02d}"})
                   for i in range(12)])
        for i in range(12):
            _ciclo(A, f"RN_l{i:02d}", dias_atras=1)
        r = self._buscar(cliente, "loja")
        assert len(r["clientes"]) == 8 and len(r["ciclos"]) == 8


# == O anexo da base exige dono ou administrador ============================

CSV = b"customer_id_externo,mrr,billing_profile\nc1,100,PJ\nc2,200,CLT\n"


class TestImportarExigePapel:
    def _importar(self, cliente, **token):
        return cliente.post("/clientes/importar", headers=cliente.projeto.bearer(A, **token),
                            files={"arquivo": ("base.csv", CSV, "text/csv")})

    @pytest.mark.parametrize("papel", ["owner", "admin"])
    def test_dono_e_administrador_importam(self, cliente, papel):
        r = self._importar(cliente, papel=papel)
        assert r.status_code == 200 and r.json()["importados"] == 2
        assert ci.contar(A) == 2

    def test_membro_recebe_403_e_nada_e_gravado(self, cliente):
        r = self._importar(cliente, papel="membro")
        assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert ci.contar(A) == 0

    def test_token_sem_papel_tambem_e_403(self, cliente):
        assert self._importar(cliente).status_code == 403
        assert ci.contar(A) == 0

    def test_sem_token_continua_401(self, cliente):
        r = cliente.post("/clientes/importar", files={"arquivo": ("base.csv", CSV, "text/csv")})
        assert r.status_code == 401


# == O expurgo: os ciclos antigos sao anonimizados ==========================

def _sql(consulta, *args):
    conn = sqlite3.connect(cc.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(consulta, args)]
    finally:
        conn.close()


def _ciclo_antigo(tenant, rec, dias_atras, valor=200.0):
    """Recuperado na 2a tentativa ha `dias_atras` dias, com estorno parcial e linha no dataset."""
    ciclo_id, quando = _recuperado_na_tentativa(tenant, rec, 2, valor=valor, dias_atras=dias_atras)
    cc.registrar_estorno(ciclo_id, f"D-{rec}", 50.0, 30, quando + timedelta(days=1))
    conn = recovery_log._conectar()
    try:
        conn.execute("INSERT INTO ciclos_recuperacao (tenant_id, customer_id, e2e_id, registrado_em, "
                     "invoice_amount, recovered, ciclo_id) VALUES (?, ?, ?, ?, ?, 1, ?)",
                     (tenant, rec, f"E-{rec}", quando.isoformat(), valor, ciclo_id))
        conn.commit()
    finally:
        conn.close()
    return ciclo_id, quando


class TestAnonimizacaoDosCiclos:
    def test_ciclo_com_desfecho_ha_mais_de_24_meses_perde_os_identificadores(self, cliente):
        _base(A, ("c-ana", 500.0, 3, 4, {"nome": "Ana Prado", "id_recorrencia": "RN_velho"}))
        ciclo_id, quando = _ciclo_antigo(A, "RN_velho", dias_atras=800)
        mes = quando.strftime("%Y-%m")
        antes_mes = _get(cliente, A, "/metrics/involuntario/mes", mes=mes).json()
        antes_extrato = _get(cliente, A, "/extrato", mes=mes).json()["totais"]
        antes = _sql("SELECT * FROM ciclos_cobranca WHERE id = ?", ciclo_id)[0]
        tentativas_antes = _sql("SELECT * FROM tentativas_cobranca WHERE ciclo_id = ? ORDER BY numero", ciclo_id)
        assert antes_mes["recuperados"] == 1 and antes_extrato["liquido"] > 0
        assert any(t["id_cobranca"] for t in tentativas_antes)

        assert configuracao.ler(A)["retencao_ciclos_meses"] == 24
        assert relogio.expurgar_ciclos(datas.agora_local()) == 1

        depois = _sql("SELECT * FROM ciclos_cobranca WHERE id = ?", ciclo_id)[0]
        marca = f"{cc.MARCA_DE_ANONIMIZADO}{ciclo_id}"
        assert depois["id_recorrencia"] == depois["id_cobranca_original"] == marca == f"anonimizado-{ciclo_id}"
        assert depois["e2e_falha_original"] is None
        # O que e agregado fica exatamente como estava.
        for campo in set(antes) - {"id_recorrencia", "id_cobranca_original", "e2e_falha_original"}:
            assert depois[campo] == antes[campo], campo
        tentativas = _sql("SELECT * FROM tentativas_cobranca WHERE ciclo_id = ? ORDER BY numero", ciclo_id)
        assert len(tentativas) == 2 and all(t["id_cobranca"] is None and t["e2e_resultado"] is None
                                            for t in tentativas)
        for depois_t, antes_t in zip(tentativas, tentativas_antes):
            for campo in set(antes_t) - {"id_cobranca", "e2e_resultado"}:
                assert depois_t[campo] == antes_t[campo], campo
        estorno = _sql("SELECT * FROM estornos_ciclo WHERE ciclo_id = ?", ciclo_id)[0]
        assert estorno["id_devolucao"].startswith(cc.MARCA_DE_ANONIMIZADO) and estorno["valor_devolvido"] == 50.0
        dataset = _sql("SELECT * FROM ciclos_recuperacao WHERE ciclo_id = ?", ciclo_id)[0]
        assert dataset["customer_id"] == marca and dataset["e2e_id"].startswith(cc.MARCA_DE_ANONIMIZADO)
        assert dataset["invoice_amount"] == 200.0 and dataset["recovered"] == 1
        for tabela in ("ciclos_cobranca", "tentativas_cobranca", "estornos_ciclo", "ciclos_recuperacao"):
            assert "RN_velho" not in str(_sql(f"SELECT * FROM {tabela}")), tabela

        # Os agregados do periodo: identicos.
        assert _get(cliente, A, "/metrics/involuntario/mes", mes=mes).json() == antes_mes
        assert _get(cliente, A, "/extrato", mes=mes).json()["totais"] == antes_extrato
        # E o ciclo deixou de apontar para a pessoa.
        linha = _get(cliente, A, "/ciclos").json()["ciclos"][0]
        assert linha["id_recorrencia"] == marca and linha["cliente_nome"] is None
        assert "Ana Prado" not in _get(cliente, A, "/extrato", mes=mes).text

    def test_o_texto_das_mensagens_some_junto(self, cliente):
        ciclo_id, aberto = _ciclo(A, "RN_msg", dias_atras=900)
        for n in (1, 2, 3):
            _tentar(ciclo_id, n, cc.FALHOU, aberto + timedelta(hours=n))
        _mensagem(ciclo_id, aberto + timedelta(hours=4))
        cc.transicionar(ciclo_id, cc.PERDIDO, aberto + timedelta(hours=8))
        assert any(m["texto"] for m in cc.mensagens_do_ciclo(ciclo_id))
        assert cc.anonimizar_ciclos_expirados(datas.agora_local(), A, 24) == 1
        mensagens = cc.mensagens_do_ciclo(ciclo_id)
        assert len(mensagens) == 3 and all(m["texto"] is None and m["texto_apagado_em"] for m in mensagens)
        assert [m["abordagem"] for m in mensagens if m["escolhida"]] == ["lembrete_cordial"]

    def test_dentro_do_prazo_e_sem_desfecho_ninguem_e_tocado(self, cliente):
        recente, _ = _ciclo_antigo(A, "RN_recente", dias_atras=700)        # 23 meses
        aberto_velho, _ = _ciclo(A, "RN_aberto", dias_atras=900)           # sem desfecho
        assert relogio.expurgar_ciclos(datas.agora_local()) == 0
        assert cc.ciclo_por_id(recente)["id_recorrencia"] == "RN_recente"
        assert cc.ciclo_por_id(aberto_velho)["id_recorrencia"] == "RN_aberto"

    def test_e_idempotente(self, cliente):
        ciclo_id, _ = _ciclo_antigo(A, "RN_velho", dias_atras=800)
        agora = datas.agora_local()
        assert cc.anonimizar_ciclos_expirados(agora, A, 24) == 1
        foto = _sql("SELECT * FROM ciclos_cobranca WHERE id = ?", ciclo_id)
        assert cc.anonimizar_ciclos_expirados(agora, A, 24) == 0
        assert _sql("SELECT * FROM ciclos_cobranca WHERE id = ?", ciclo_id) == foto
        assert cc.tenants_com_ciclos_identificados() == []

    def test_o_prazo_e_o_da_configuracao_de_cada_empresa(self, cliente):
        configuracao.gravar(A, {"retencao_ciclos_meses": 12})
        de_a, _ = _ciclo_antigo(A, "RN_a", dias_atras=400)                 # 13 meses
        de_b, _ = _ciclo_antigo(B, "RN_b", dias_atras=400)
        assert relogio.expurgar_ciclos(datas.agora_local()) == 1
        assert cc.ciclo_por_id(de_a)["id_recorrencia"].startswith(cc.MARCA_DE_ANONIMIZADO)
        assert cc.ciclo_por_id(de_b)["id_recorrencia"] == "RN_b", "a empresa B continua com 24 meses"

    def test_a_anonimizacao_de_uma_empresa_nao_toca_a_outra(self, cliente):
        de_a, _ = _ciclo_antigo(A, "RN_a", dias_atras=800)
        de_b, _ = _ciclo_antigo(B, "RN_b", dias_atras=800)
        assert cc.anonimizar_ciclos_expirados(datas.agora_local(), A, 24) == 1
        assert cc.ciclo_por_id(de_b)["id_recorrencia"] == "RN_b"
        assert cc.tenants_com_ciclos_identificados() == [B]

    @pytest.mark.parametrize("meses", [0, -1, 2.5, "24", True, None])
    def test_prazo_invalido_levanta(self, cliente, meses):
        with pytest.raises(ValueError):
            cc.anonimizar_ciclos_expirados(datas.agora_local(), A, meses)

    def test_meses_atras_no_calendario(self):
        from datetime import datetime
        assert cc.meses_atras(datetime(2026, 10, 5, 9, 0), 24) == datetime(2024, 10, 5, 9, 0)
        assert cc.meses_atras(datetime(2026, 3, 31), 1) == datetime(2026, 2, 28)
        assert cc.meses_atras(datetime(2024, 3, 31), 1) == datetime(2024, 2, 29)
        assert cc.meses_atras(datetime(2026, 1, 15), 2) == datetime(2025, 11, 15)

    @pytest.mark.asyncio
    async def test_a_passagem_diaria_roda_os_dois_expurgos_novos_uma_vez_por_dia(self, cliente,
                                                                                 monkeypatch):
        _ciclo_antigo(A, "RN_velho", dias_atras=800)
        monkeypatch.setitem(relogio._estado, "ultimo_expurgo_em", None)
        agora = datas.agora_local()
        primeira = await relogio.passagem(agora)
        assert primeira["ciclos_anonimizados"] == 1 and primeira["simulacoes_apagadas"] == 0
        assert set(primeira) == {"disparos", "expurgo", "expurgo_da_trilha", "ciclos_anonimizados",
                                 "simulacoes_apagadas"}
        segunda = await relogio.passagem(agora)
        assert "ciclos_anonimizados" not in segunda and "simulacoes_apagadas" not in segunda


# == O expurgo: a simulacao parada ha mais de 30 dias =======================

def _simular(cliente, tenant):
    r = cliente.post("/simulacao/cliente", headers=cliente.projeto.bearer(tenant, papel="owner", plano="premium"),
                     json={"nome": "Ana Souza", "mensalidade": 300.0, "perfil": "clt",
                           "verdade": {"dias_ate_saldo": 3, "chance_pagar": 1.0, "vai_revogar": False}})
    assert r.status_code == 200, r.text
    assert cliente.post("/simulacao/cobrar",
                        headers=cliente.projeto.bearer(tenant, papel="owner", plano="premium")).status_code == 200
    existentes = [p for p in simulador.arquivos(tenant) if p.exists()]
    assert existentes
    return existentes


def _envelhecer(arquivos, dias):
    import gc
    gc.collect()
    quando = (datas.agora_local() - timedelta(days=dias)).timestamp()
    for arquivo in arquivos:
        os.utime(arquivo, (quando, quando))


class TestSimulacaoParada:
    def test_simulacao_parada_ha_mais_de_30_dias_e_apagada(self, cliente):
        arquivos = _simular(cliente, A)
        reais = [cc.caminho_do_banco()]
        conteudo_real = [p.read_bytes() for p in reais]
        _envelhecer(arquivos, 31)
        assert simulador.DIAS_DE_SIMULACAO_PARADA == 30
        assert relogio.expurgar_simulacoes(datas.agora_local()) == 1
        assert not any(p.exists() for p in simulador.arquivos(A))
        assert simulador.existe(A) is False
        r = cliente.get("/simulacao", headers=cliente.projeto.bearer(A, papel="owner", plano="premium"))
        assert r.json()["existe"] is False
        assert [p.read_bytes() for p in reais] == conteudo_real, "nada do que e real foi tocado"

    def test_simulacao_usada_ha_menos_de_30_dias_fica(self, cliente):
        arquivos = _simular(cliente, A)
        _envelhecer(arquivos, 29)
        assert simulador.apagar_paradas(datas.agora_local()) == 0
        assert all(p.exists() for p in arquivos)

    def test_basta_um_arquivo_recente_para_a_simulacao_ficar(self, cliente):
        arquivos = _simular(cliente, A)
        _envelhecer(arquivos, 60)
        _envelhecer(arquivos[:1], 2)
        assert simulador.apagar_paradas(datas.agora_local()) == 0

    def test_so_a_simulacao_parada_sai_e_a_outra_empresa_continua(self, cliente):
        de_a, de_b = _simular(cliente, A), _simular(cliente, B)
        _envelhecer(de_a, 45)
        assert simulador.apagar_paradas(datas.agora_local()) == 1
        assert simulador.existe(A) is False and simulador.existe(B) is True
        assert all(p.exists() for p in de_b)

    def test_empresa_cujo_nome_vira_hash_no_arquivo(self, cliente):
        tenant = "Empresa.Maiuscula"
        assert ambiente.parte_do_nome(tenant).startswith("h")
        arquivos = _simular(cliente, tenant)
        _envelhecer(arquivos, 31)
        assert simulador.apagar_paradas(datas.agora_local()) == 1
        assert simulador.existe(tenant) is False

    def test_arquivo_de_simulacao_ilegivel_tambem_sai_e_o_real_nunca(self, cliente):
        real = cc.caminho_do_banco()
        _ciclo(A, "RN_real", dias_atras=1)                 # o banco real existe
        orfao = ambiente.caminho_simulado(real, "empresa-orfa")
        orfao.write_bytes(b"isto nao e um banco")
        _envelhecer([orfao, real], 90)
        assert simulador.apagar_paradas(datas.agora_local()) == 1
        assert not orfao.exists() and real.exists()

    @pytest.mark.parametrize("dias", [0, -3, 1.5, "30", True])
    def test_prazo_invalido_levanta(self, dias):
        with pytest.raises(ValueError):
            simulador.apagar_paradas(datas.agora_local(), dias)
