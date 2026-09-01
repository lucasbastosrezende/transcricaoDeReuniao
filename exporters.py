"""Geração dos arquivos de saída a partir do resultado da transcrição.

Cada formato serve a um uso diferente: TXT e DOCX para ler e editar, SRT/VTT
para legendar, CSV para planilha, JSON para integrar com outro sistema.
"""
import csv
import io
import json
import os
from typing import Any, Dict, List

import ptbr

# Rótulo, extensão e tipo MIME de cada formato oferecido na interface.
FORMATS = {
    "txt": ("Texto com parágrafos", ".txt", "text/plain; charset=utf-8"),
    "txt_timestamps": ("Texto com marcações de tempo", ".txt", "text/plain; charset=utf-8"),
    "srt": ("Legenda SRT", ".srt", "application/x-subrip; charset=utf-8"),
    "vtt": ("Legenda WebVTT", ".vtt", "text/vtt; charset=utf-8"),
    "md": ("Markdown", ".md", "text/markdown; charset=utf-8"),
    "csv": ("Planilha CSV", ".csv", "text/csv; charset=utf-8"),
    "json": ("JSON completo", ".json", "application/json; charset=utf-8"),
    "docx": ("Documento Word", ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}


def build_txt(result: Dict[str, Any]) -> str:
    """Texto corrido, quebrado em parágrafos nas pausas da fala."""
    return "\n\n".join(result.get("paragraphs") or [result.get("plain_text", "")]) + "\n"


def build_txt_with_timestamps(result: Dict[str, Any]) -> str:
    lines = []
    for seg in result.get("segments", []):
        lines.append(f"[{seg['start_str']} - {seg['end_str']}] {seg['text']}")
    return "\n".join(lines) + "\n"


def build_srt(result: Dict[str, Any]) -> str:
    blocks = []
    for cue in result.get("cues", []):
        start = ptbr.seconds_to_timestamp(cue["start"], srt=True)
        end = ptbr.seconds_to_timestamp(cue["end"], srt=True)
        body = "\n".join(cue["lines"])
        blocks.append(f"{cue['id']}\n{start} --> {end}\n{body}\n")
    return "\n".join(blocks)


def build_vtt(result: Dict[str, Any]) -> str:
    blocks = ["WEBVTT", ""]
    for cue in result.get("cues", []):
        start = ptbr.seconds_to_timestamp(cue["start"], srt=False)
        end = ptbr.seconds_to_timestamp(cue["end"], srt=False)
        body = "\n".join(cue["lines"])
        blocks.append(f"{start} --> {end}\n{body}\n")
    return "\n".join(blocks)


def build_markdown(result: Dict[str, Any], title: str) -> str:
    minutes, seconds = divmod(int(result.get("duration", 0)), 60)
    header = [
        f"# {title}",
        "",
        f"- **Duração:** {minutes}min {seconds}s",
        f"- **Palavras:** {result.get('word_count', 0)}",
        f"- **Idioma detectado:** {result.get('language', 'pt')}",
        f"- **Modelo:** {result.get('model', '')} ({result.get('profile', '')})",
        f"- **Confiança média:** {result.get('avg_confidence', 0) * 100:.0f}%",
        "",
        "---",
        "",
    ]
    body = []
    for seg in result.get("segments", []):
        body.append(f"**`{seg['start_str']}`** {seg['text']}")
        body.append("")
    return "\n".join(header + body)


def build_csv(result: Dict[str, Any]) -> str:
    buffer = io.StringIO(newline="")
    # Ponto e vírgula é o separador que o Excel em português espera.
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["#", "Início", "Fim", "Início (s)", "Fim (s)", "Confiança", "Texto"])
    for seg in result.get("segments", []):
        writer.writerow([
            seg["id"], seg["start_str"], seg["end_str"],
            f"{seg['start']:.2f}".replace(".", ","),
            f"{seg['end']:.2f}".replace(".", ","),
            f"{seg['confidence'] * 100:.0f}%",
            seg["text"],
        ])
    return buffer.getvalue()


def build_json(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def build_docx(result: Dict[str, Any], title: str, path: str) -> None:
    """Escreve um .docx nativo, sem depender de bibliotecas externas.

    Um .docx é um ZIP com XML dentro; montar os quatro arquivos mínimos à mão
    mantém a instalação leve e 100% offline.
    """
    import zipfile
    from xml.sax.saxutils import escape

    def paragraph(text: str, style: str = "") -> str:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{style_xml}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"

    minutes, seconds = divmod(int(result.get("duration", 0)), 60)
    body = [
        paragraph(title, "Heading1"),
        paragraph(
            f"Duração {minutes}min {seconds}s · {result.get('word_count', 0)} palavras · "
            f"modelo {result.get('model', '')} · confiança média "
            f"{result.get('avg_confidence', 0) * 100:.0f}%"
        ),
        paragraph(""),
    ]
    for para in result.get("paragraphs") or [result.get("plain_text", "")]:
        body.append(paragraph(para))
        body.append(paragraph(""))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body)}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document)


def write_all(result: Dict[str, Any], output_dir: str, base_name: str) -> Dict[str, str]:
    """Grava todos os formatos em disco e devolve o caminho de cada um."""
    os.makedirs(output_dir, exist_ok=True)
    paths: Dict[str, str] = {}

    text_builders = {
        "txt": build_txt(result),
        "txt_timestamps": build_txt_with_timestamps(result),
        "srt": build_srt(result),
        "vtt": build_vtt(result),
        "md": build_markdown(result, base_name),
        "csv": build_csv(result),
        "json": build_json(result),
    }
    suffixes = {
        "txt": "_transcricao",
        "txt_timestamps": "_transcricao_com_tempos",
        "srt": "_legendas",
        "vtt": "_legendas",
        "md": "_transcricao",
        "csv": "_segmentos",
        "json": "_dados",
        "docx": "_transcricao",
    }

    for key, content in text_builders.items():
        _label, ext, _mime = FORMATS[key]
        path = os.path.join(output_dir, f"{base_name}{suffixes[key]}{ext}")
        # O BOM faz o Excel abrir o CSV em UTF-8 sem estragar os acentos.
        encoding = "utf-8-sig" if key == "csv" else "utf-8"
        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
        paths[key] = path

    docx_path = os.path.join(output_dir, f"{base_name}{suffixes['docx']}.docx")
    try:
        build_docx(result, base_name, docx_path)
        paths["docx"] = docx_path
    except Exception:  # o .docx é um extra; nunca deve derrubar a tarefa
        pass

    return paths


def available_formats(paths: Dict[str, str]) -> List[Dict[str, str]]:
    return [
        {"id": key, "label": FORMATS[key][0], "ext": FORMATS[key][1]}
        for key in FORMATS
        if key in paths and os.path.exists(paths[key])
    ]
