"""Extracao de texto linear em ordem de documento. Spec §8.3.

Duas coisas dificeis moram aqui.

A primeira e a ORDEM. A §8.3 pede ordem de documento, e a captura da arvore (§5.2)
e em largura por nivel: naquela lista plana todos os nos do nivel 1 vem antes de
qualquer no do nivel 2, entao o texto de um painel apareceria costurado no meio do
texto de outro. Por isso o percurso aqui e proprio, em profundidade.

A segunda e a DEDUPLICACAO (CA-12). Quando um ancestral expoe TextPattern, o texto
dele ja cobre os descendentes; emitir os dois duplica tudo. E o padrao
`ListItem[name=X] > Text[name=X]` duplica de novo, sem pattern nenhum no meio.

O percurso e injetado, como em `tree.py`: quem sabe COM e o `LeitorCOM` do servidor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Teto de nos VISITADOS. Independente do max_chars do chamador: cortar o percurso
# pelo tamanho do texto ja acumulado quebraria o `offset`, que pagina sobre um
# documento que precisa ser o mesmo em toda chamada.
MAX_NOS = 3000
MAX_PROFUNDIDADE = 40


@dataclass(frozen=True, slots=True)
class LeituraDeNo:
    """O que o leitor extrai de um no. Uma chamada por no, um lugar so com COM."""

    # Texto do TextPattern do proprio no: ja cobre a subarvore inteira dele.
    texto_de_pattern: str | None = None
    # Name, ou valor quando o nome e vazio. Ja redigido se for campo de senha.
    texto_proprio: str = ""
    ref: str = ""
    offscreen: bool = False


@dataclass(slots=True)
class Bloco:
    texto: str
    ref: str = ""
    offscreen: bool = False


@dataclass(slots=True)
class ResultadoDeTexto:
    blocos: list[Bloco] = field(default_factory=list)
    visitados: int = 0
    truncado: bool = False


class Leitor(Protocol):
    def filhos(self, no: Any) -> Sequence[Any]: ...
    def ler(self, no: Any) -> LeituraDeNo: ...


def extrair(
    raiz: Any,
    leitor: Leitor,
    *,
    max_nos: int = MAX_NOS,
    max_profundidade: int = MAX_PROFUNDIDADE,
) -> ResultadoDeTexto:
    """Percurso em profundidade, pre-ordem, com supressao de conteudo repetido."""
    r = ResultadoDeTexto()

    # (no, nivel, texto do ancestral mais proximo que ja emitiu bloco). O terceiro
    # campo desce pela pilha porque a duplicacao ancestral/descendente pode pular
    # niveis sem texto: ListItem[X] > Pane[] > Text[X].
    pilha: list[tuple[Any, int, str]] = [(raiz, 0, "")]

    while pilha:
        if r.visitados >= max_nos:
            r.truncado = True
            break

        no, nivel, ancestral = pilha.pop()
        r.visitados += 1
        leitura = leitor.ler(no)

        # Provider que anuncia TextPattern e devolve "" existe: nesse caso o pattern
        # nao cobriu nada, e apagar a subarvore perderia o conteudo de verdade.
        do_pattern = (leitura.texto_de_pattern or "").strip()
        if do_pattern:
            r.blocos.append(Bloco(do_pattern, leitura.ref, leitura.offscreen))
            continue

        proprio = leitura.texto_proprio.strip()
        if proprio and proprio != ancestral:
            r.blocos.append(Bloco(proprio, leitura.ref, leitura.offscreen))
            ancestral = proprio

        if nivel >= max_profundidade:
            r.truncado = True
            continue

        # Empilhado ao contrario para sair em pre-ordem da esquerda para a direita.
        for filho in reversed(list(leitor.filhos(no))):
            pilha.append((filho, nivel + 1, ancestral))

    return r


def montar_texto(blocos: Sequence[Bloco], *, include_refs: bool = False) -> str:
    """Junta os blocos com `\n`. Spec §8.3."""
    if not include_refs:
        return "\n".join(b.texto for b in blocos)

    linhas: list[str] = []
    for b in blocos:
        marca = f"[{b.ref}]"
        if b.offscreen:
            marca += "[offscreen]"
        linhas.append(f"{marca} {b.texto}")
    return "\n".join(linhas)
