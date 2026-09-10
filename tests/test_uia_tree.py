from __future__ import annotations

from mcp_windows_uia.uia.tree import CaptureBudget, percorrer


def arvore_falsa(ramificacao: int, profundidade: int):
    """Gera um navegador de arvore sintetica: cada no tem `ramificacao` filhos."""
    def filhos_de(no_id, _nivel):
        if _nivel >= profundidade:
            return []
        return [f"{no_id}.{i}" for i in range(ramificacao)]
    return filhos_de


def sempre_passa(_no_id, _nivel):
    return True


def nunca_passa(_no_id, _nivel):
    return False


def so_abaixo_de(limite):
    def predicado(_no_id, nivel):
        return nivel >= limite
    return predicado


def test_percurso_e_em_largura_por_nivel() -> None:
    """Spec §6.1: os nos rasos aparecem antes dos fundos."""
    r = percorrer("raiz", arvore_falsa(2, 3), sempre_passa,
                  CaptureBudget(max_nodes=100, max_depth=10))
    niveis = [nivel for _, nivel in r.emitidos]
    assert niveis == sorted(niveis)


def test_para_no_max_nodes() -> None:
    r = percorrer("raiz", arvore_falsa(4, 6), sempre_passa,
                  CaptureBudget(max_nodes=10, max_depth=10))
    assert len(r.emitidos) == 10
    assert r.truncado is True


def test_para_no_max_depth() -> None:
    r = percorrer("raiz", arvore_falsa(2, 10), sempre_passa,
                  CaptureBudget(max_nodes=1000, max_depth=3))
    assert max(nivel for _, nivel in r.emitidos) == 3


def test_max_children_por_no_elide_e_conta_o_resto() -> None:
    """Spec §6.1: emite os primeiros N e anota quantos ficaram de fora."""
    r = percorrer("raiz", arvore_falsa(10, 2), sempre_passa,
                  CaptureBudget(max_nodes=1000, max_depth=5, max_children_per_node=3))
    assert r.elididos["raiz"] == 7
    filhos_diretos = [i for i, nivel in r.emitidos if nivel == 1]
    assert len(filhos_diretos) == 3


def test_aprofundamento_adaptativo_quando_nada_passa_no_filtro() -> None:
    """Spec §6.1: parou raso e nao achou nada -> continua descendo."""
    r = percorrer("raiz", arvore_falsa(2, 8), so_abaixo_de(5),
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=True))
    assert r.auto_deepened is True
    assert r.depth_reached > 2
    assert len(r.emitidos) > 0


def test_aprofundamento_para_assim_que_acha_um_no() -> None:
    r = percorrer("raiz", arvore_falsa(2, 8), so_abaixo_de(4),
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=True))
    assert r.depth_reached == 4  # parou no primeiro nivel que rendeu


def test_sem_aprofundamento_quando_a_profundidade_foi_explicita() -> None:
    """Spec §6.1: max_depth informado pelo chamador e respeitado ao pe da letra."""
    r = percorrer("raiz", arvore_falsa(2, 8), so_abaixo_de(5),
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=False))
    assert r.auto_deepened is False
    assert r.depth_reached == 2
    assert r.emitidos == []


def test_sem_aprofundamento_quando_ja_achou_algo_raso() -> None:
    r = percorrer("raiz", arvore_falsa(2, 8), sempre_passa,
                  CaptureBudget(max_nodes=100, max_depth=2, depth_is_default=True))
    assert r.auto_deepened is False
    assert r.depth_reached == 2


def test_aprofundamento_respeita_o_teto_duro_de_40() -> None:
    r = percorrer("raiz", arvore_falsa(1, 100), nunca_passa,
                  CaptureBudget(max_nodes=1000, max_depth=2, depth_is_default=True))
    assert r.depth_reached <= 40


def test_aprofundamento_respeita_o_max_nodes() -> None:
    r = percorrer("raiz", arvore_falsa(3, 4), so_abaixo_de(3),
                  CaptureBudget(max_nodes=5, max_depth=2, depth_is_default=True))
    assert len(r.emitidos) <= 5


def test_visitados_conta_mais_que_emitidos_quando_ha_filtro() -> None:
    r = percorrer("raiz", arvore_falsa(2, 4), so_abaixo_de(3),
                  CaptureBudget(max_nodes=100, max_depth=4))
    assert r.visitados > len(r.emitidos)


def test_arvore_vazia_nao_explode() -> None:
    r = percorrer("raiz", lambda _n, _l: [], sempre_passa,
                  CaptureBudget(max_nodes=10, max_depth=5))
    assert len(r.emitidos) == 1  # so a raiz
    assert r.truncado is False

def test_teto_de_visitados_impede_explosao_exponencial() -> None:
    """Filtro restritivo + aprofundamento adaptativo nao pode virar busca infinita.

    Sem este teto, ramificacao 3 descendo ate o nivel 25 visita 3^25 nos.
    """
    r = percorrer("raiz", arvore_falsa(3, 30), so_abaixo_de(25),
                  CaptureBudget(max_nodes=5, max_depth=2, max_visited=500,
                                depth_is_default=True))
    assert r.visitados <= 500
    assert r.exhausted_budget is True
    assert r.truncado is True
