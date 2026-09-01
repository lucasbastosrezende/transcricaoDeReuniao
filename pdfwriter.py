"""Gerador de PDF mínimo, escrito à mão e sem dependências.

Um PDF é um formato de arquivo bem documentado: um cabeçalho, uma lista de
objetos numerados, uma tabela de deslocamentos (`xref`) e um rodapé apontando
para ela. Para páginas de texto corrido com as fontes padrão — que todo leitor
de PDF é obrigado a ter — não é preciso nada além disso.

Escrever este módulo custa umas duzentas linhas e evita levar `reportlab`
(vários megabytes) para dentro de um projeto cujo objetivo é caber em uma
instalação leve e offline.

Limitação assumida: as fontes base do PDF usam `WinAnsiEncoding`, ou seja,
Latin-1. Cobre o português inteiro; um caractere fora disso (um emoji, um
ideograma) vira `?` no PDF em vez de corromper o arquivo.
"""
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Larguras da Helvetica em milésimos de "em", conforme as métricas AFM
# publicadas pela Adobe. São o que permite quebrar linha na coluna certa.
_HELVETICA_WIDTHS = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191,
    "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556,
    "8": 556, "9": 556, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556,
    "@": 1015, "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778,
    "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
    "`": 333, "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556, "o": 556,
    "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556, "v": 500, "w": 722,
    "x": 500, "y": 500, "z": 500, "{": 334, "|": 260, "}": 334, "~": 584,
}
# A Helvetica-Bold é ~4% mais larga na média; medir com este fator evita que um
# título estoure a margem sem precisar de uma segunda tabela inteira.
_BOLD_FACTOR = 1.04
_FALLBACK_WIDTH = 556


def _base_char(char: str) -> str:
    """Letra sem acento, para consultar a largura na tabela ASCII."""
    decomposed = unicodedata.normalize("NFD", char)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped[:1] or char


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Largura do texto em pontos, na fonte e no corpo informados."""
    total = 0
    for char in text:
        width = _HELVETICA_WIDTHS.get(char)
        if width is None:
            width = _HELVETICA_WIDTHS.get(_base_char(char), _FALLBACK_WIDTH)
        total += width
    points = total * size / 1000.0
    return points * _BOLD_FACTOR if bold else points


def wrap(text: str, width: float, size: float, bold: bool = False) -> List[str]:
    """Quebra o parágrafo em linhas que cabem na largura disponível."""
    words = text.split()
    if not words:
        return [""]

    lines: List[str] = []

    def cut_oversized(chunk: str) -> str:
        """Emite os pedaços cheios de uma palavra maior que a linha inteira.

        Acontece com URLs e códigos longos. Sem isto o texto sairia da margem
        direita da página, que é o defeito mais visível que um PDF pode ter.
        """
        while len(chunk) > 1 and text_width(chunk, size, bold) > width:
            cut = len(chunk) - 1
            while cut > 1 and text_width(chunk[:cut], size, bold) > width:
                cut -= 1
            lines.append(chunk[:cut])
            chunk = chunk[cut:]
        return chunk

    current = ""
    for word in words:
        if not current:
            current = word
        elif text_width(f"{current} {word}", size, bold) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
        current = cut_oversized(current)

    if current:
        lines.append(current)
    return lines


def _escape(text: str) -> bytes:
    """Codifica em Latin-1 e protege os caracteres com significado no PDF."""
    encoded = text.encode("latin-1", "replace")
    return (
        encoded.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
    )


class Document:
    """Acumula linhas de texto e escreve o PDF paginado no final."""

    PAGE_WIDTH = 595.28   # A4 retrato, em pontos (1/72 de polegada)
    PAGE_HEIGHT = 841.89
    MARGIN = 56.0

    def __init__(self, title: str = "", author: str = "Transcritor pt-BR"):
        self.title = title
        self.author = author
        self.pages: List[List[Tuple[float, float, str, float, bool, Tuple[float, float, float]]]] = [[]]
        self.cursor = self.PAGE_HEIGHT - self.MARGIN
        self.content_width = self.PAGE_WIDTH - 2 * self.MARGIN

    # --- Construção -----------------------------------------------------
    def _new_page(self) -> None:
        self.pages.append([])
        self.cursor = self.PAGE_HEIGHT - self.MARGIN

    def space(self, amount: float) -> None:
        self.cursor -= amount
        if self.cursor < self.MARGIN:
            self._new_page()

    def line(self, text: str, size: float = 10.5, bold: bool = False,
             color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
             indent: float = 0.0, leading: Optional[float] = None) -> None:
        """Escreve uma linha já quebrada, abrindo página nova quando necessário."""
        step = leading if leading is not None else size * 1.42
        if self.cursor - step < self.MARGIN:
            self._new_page()
        self.cursor -= step
        self.pages[-1].append((self.MARGIN + indent, self.cursor, text, size, bold, color))

    def paragraph(self, text: str, size: float = 10.5, bold: bool = False,
                  color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                  indent: float = 0.0, after: float = 6.0) -> None:
        for line in wrap(text, self.content_width - indent, size, bold):
            self.line(line, size, bold, color, indent)
        self.space(after)

    def rule(self, after: float = 8.0) -> None:
        """Linha divisória desenhada com underscores — sem gráficos vetoriais."""
        size = 9.0
        repeat = int(self.content_width / text_width("_", size))
        self.line("_" * max(10, repeat), size, False, (0.78, 0.80, 0.85))
        self.space(after)

    # --- Serialização ---------------------------------------------------
    def _content_stream(self, page: Sequence[tuple]) -> bytes:
        parts: List[bytes] = []
        last_color: Optional[Tuple[float, float, float]] = None
        for x, y, text, size, bold, color in page:
            if not text:
                continue
            font = b"/F2" if bold else b"/F1"
            if color != last_color:
                parts.append(b"%.3f %.3f %.3f rg\n" % color)
                last_color = color
            parts.append(
                b"BT " + font + b" %.2f Tf %.2f %.2f Td (" % (size, x, y)
                + _escape(text) + b") Tj ET\n"
            )
        return b"".join(parts)

    def render(self) -> bytes:
        pages = [page for page in self.pages if page] or [[]]
        objects: List[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)  # os objetos são numerados a partir de 1

        catalog_id = add(b"")   # reservado: precisa do id de Pages
        pages_id = add(b"")     # reservado: precisa dos ids das páginas
        font_regular = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        )
        font_bold = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        )

        page_ids: List[int] = []
        for page in pages:
            stream = self._content_stream(page)
            stream_id = add(
                b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
            )
            page_id = add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> "
                b"/Contents %d 0 R >>"
                % (pages_id, self.PAGE_WIDTH, self.PAGE_HEIGHT,
                   font_regular, font_bold, stream_id)
            )
            page_ids.append(page_id)

        kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
        objects[pages_id - 1] = (
            b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_ids)
        )
        objects[catalog_id - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id

        info_id = add(
            b"<< /Title (" + _escape(self.title) + b") /Author ("
            + _escape(self.author) + b") /Producer (Transcritor pt-BR) >>"
        )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

        xref_at = len(out)
        out += b"xref\n0 %d\n" % (len(objects) + 1)
        out += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            out += b"%010d 00000 n \n" % offset
        out += (
            b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, catalog_id, info_id, xref_at)
        )
        return bytes(out)

    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.render())


def build(title: str, blocks: Sequence[Dict[str, Any]], path: str,
          subtitle: str = "") -> None:
    """Monta um PDF a partir de blocos declarativos.

    Cada bloco é `{"tipo": ..., "texto": ...}` com `tipo` em `titulo`,
    `subtitulo`, `secao`, `paragrafo`, `tempo`, `lista`, `linha`.
    """
    document = Document(title=title)
    document.paragraph(title, size=20, bold=True, color=(0.11, 0.22, 0.39), after=2)
    if subtitle:
        document.paragraph(subtitle, size=9.5, color=(0.42, 0.45, 0.52), after=4)
    document.rule()

    for block in blocks:
        kind = block.get("tipo", "paragrafo")
        text = block.get("texto", "")
        if kind == "secao":
            document.space(6)
            document.paragraph(text, size=13, bold=True, color=(0.11, 0.22, 0.39), after=3)
        elif kind == "tempo":
            document.paragraph(text, size=8.5, color=(0.35, 0.45, 0.70), after=1)
        elif kind == "lista":
            document.paragraph(f"•  {text}", size=10.5, indent=10, after=3)
        elif kind == "linha":
            document.rule()
        else:
            document.paragraph(text, size=10.5, after=7)

    document.save(path)
