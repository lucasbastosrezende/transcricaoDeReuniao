"""Limpeza de texto, divisão em frases e legendagem em português."""
import config
import ptbr


class TestLimpeza:
    def test_remove_espaco_antes_da_pontuacao(self):
        assert ptbr.clean_text("Olá , tudo bem ?") == "Olá, tudo bem?"

    def test_preserva_decimal_e_hora(self):
        assert ptbr.clean_text("custou 1,5 milhão às 10:30") == "custou 1,5 milhão às 10:30"

    def test_separa_virgula_colada(self):
        assert ptbr.clean_text("bom dia,pessoal") == "bom dia, pessoal"

    def test_normaliza_reticencias(self):
        assert ptbr.clean_text("então . . . vamos") == "então... vamos"

    def test_corta_laco_de_repeticao(self):
        assert ptbr.clean_text("sim sim sim sim") == "sim sim"

    def test_espacamento_de_moeda_e_porcentagem(self):
        assert ptbr.clean_text("R$1000 e 15 %") == "R$ 1000 e 15%"

    def test_texto_vazio_nao_quebra(self):
        assert ptbr.clean_text("") == ""
        assert ptbr.clean_text("   ") == ""


class TestFimDeFrase:
    def test_ponto_final_encerra(self):
        assert ptbr.ends_sentence("reunião.")

    def test_abreviacao_nao_encerra(self):
        assert not ptbr.ends_sentence("Dr.")
        assert not ptbr.ends_sentence("Sra.")
        assert not ptbr.ends_sentence("etc.")

    def test_enumeracao_nao_encerra(self):
        assert not ptbr.ends_sentence("1.")

    def test_inicial_isolada_nao_encerra(self):
        assert not ptbr.ends_sentence("J.")

    def test_interrogacao_encerra(self):
        assert ptbr.ends_sentence("verdade?")


class TestCapitalizacao:
    def test_primeira_letra_da_frase(self):
        assert ptbr.capitalize_sentences("bom dia. tudo certo?") == "Bom dia. Tudo certo?"

    def test_preserva_sigla(self):
        assert ptbr.capitalize_sentences("CNPJ do cliente.") == "CNPJ do cliente."

    def test_nao_capitaliza_depois_de_abreviacao(self):
        # "Dr." não encerra frase, então "marcela" segue no meio dela.
        assert "Dr. marcela" in ptbr.capitalize_sentences("falei com o Dr. marcela ontem.")


class TestVocabulario:
    def test_corrige_caixa(self):
        assert ptbr.apply_vocabulary("enviei pelo siscomex ontem", ["SISCOMEX"]) == \
            "enviei pelo SISCOMEX ontem"

    def test_ignora_acento_na_comparacao(self):
        assert ptbr.apply_vocabulary("falei com a Marcela", ["Márcela"]) == "falei com a Márcela"

    def test_nao_altera_dentro_de_palavra(self):
        assert ptbr.apply_vocabulary("descomprometido", ["metido"]) == "descomprometido"

    def test_expressao_com_espaco(self):
        assert ptbr.apply_vocabulary("mandei a nota fiscal", ["Nota Fiscal"]) == "mandei a Nota Fiscal"

    def test_lista_vazia_nao_muda_nada(self):
        assert ptbr.apply_vocabulary("texto qualquer", []) == "texto qualquer"


class TestAlucinacao:
    def test_bordao_de_legenda(self):
        assert ptbr.is_hallucination("Legendas pela comunidade Amara.org")

    def test_marcador_de_musica(self):
        assert ptbr.is_hallucination("[Música]")

    def test_texto_normal_passa(self):
        assert not ptbr.is_hallucination("O contrato precisa ser revisado.")

    def test_repeticao_longa_de_uma_palavra(self):
        assert ptbr.is_hallucination("sim sim sim sim sim sim sim")

    def test_baixa_probabilidade_com_silencio(self):
        assert ptbr.is_hallucination("alguma coisa", no_speech_prob=0.9, avg_logprob=-1.5)


class TestSegmentacao:
    def test_divide_em_frases_pelo_ponto(self, segmentos):
        divididos = ptbr.split_into_sentences(segmentos)
        assert len(divididos) >= len(segmentos)
        assert all(s["text"] for s in divididos)

    def test_tempos_sao_crescentes(self, segmentos):
        divididos = ptbr.split_into_sentences(segmentos)
        for anterior, seguinte in zip(divididos, divididos[1:]):
            assert anterior["start"] <= seguinte["start"]

    def test_confianca_vem_das_palavras(self, segmentos):
        divididos = ptbr.split_into_sentences(segmentos)
        assert all(0.0 <= s["confidence"] <= 1.0 for s in divididos)

    def test_remove_repeticoes_seguidas(self):
        base = {"start": 0, "end": 1, "start_str": "00:00:00", "end_str": "00:00:01",
                "confidence": 0.9, "words": []}
        entrada = [dict(base, id=i, text="obrigado") for i in range(6)]
        assert len(ptbr.drop_repeated_segments(entrada)) == 2


class TestParagrafos:
    def test_agrupa_frases(self, segmentos):
        paragrafos = ptbr.build_paragraphs(segmentos)
        assert paragrafos
        assert all(p[0].isupper() for p in paragrafos)

    def test_troca_de_falante_quebra_paragrafo(self, segmentos):
        for indice, segmento in enumerate(segmentos):
            segmento["speaker"] = "Falante 1" if indice < 2 else "Falante 2"
        assert len(ptbr.build_paragraphs(segmentos)) >= 2

    def test_dialogo_agrupa_por_voz(self, segmentos):
        for indice, segmento in enumerate(segmentos):
            segmento["speaker"] = "Falante 1" if indice % 2 == 0 else "Falante 2"
            segmento["speaker_id"] = indice % 2
        blocos = ptbr.build_dialogue(segmentos)
        assert len(blocos) == len(segmentos)
        assert blocos[0]["speaker"] == "Falante 1"


class TestTempo:
    def test_formato_srt(self):
        assert ptbr.seconds_to_timestamp(3661.5) == "01:01:01,500"

    def test_formato_vtt(self):
        assert ptbr.seconds_to_timestamp(3661.5, srt=False) == "01:01:01.500"

    def test_formato_curto(self):
        assert ptbr.seconds_to_short(65) == "00:01:05"

    def test_formato_ass(self):
        assert ptbr.seconds_to_ass(3661.5) == "1:01:01.50"

    def test_negativo_vira_zero(self):
        assert ptbr.seconds_to_timestamp(-5) == "00:00:00,000"


class TestLegendas:
    def test_quebra_equilibrada(self):
        texto = "esta é uma linha bem longa que precisa ser dividida em duas partes iguais"
        linhas = ptbr.wrap_balanced(texto, 42, 2)
        assert len(linhas) == 2
        assert all(len(linha) <= 42 for linha in linhas)

    def test_texto_curto_fica_em_uma_linha(self):
        assert ptbr.wrap_balanced("curto", 42, 2) == ["curto"]

    def test_cues_tem_duracao_positiva(self, segmentos):
        cues = ptbr.build_cues(segmentos)
        assert cues
        assert all(c["end"] > c["start"] for c in cues)
        assert all(c["text"] and c["lines"] for c in cues)

    def test_cues_respeitam_limite_de_colunas(self, segmentos):
        cues = ptbr.build_cues(segmentos)
        for cue in cues:
            assert len(cue["lines"]) <= config.SUB_MAX_LINES

    def test_cues_nao_se_sobrepoem(self, segmentos):
        cues = ptbr.build_cues(segmentos)
        for anterior, seguinte in zip(cues, cues[1:]):
            assert anterior["end"] <= seguinte["start"] + 0.001

    def test_relatorio_de_qualidade(self, segmentos):
        relatorio = ptbr.subtitle_report(ptbr.build_cues(segmentos))
        assert relatorio["blocos"] > 0
        assert relatorio["sobrepostas"] == []


class TestVicios:
    def test_remove_marcadores(self):
        limpo = ptbr.strip_fillers("Então, né, o contrato tipo assim precisa de revisão.")
        assert "né" not in limpo
        assert "contrato" in limpo

    def test_mantem_conteudo(self):
        limpo = ptbr.strip_fillers("A nota fiscal foi enviada.")
        assert "nota fiscal" in limpo
