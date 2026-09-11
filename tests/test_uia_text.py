"""Extracao de texto: ordem de documento e deduplicacao. Spec §8.3, CA-12.

O percurso e injetado, como em `tree.py`: a logica de ordem e de supressao e onde
mora o risco, e ela precisa ser testavel sem Windows.
"""

from __future__ import annotations

from mcp_windows_uia.uia.text import extrair, montar_texto


class NoFalso:
    """Nó de árvore de teste. `pattern` simula um TextPattern que cobre a subárvore."""

    def __init__(self, texto="", *, ref="", pattern=None, offscreen=False, filhos=()):
        self.texto = texto
        self.ref = ref
        self.pattern = pattern
        self.offscreen = offscreen
        self.filhos = list(filhos)


class LeitorFalso:
    def filhos(self, no):
        return no.filhos

    def ler(self, no):
        from mcp_windows_uia.uia.text import LeituraDeNo

        return LeituraDeNo(
            texto_de_pattern=no.pattern,
            texto_proprio=no.texto,
            ref=no.ref,
            offscreen=no.offscreen,
        )


def texto_de(raiz, **kw) -> str:
    return montar_texto(extrair(raiz, LeitorFalso()).blocos, **kw)


# ------------------------------------------------------------------------ ordem


def test_ordem_e_de_documento_nao_de_largura() -> None:
    """§8.3 pede ordem de documento. A captura da arvore e em LARGURA por nivel.

    Reusar aquela lista plana entregaria "A, B, A1, A2, B1": o texto de um painel
    apareceria no meio do texto de outro. Este teste e a razao de `extrair` fazer
    o proprio percurso em profundidade.
    """
    raiz = NoFalso(
        filhos=[
            NoFalso("A", filhos=[NoFalso("A1"), NoFalso("A2")]),
            NoFalso("B", filhos=[NoFalso("B1")]),
        ]
    )
    assert texto_de(raiz) == "A\nA1\nA2\nB\nB1"


def test_separa_blocos_por_quebra_de_linha() -> None:
    raiz = NoFalso(filhos=[NoFalso("a"), NoFalso("b")])
    assert texto_de(raiz) == "a\nb"


def test_no_sem_texto_nao_vira_bloco_mas_deixa_descer() -> None:
    raiz = NoFalso(filhos=[NoFalso("", filhos=[NoFalso("unico")])])
    assert texto_de(raiz) == "unico"


def test_arvore_vazia_devolve_string_vazia() -> None:
    assert texto_de(NoFalso()) == ""


# ------------------------------------------------------------- deduplicacao CA-12


def test_ancestral_com_text_pattern_suprime_descendentes() -> None:
    """CA-12: sem duplicacao de conteudo por ancestral/descendente."""
    doc = NoFalso(
        "doc",
        pattern="linha um\nlinha dois",
        filhos=[NoFalso("linha um"), NoFalso("linha dois")],
    )
    texto = texto_de(NoFalso(filhos=[doc]))
    assert texto.count("linha um") == 1
    assert texto.count("linha dois") == 1


def test_irmao_apos_o_bloco_do_ancestral_volta_a_contar() -> None:
    """A supressao vale so para a subarvore coberta, nao para o resto da janela."""
    raiz = NoFalso(
        filhos=[
            NoFalso("doc", pattern="dentro", filhos=[NoFalso("dentro")]),
            NoFalso("fora"),
        ]
    )
    texto = texto_de(raiz)
    assert "fora" in texto
    assert texto.count("dentro") == 1


def test_pattern_vazio_cai_para_os_filhos() -> None:
    """Provider que anuncia TextPattern e devolve "" nao pode apagar a subarvore."""
    raiz = NoFalso(filhos=[NoFalso("doc", pattern="   ", filhos=[NoFalso("conteudo")])])
    assert texto_de(raiz) == "doc\nconteudo"


def test_texto_igual_ao_do_ancestral_nao_repete() -> None:
    """ListItem[name=X] > Text[name=X] e o padrao de duplicacao mais comum no UIA."""
    raiz = NoFalso(filhos=[NoFalso("Joao", filhos=[NoFalso("Joao")])])
    assert texto_de(raiz) == "Joao"


def test_duplicacao_atravessa_ancestral_sem_texto() -> None:
    raiz = NoFalso(filhos=[NoFalso("Joao", filhos=[NoFalso("", filhos=[NoFalso("Joao")])])])
    assert texto_de(raiz) == "Joao"


def test_irmaos_com_texto_igual_ambos_contam() -> None:
    """Guarda contra dedup exagerada: duas celulas "Vazio" sao dois dados, nao um.

    Suprimir por "linha igual a anterior" apagaria uma delas em silencio.
    """
    raiz = NoFalso(filhos=[NoFalso("Vazio"), NoFalso("Vazio")])
    assert texto_de(raiz) == "Vazio\nVazio"


# ------------------------------------------------------------------ apresentacao


def test_senha_aparece_redigida() -> None:
    raiz = NoFalso(filhos=[NoFalso("«redacted:password»")])
    assert texto_de(raiz) == "«redacted:password»"


def test_sem_include_refs_nao_ha_prefixo() -> None:
    raiz = NoFalso(filhos=[NoFalso("oi", ref="w1-e3")])
    assert texto_de(raiz) == "oi"


def test_include_refs_prefixa_cada_bloco() -> None:
    raiz = NoFalso(filhos=[NoFalso("oi", ref="w1-e3")])
    assert texto_de(raiz, include_refs=True) == "[w1-e3] oi"


def test_include_refs_marca_offscreen() -> None:
    """§8.3: offscreen entra no texto, mas sinalizado — nao e o que o usuario ve."""
    raiz = NoFalso(filhos=[NoFalso("escondido", ref="w1-e3", offscreen=True)])
    assert texto_de(raiz, include_refs=True) == "[w1-e3][offscreen] escondido"


# ----------------------------------------------------------------------- orcamento


def test_teto_de_nos_marca_truncado() -> None:
    raiz = NoFalso(filhos=[NoFalso(str(i)) for i in range(50)])
    r = extrair(raiz, LeitorFalso(), max_nos=10)
    assert r.visitados <= 10
    assert r.truncado is True


def test_teto_de_profundidade_marca_truncado() -> None:
    fundo = NoFalso("fundo")
    atual = fundo
    for _ in range(10):
        atual = NoFalso("", filhos=[atual])
    r = extrair(atual, LeitorFalso(), max_profundidade=3)
    assert r.truncado is True
    assert "fundo" not in montar_texto(r.blocos)


def test_dentro_do_orcamento_nao_marca_truncado() -> None:
    raiz = NoFalso(filhos=[NoFalso("a"), NoFalso("b")])
    assert extrair(raiz, LeitorFalso()).truncado is False


# ------------------------------------------------- marcador de objeto embutido


def test_placeholder_de_objeto_sozinho_nao_vira_bloco() -> None:
    """\ufffc e OBJECT REPLACEMENT CHARACTER: marca imagem embutida, nao texto.

    Medido no WhatsApp Desktop: 52 dos 376 blocos eram so isso, em sequencia. Para o
    agente, "￼" nao diz nada — e ainda dispara o alarme de bloco duplicado.
    """
    raiz = NoFalso(filhos=[NoFalso("\ufffc"), NoFalso("conteudo")])
    assert texto_de(raiz) == "conteudo"


def test_placeholder_misturado_com_texto_some_so_ele() -> None:
    raiz = NoFalso(filhos=[NoFalso("\ufffc Foto \ufffc")])
    assert texto_de(raiz) == "Foto"


def test_placeholder_tambem_sai_do_texto_de_pattern() -> None:
    raiz = NoFalso(filhos=[NoFalso("doc", pattern="\ufffc")])
    assert texto_de(raiz) == "doc"
