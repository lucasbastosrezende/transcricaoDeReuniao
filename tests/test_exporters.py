"""Geração dos arquivos de saída, do SRT ao PDF."""
import json
import os
import re
import zipfile

import exporters
import pdfwriter


class TestTexto:
    def test_txt_tem_paragrafos(self, resultado):
        texto = exporters.build_txt(resultado)
        assert "contrato" in texto
        assert texto.endswith("\n")

    def test_txt_com_falantes(self, resultado):
        resultado["dialogue"] = [
            {"speaker": "Falante 1", "speaker_id": 0, "start_str": "00:00:00", "texto": "Bom dia."},
            {"speaker": "Falante 2", "speaker_id": 1, "start_str": "00:00:04", "texto": "Bom dia."},
        ]
        assert "Falante 1: Bom dia." in exporters.build_txt(resultado)

    def test_txt_com_tempos(self, resultado):
        linhas = exporters.build_txt_with_timestamps(resultado).strip().split("\n")
        assert len(linhas) == len(resultado["segments"])
        assert linhas[0].startswith("[00:00:00")


class TestLegendas:
    def test_srt_bem_formado(self, resultado):
        srt = exporters.build_srt(resultado)
        assert re.search(r"^1\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n", srt)

    def test_vtt_comeca_com_cabecalho(self, resultado):
        assert exporters.build_vtt(resultado).startswith("WEBVTT")

    def test_vtt_usa_ponto_no_milissegundo(self, resultado):
        assert re.search(r"\d{2}:\d{2}:\d{2}\.\d{3} -->", exporters.build_vtt(resultado))

    def test_ass_tem_estilos_e_eventos(self, resultado):
        ass = exporters.build_ass(resultado, "teste")
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass
        assert ass.count("Dialogue:") == len(resultado["cues"])

    def test_ass_colore_por_falante(self, resultado):
        resultado["diarization"] = {
            "total": 2,
            "falantes": [
                {"id": 0, "nome": "Falante 1", "segundos": 10, "percentual": 60, "trechos": 3},
                {"id": 1, "nome": "Falante 2", "segundos": 6, "percentual": 40, "trechos": 2},
            ],
        }
        ass = exporters.build_ass(resultado, "teste")
        assert "Style: F1," in ass and "Style: F2," in ass


class TestTabelas:
    def test_csv_usa_ponto_e_virgula(self, resultado):
        csv = exporters.build_csv(resultado)
        assert csv.splitlines()[0].count(";") == 7

    def test_csv_usa_virgula_decimal(self, resultado):
        assert re.search(r";\d+,\d{2};", exporters.build_csv(resultado))

    def test_tsv_usa_ponto_decimal(self, resultado):
        linha = exporters.build_tsv(resultado).splitlines()[1]
        assert linha.count("\t") == 4
        assert re.match(r"\d+\.\d{3}\t", linha)

    def test_json_reabre(self, resultado):
        recarregado = json.loads(exporters.build_json(resultado))
        assert recarregado["word_count"] == resultado["word_count"]
        assert len(recarregado["segments"]) == len(resultado["segments"])


class TestDocumentos:
    def test_markdown_tem_titulo(self, resultado):
        md = exporters.build_markdown(resultado, "reuniao")
        assert md.startswith("# reuniao")
        assert "## Transcrição" in md

    def test_resumo_lista_pendencias(self, resultado):
        md = exporters.build_summary_markdown(resultado, "reuniao")
        assert "Possíveis pendências" in md
        assert "- [ ]" in md

    def test_html_e_autocontido(self, resultado):
        pagina = exporters.build_html(resultado, "reuniao")
        assert pagina.startswith("<!DOCTYPE html>")
        assert "<style>" in pagina and "http://" not in pagina.split("<style>")[0]
        assert "contrato" in pagina

    def test_html_escapa_conteudo(self, resultado):
        resultado["segments"][0]["text"] = "<script>alert(1)</script>"
        pagina = exporters.build_html(resultado, "x")
        assert "<script>alert(1)</script>" not in pagina.split("<script>")[-1]
        assert "&lt;script&gt;" in pagina

    def test_docx_e_um_zip_valido(self, resultado, tmp_path):
        destino = str(tmp_path / "saida.docx")
        exporters.build_docx(resultado, "reuniao", destino)
        with zipfile.ZipFile(destino) as pacote:
            nomes = pacote.namelist()
            assert "word/document.xml" in nomes
            assert "word/styles.xml" in nomes
            assert "[Content_Types].xml" in nomes
            documento = pacote.read("word/document.xml").decode("utf-8")
        assert "<w:body>" in documento
        assert "reuniao" in documento

    def test_docx_escapa_xml(self, resultado, tmp_path):
        resultado["paragraphs"] = ["Contrato & cia <teste>"]
        resultado["dialogue"] = []
        destino = str(tmp_path / "escape.docx")
        exporters.build_docx(resultado, "t", destino)
        with zipfile.ZipFile(destino) as pacote:
            documento = pacote.read("word/document.xml").decode("utf-8")
        assert "&amp;" in documento and "&lt;teste&gt;" in documento

    def test_pdf_tem_cabecalho_e_fim(self, resultado, tmp_path):
        destino = str(tmp_path / "saida.pdf")
        exporters.build_pdf(resultado, "reuniao", destino)
        with open(destino, "rb") as arquivo:
            conteudo = arquivo.read()
        assert conteudo.startswith(b"%PDF-1.4")
        assert conteudo.rstrip().endswith(b"%%EOF")
        assert b"/Type /Catalog" in conteudo
        assert b"xref" in conteudo


class TestEscritaEmLote:
    def test_gera_todos_os_formatos(self, resultado, tmp_path):
        caminhos = exporters.write_all(resultado, str(tmp_path), "reuniao")
        assert set(caminhos) >= {"txt", "srt", "vtt", "ass", "csv", "tsv", "json", "md", "html", "docx", "pdf"}
        assert all(os.path.exists(caminho) for caminho in caminhos.values())

    def test_respeita_a_lista_pedida(self, resultado, tmp_path):
        caminhos = exporters.write_all(resultado, str(tmp_path), "x", only=["txt", "srt"])
        assert set(caminhos) == {"txt", "srt"}

    def test_formatos_disponiveis(self, resultado, tmp_path):
        caminhos = exporters.write_all(resultado, str(tmp_path), "x", only=["txt"])
        disponiveis = exporters.available_formats(caminhos)
        assert disponiveis == [{"id": "txt", "label": exporters.FORMATS["txt"][0], "ext": ".txt"}]

    def test_csv_gravado_com_bom(self, resultado, tmp_path):
        caminhos = exporters.write_all(resultado, str(tmp_path), "x", only=["csv"])
        with open(caminhos["csv"], "rb") as arquivo:
            assert arquivo.read(3) == b"\xef\xbb\xbf"


class TestPdfWriter:
    def test_largura_cresce_com_o_texto(self):
        assert pdfwriter.text_width("mmmm", 10) > pdfwriter.text_width("iiii", 10)

    def test_acento_tem_largura_da_letra_base(self):
        assert pdfwriter.text_width("á", 10) == pdfwriter.text_width("a", 10)

    def test_quebra_respeita_a_largura(self):
        linhas = pdfwriter.wrap("palavra " * 40, 200, 10)
        assert len(linhas) > 1
        assert all(pdfwriter.text_width(linha, 10) <= 200.5 for linha in linhas)

    def test_palavra_gigante_e_cortada(self):
        linhas = pdfwriter.wrap("x" * 400, 100, 10)
        assert len(linhas) > 1

    def test_paginacao(self, tmp_path):
        documento = pdfwriter.Document("Longo")
        for i in range(300):
            documento.paragraph(f"Linha número {i} com algum texto de enchimento.")
        assert len(documento.pages) > 1
        destino = str(tmp_path / "longo.pdf")
        documento.save(destino)
        assert os.path.getsize(destino) > 1000
