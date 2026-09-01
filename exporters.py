"""Geração dos arquivos de saída a partir do resultado da transcrição.

Cada formato serve a um uso diferente: TXT e DOCX para ler e editar, SRT/VTT/ASS
para legendar, CSV/TSV para planilha, PDF para arquivar e assinar, HTML para
mandar por e-mail sem anexo pesado, JSON para integrar com outro sistema.

Todos são escritos com a biblioteca padrão do Python. O DOCX é um ZIP de XML e
o PDF é montado por `pdfwriter`; nenhum dos dois exige dependência externa, o
que mantém a instalação leve e o programa funcionando sem internet.
"""
import csv
import html
import io
import json
import os
from typing import Any, Dict, List, Optional

import pdfwriter
import ptbr

# Rótulo, extensão e tipo MIME de cada formato oferecido na interface.
FORMATS = {
    "txt": ("Texto com parágrafos", ".txt", "text/plain; charset=utf-8"),
    "txt_timestamps": ("Texto com marcações de tempo", ".txt", "text/plain; charset=utf-8"),
    "srt": ("Legenda SRT", ".srt", "application/x-subrip; charset=utf-8"),
    "vtt": ("Legenda WebVTT", ".vtt", "text/vtt; charset=utf-8"),
    "ass": ("Legenda ASS (estilizada)", ".ass", "text/plain; charset=utf-8"),
    "md": ("Markdown", ".md", "text/markdown; charset=utf-8"),
    "resumo": ("Resumo e temas", ".md", "text/markdown; charset=utf-8"),
    "csv": ("Planilha CSV", ".csv", "text/csv; charset=utf-8"),
    "tsv": ("Tabela TSV", ".tsv", "text/tab-separated-values; charset=utf-8"),
    "json": ("JSON completo", ".json", "application/json; charset=utf-8"),
    "html": ("Página HTML", ".html", "text/html; charset=utf-8"),
    "docx": ("Documento Word", ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": ("PDF", ".pdf", "application/pdf"),
}

SUFFIXES = {
    "txt": "_transcricao",
    "txt_timestamps": "_transcricao_com_tempos",
    "srt": "_legendas",
    "vtt": "_legendas",
    "ass": "_legendas",
    "md": "_transcricao",
    "resumo": "_resumo",
    "csv": "_segmentos",
    "tsv": "_segmentos",
    "json": "_dados",
    "html": "_transcricao",
    "docx": "_transcricao",
    "pdf": "_transcricao",
}


# --- Auxiliares ---------------------------------------------------------

def _duration_label(seconds: float) -> str:
    total = int(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}min"
    return f"{minutes}min {secs:02d}s"


def _header_lines(result: Dict[str, Any]) -> List[str]:
    analysis = result.get("analysis") or {}
    stats = analysis.get("estatisticas") or {}
    speakers = (result.get("diarization") or {}).get("total") or 0
    lines = [
        f"Duração: {_duration_label(result.get('duration', 0))}",
        f"Palavras: {result.get('word_count', 0)}",
        f"Modelo: {result.get('model', '')} · perfil {result.get('profile', '')}",
        f"Idioma: {result.get('language', 'pt')}",
        f"Confiança média: {result.get('avg_confidence', 0) * 100:.0f}%",
    ]
    if speakers:
        lines.append(f"Falantes identificados: {speakers}")
    if stats.get("palavras_por_minuto"):
        lines.append(f"Ritmo: {stats['palavras_por_minuto']} palavras por minuto")
    return lines


def _dialogue_or_paragraphs(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Blocos de leitura: diálogo quando há falantes, parágrafos quando não há."""
    dialogue = result.get("dialogue")
    if dialogue:
        return dialogue
    return [
        {"speaker": None, "start_str": "", "texto": paragraph}
        for paragraph in (result.get("paragraphs") or [result.get("plain_text", "")])
        if paragraph
    ]


# --- Texto --------------------------------------------------------------

def build_txt(result: Dict[str, Any]) -> str:
    """Texto corrido, quebrado em parágrafos nas pausas da fala."""
    blocks = _dialogue_or_paragraphs(result)
    if any(block.get("speaker") for block in blocks):
        return "\n\n".join(
            f"{block['speaker']}: {block['texto']}" if block.get("speaker") else block["texto"]
            for block in blocks
        ) + "\n"
    return "\n\n".join(block["texto"] for block in blocks) + "\n"


def build_txt_with_timestamps(result: Dict[str, Any]) -> str:
    lines = []
    for seg in result.get("segments", []):
        who = f" {seg['speaker']}:" if seg.get("speaker") else ""
        lines.append(f"[{seg['start_str']} - {seg['end_str']}]{who} {seg['text']}")
    return "\n".join(lines) + "\n"


# --- Legendas -----------------------------------------------------------

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


# Paleta usada quando há falantes: cada voz ganha uma cor na legenda ASS.
# O formato guarda a cor em BGR (não RGB), herança do SubStation original.
_ASS_COLORS = ["&H00FFFFFF", "&H0080D4FF", "&H0090EE90", "&H00E1A8FF",
               "&H00FFD280", "&H008CB4FF", "&H00C0FFFF", "&H00D0B0B0"]


def build_ass(result: Dict[str, Any], title: str = "") -> str:
    """Legenda estilizada, o formato que players e editores de vídeo preferem.

    Diferente do SRT, o ASS carrega fonte, contorno e posição — e permite dar
    uma cor a cada falante, o que torna a legenda de uma entrevista legível.
    """
    speakers = (result.get("diarization") or {}).get("falantes") or []
    styles = [
        "Style: Padrao,Arial,44,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2.4,1.2,2,60,60,42,1"
    ]
    for speaker in speakers:
        color = _ASS_COLORS[speaker["id"] % len(_ASS_COLORS)]
        name = f"F{speaker['id'] + 1}"
        styles.append(
            f"Style: {name},Arial,44,{color},&H000000FF,&H00101010,&H80000000,"
            "0,0,0,0,100,100,0,0,1,2.4,1.2,2,60,60,42,1"
        )

    header = [
        "[Script Info]",
        f"Title: {title or 'Transcrição'}",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    index = {f"Falante {s['id'] + 1}": f"F{s['id'] + 1}" for s in speakers}
    events = []
    for cue in result.get("cues", []):
        style = index.get(cue.get("speaker") or "", "Padrao")
        text = "\\N".join(cue["lines"]).replace("{", "(").replace("}", ")")
        events.append(
            f"Dialogue: 0,{ptbr.seconds_to_ass(cue['start'])},"
            f"{ptbr.seconds_to_ass(cue['end'])},{style},,0,0,0,,{text}"
        )
    return "\n".join(header + events) + "\n"


# --- Documentos ---------------------------------------------------------

def build_markdown(result: Dict[str, Any], title: str) -> str:
    header = [f"# {title}", ""]
    header += [f"- **{line.split(': ', 1)[0]}:** {line.split(': ', 1)[1]}"
               for line in _header_lines(result) if ": " in line]
    header += ["", "---", ""]

    analysis = result.get("analysis") or {}
    body: List[str] = []
    if analysis.get("capitulos"):
        body += ["## Capítulos", ""]
        for chapter in analysis["capitulos"]:
            body.append(f"- `{chapter['start_str']}` **{chapter['titulo']}**")
        body += ["", "---", ""]

    body += ["## Transcrição", ""]
    for seg in result.get("segments", []):
        who = f"**{seg['speaker']}** " if seg.get("speaker") else ""
        body.append(f"**`{seg['start_str']}`** {who}{seg['text']}")
        body.append("")
    return "\n".join(header + body)


def build_summary_markdown(result: Dict[str, Any], title: str) -> str:
    """Página de uma folha: o que foi dito, por quem, e o que ficou pendente."""
    analysis = result.get("analysis") or {}
    stats = analysis.get("estatisticas") or {}
    lines = [f"# {title} — resumo", ""]
    lines += [f"- {line}" for line in _header_lines(result)]
    lines += ["", "---", ""]

    if analysis.get("resumo"):
        lines += ["## Em poucas linhas", ""]
        for item in analysis["resumo"]:
            marker = f"`{item['start_str']}` " if item.get("start_str") else ""
            lines.append(f"- {marker}{item['texto']}")
        lines.append("")

    if analysis.get("palavras_chave"):
        termos = ", ".join(f"**{k['termo']}** ({k['ocorrencias']}×)"
                           for k in analysis["palavras_chave"])
        lines += ["## Temas dominantes", "", termos, ""]

    if analysis.get("capitulos"):
        lines += ["## Capítulos", ""]
        for chapter in analysis["capitulos"]:
            lines.append(f"- `{chapter['start_str']}` **{chapter['titulo']}** — {chapter['abertura']}")
        lines.append("")

    if analysis.get("pendencias"):
        lines += ["## Possíveis pendências", "",
                  "> Frases que soam como compromisso assumido. Confira no áudio antes de cobrar alguém.", ""]
        for item in analysis["pendencias"]:
            who = f"{item['falante']} — " if item.get("falante") else ""
            lines.append(f"- [ ] `{item['start_str']}` {who}{item['texto']}")
        lines.append("")

    if analysis.get("perguntas"):
        lines += ["## Perguntas feitas", ""]
        for item in analysis["perguntas"]:
            lines.append(f"- `{item['start_str']}` {item['texto']}")
        lines.append("")

    if analysis.get("por_falante"):
        lines += ["## Participação", "", "| Falante | Tempo | Palavras | Ritmo | Termos |", "|---|---|---|---|---|"]
        for speaker in analysis["por_falante"]:
            lines.append(
                f"| {speaker['nome']} | {_duration_label(speaker['tempo_s'])} | "
                f"{speaker['palavras']} | {speaker['palavras_por_minuto']} ppm | "
                f"{', '.join(speaker['termos'])} |"
            )
        lines.append("")

    if stats:
        lines += [
            "## Números da gravação", "",
            f"- Tempo de fala: {_duration_label(stats.get('tempo_fala_s', 0))} "
            f"({stats.get('proporcao_fala', 0) * 100:.0f}% da duração)",
            f"- Silêncio: {_duration_label(stats.get('tempo_silencio_s', 0))}",
            f"- Ritmo: {stats.get('palavras_por_minuto', 0)} palavras por minuto",
            f"- Vocabulário distinto: {stats.get('vocabulario_unico', 0)} palavras "
            f"(riqueza {stats.get('riqueza_lexical', 0)})",
            f"- Maior pausa: {stats.get('maior_pausa_s', 0)} s",
            f"- Trechos de baixa confiança: {stats.get('trechos_baixa_confianca', 0)}",
            "",
        ]
    return "\n".join(lines)


def build_csv(result: Dict[str, Any]) -> str:
    buffer = io.StringIO(newline="")
    # Ponto e vírgula é o separador que o Excel em português espera.
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["#", "Início", "Fim", "Início (s)", "Fim (s)", "Falante", "Confiança", "Texto"])
    for seg in result.get("segments", []):
        writer.writerow([
            seg["id"], seg["start_str"], seg["end_str"],
            f"{seg['start']:.2f}".replace(".", ","),
            f"{seg['end']:.2f}".replace(".", ","),
            seg.get("speaker", ""),
            f"{seg['confidence'] * 100:.0f}%",
            seg["text"],
        ])
    return buffer.getvalue()


def build_tsv(result: Dict[str, Any]) -> str:
    """Tabulado com números em ponto decimal — o formato que scripts esperam."""
    lines = ["start\tend\tspeaker\tconfidence\ttext"]
    for seg in result.get("segments", []):
        text = seg["text"].replace("\t", " ").replace("\n", " ")
        lines.append(
            f"{seg['start']:.3f}\t{seg['end']:.3f}\t{seg.get('speaker', '')}\t"
            f"{seg['confidence']:.3f}\t{text}"
        )
    return "\n".join(lines) + "\n"


def build_json(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


# --- HTML autocontido ---------------------------------------------------

_HTML_STYLE = """
:root{color-scheme:light dark;--fg:#141a25;--bg:#fff;--muted:#5f6c85;--line:#e3e7f0;--accent:#2563eb;--mark:#ffe08a}
@media (prefers-color-scheme:dark){:root{--fg:#e8edf7;--bg:#0d1220;--muted:#93a0ba;--line:#232c3f;--accent:#6ea3ff;--mark:#7a5c12}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:40px 22px 90px}
h1{font-size:1.7rem;margin:0 0 6px}
.meta{color:var(--muted);font-size:.85rem;margin-bottom:22px}
.meta span{margin-right:14px;white-space:nowrap}
.box{border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:0 0 22px}
.box h2{font-size:1rem;margin:0 0 10px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{border:1px solid var(--line);border-radius:99px;padding:3px 11px;font-size:.8rem}
.seg{display:grid;grid-template-columns:74px 1fr;gap:14px;padding:7px 0;border-bottom:1px solid transparent}
.seg time{font-family:ui-monospace,Consolas,monospace;font-size:.78rem;color:var(--accent);padding-top:3px}
.who{font-weight:650;margin-right:6px}
.s0{color:#2563eb}.s1{color:#c2410c}.s2{color:#047857}.s3{color:#7c3aed}
.s4{color:#b91c1c}.s5{color:#0369a1}.s6{color:#a16207}.s7{color:#be185d}
mark{background:var(--mark);color:inherit;border-radius:3px}
#busca{width:100%;padding:11px 14px;border-radius:10px;border:1px solid var(--line);
background:transparent;color:inherit;font:inherit;margin-bottom:18px}
.oculto{display:none}
ul{margin:0;padding-left:20px}
li{margin-bottom:6px}
@media print{.box,#busca{border:none}#busca{display:none}.wrap{padding:0}}
"""

_HTML_SCRIPT = """
const campo=document.getElementById('busca');
const linhas=[...document.querySelectorAll('.seg')];
const originais=linhas.map(l=>l.querySelector('.txt').textContent);
campo.addEventListener('input',()=>{
  const alvo=campo.value.trim().toLowerCase();
  linhas.forEach((linha,i)=>{
    const texto=originais[i];
    if(!alvo){linha.classList.remove('oculto');linha.querySelector('.txt').textContent=texto;return;}
    const achou=texto.toLowerCase().includes(alvo);
    linha.classList.toggle('oculto',!achou);
    if(achou){
      const escapado=alvo.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&');
      linha.querySelector('.txt').innerHTML=texto.replace(new RegExp(escapado,'gi'),m=>'<mark>'+m+'</mark>');
    }
  });
});
"""


def build_html(result: Dict[str, Any], title: str) -> str:
    """Uma página só, sem arquivos ao lado: abre em qualquer navegador e imprime."""
    escape = html.escape
    analysis = result.get("analysis") or {}
    meta = "".join(f"<span>{escape(line)}</span>" for line in _header_lines(result))

    boxes: List[str] = []
    if analysis.get("resumo"):
        itens = "".join(
            f"<li><strong>{escape(i.get('start_str', ''))}</strong> {escape(i['texto'])}</li>"
            for i in analysis["resumo"]
        )
        boxes.append(f'<div class="box"><h2>Resumo</h2><ul>{itens}</ul></div>')
    if analysis.get("palavras_chave"):
        chips = "".join(
            f'<span class="chip">{escape(k["termo"])} · {k["ocorrencias"]}</span>'
            for k in analysis["palavras_chave"]
        )
        boxes.append(f'<div class="box"><h2>Temas</h2><div class="chips">{chips}</div></div>')
    if analysis.get("capitulos"):
        itens = "".join(
            f"<li><strong>{escape(c['start_str'])}</strong> — {escape(c['titulo'])}</li>"
            for c in analysis["capitulos"]
        )
        boxes.append(f'<div class="box"><h2>Capítulos</h2><ul>{itens}</ul></div>')
    if analysis.get("pendencias"):
        itens = "".join(
            f"<li><strong>{escape(p['start_str'])}</strong> {escape(p['texto'])}</li>"
            for p in analysis["pendencias"]
        )
        boxes.append(f'<div class="box"><h2>Possíveis pendências</h2><ul>{itens}</ul></div>')

    linhas = []
    for seg in result.get("segments", []):
        speaker = ""
        if seg.get("speaker"):
            classe = f"s{seg.get('speaker_id', 0) % 8}"
            speaker = f'<span class="who {classe}">{escape(seg["speaker"])}</span>'
        linhas.append(
            f'<div class="seg"><time>{escape(seg["start_str"])}</time>'
            f'<div class="txt">{speaker}{escape(seg["text"])}</div></div>'
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>{_HTML_STYLE}</style></head>
<body><div class="wrap">
<h1>{escape(title)}</h1>
<div class="meta">{meta}</div>
{''.join(boxes)}
<input id="busca" type="search" placeholder="Filtrar trechos...">
<div id="lista">{''.join(linhas)}</div>
</div><script>{_HTML_SCRIPT}</script></body></html>"""


# --- PDF ----------------------------------------------------------------

def build_pdf(result: Dict[str, Any], title: str, path: str) -> None:
    analysis = result.get("analysis") or {}
    blocks: List[Dict[str, Any]] = []

    if analysis.get("resumo"):
        blocks.append({"tipo": "secao", "texto": "Resumo"})
        for item in analysis["resumo"]:
            blocks.append({"tipo": "lista", "texto": f"{item.get('start_str', '')}  {item['texto']}"})
        blocks.append({"tipo": "linha"})

    if analysis.get("palavras_chave"):
        termos = ", ".join(k["termo"] for k in analysis["palavras_chave"])
        blocks.append({"tipo": "secao", "texto": "Temas"})
        blocks.append({"tipo": "paragrafo", "texto": termos})

    if analysis.get("pendencias"):
        blocks.append({"tipo": "secao", "texto": "Possíveis pendências"})
        for item in analysis["pendencias"]:
            blocks.append({"tipo": "lista", "texto": f"{item['start_str']}  {item['texto']}"})
        blocks.append({"tipo": "linha"})

    blocks.append({"tipo": "secao", "texto": "Transcrição"})
    for block in _dialogue_or_paragraphs(result):
        if block.get("speaker"):
            blocks.append({"tipo": "tempo", "texto": f"{block.get('start_str', '')}  {block['speaker']}"})
        elif block.get("start_str"):
            blocks.append({"tipo": "tempo", "texto": block["start_str"]})
        blocks.append({"tipo": "paragrafo", "texto": block["texto"]})

    pdfwriter.build(title, blocks, path, subtitle=" · ".join(_header_lines(result)))


# --- DOCX ---------------------------------------------------------------

_DOCX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="288" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
<w:pPr><w:spacing w:after="80"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="52"/><w:color w:val="1B3A66"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/>
<w:pPr><w:spacing w:after="320"/></w:pPr>
<w:rPr><w:sz w:val="18"/><w:color w:val="6B7280"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="360" w:after="140"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="30"/><w:color w:val="1B3A66"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
<w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="240" w:after="100"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="2B5490"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Fala"><w:name w:val="Fala"/>
<w:pPr><w:spacing w:after="180"/><w:ind w:left="284"/></w:pPr></w:style>
<w:style w:type="character" w:styleId="Tempo"><w:name w:val="Tempo"/>
<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:color w:val="8A94A6"/><w:sz w:val="17"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="Falante"><w:name w:val="Falante"/>
<w:rPr><w:b/><w:color w:val="1B3A66"/></w:rPr></w:style>
</w:styles>"""


def build_docx(result: Dict[str, Any], title: str, path: str) -> None:
    """Escreve um .docx nativo, sem depender de bibliotecas externas.

    Um .docx é um ZIP com XML dentro. Além do documento em si, aqui vão uma
    folha de estilos de verdade (títulos navegáveis no painel do Word, marca de
    tempo em fonte monoespaçada, nome do falante em destaque) e as propriedades
    do arquivo — o que faz o resultado parecer um documento redigido, não uma
    exportação bruta.
    """
    import zipfile
    from xml.sax.saxutils import escape

    def run(text: str, style: str = "") -> str:
        style_xml = f'<w:rPr><w:rStyle w:val="{style}"/></w:rPr>' if style else ""
        return (f'<w:r>{style_xml}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')

    def paragraph(runs: str, style: str = "") -> str:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{style_xml}{runs}</w:p>"

    analysis = result.get("analysis") or {}
    body = [
        paragraph(run(title), "Title"),
        paragraph(run(" · ".join(_header_lines(result))), "Subtitle"),
    ]

    if analysis.get("resumo"):
        body.append(paragraph(run("Resumo"), "Heading1"))
        for item in analysis["resumo"]:
            body.append(paragraph(
                run(f"{item.get('start_str', '')}  ", "Tempo") + run(item["texto"]), "Fala"
            ))

    if analysis.get("palavras_chave"):
        body.append(paragraph(run("Temas"), "Heading1"))
        body.append(paragraph(run(", ".join(k["termo"] for k in analysis["palavras_chave"]))))

    if analysis.get("pendencias"):
        body.append(paragraph(run("Possíveis pendências"), "Heading1"))
        for item in analysis["pendencias"]:
            body.append(paragraph(
                run(f"{item['start_str']}  ", "Tempo") + run(item["texto"]), "Fala"
            ))

    body.append(paragraph(run("Transcrição"), "Heading1"))
    for block in _dialogue_or_paragraphs(result):
        runs = ""
        if block.get("start_str"):
            runs += run(f"{block['start_str']}  ", "Tempo")
        if block.get("speaker"):
            runs += run(f"{block['speaker']}: ", "Falante")
        runs += run(block["texto"])
        body.append(paragraph(runs, "Fala" if block.get("speaker") else ""))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1418"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<dc:creator>Transcritor pt-BR</dc:creator>"
        "<cp:lastModifiedBy>Transcritor pt-BR</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("docProps/core.xml", core)
        docx.writestr("word/_rels/document.xml.rels", document_rels)
        docx.writestr("word/styles.xml", _DOCX_STYLES)
        docx.writestr("word/document.xml", document)


# --- Escrita em lote ----------------------------------------------------

def write_all(result: Dict[str, Any], output_dir: str, base_name: str,
              only: Optional[List[str]] = None) -> Dict[str, str]:
    """Grava todos os formatos em disco e devolve o caminho de cada um.

    Um formato que falhe não derruba os outros: o objetivo é sempre entregar o
    máximo possível de saídas para uma transcrição que já custou minutos de CPU.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths: Dict[str, str] = {}
    analysis = result.get("analysis") or {}

    text_builders = {
        "txt": lambda: build_txt(result),
        "txt_timestamps": lambda: build_txt_with_timestamps(result),
        "srt": lambda: build_srt(result),
        "vtt": lambda: build_vtt(result),
        "ass": lambda: build_ass(result, base_name),
        "md": lambda: build_markdown(result, base_name),
        "csv": lambda: build_csv(result),
        "tsv": lambda: build_tsv(result),
        "json": lambda: build_json(result),
        "html": lambda: build_html(result, base_name),
    }
    if analysis:
        text_builders["resumo"] = lambda: build_summary_markdown(result, base_name)

    for key, builder in text_builders.items():
        if only and key not in only:
            continue
        try:
            content = builder()
        except Exception:  # noqa: BLE001 - um formato quebrado não invalida os outros
            continue
        _label, ext, _mime = FORMATS[key]
        path = os.path.join(output_dir, f"{base_name}{SUFFIXES[key]}{ext}")
        # O BOM faz o Excel abrir o CSV em UTF-8 sem estragar os acentos.
        encoding = "utf-8-sig" if key == "csv" else "utf-8"
        try:
            with open(path, "w", encoding=encoding, newline="") as handle:
                handle.write(content)
            paths[key] = path
        except OSError:
            continue

    binary_builders = {
        "docx": build_docx,
        "pdf": build_pdf,
    }
    for key, builder in binary_builders.items():
        if only and key not in only:
            continue
        path = os.path.join(output_dir, f"{base_name}{SUFFIXES[key]}{FORMATS[key][1]}")
        try:
            builder(result, base_name, path)
            paths[key] = path
        except Exception:  # noqa: BLE001 - idem
            continue

    return paths


def available_formats(paths: Dict[str, str]) -> List[Dict[str, str]]:
    return [
        {"id": key, "label": FORMATS[key][0], "ext": FORMATS[key][1]}
        for key in FORMATS
        if key in paths and os.path.exists(paths[key])
    ]
