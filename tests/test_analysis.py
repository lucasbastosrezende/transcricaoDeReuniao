"""Resumo extrativo, palavras-chave, capítulos e estatísticas."""
import analysis


class TestTokenizacao:
    def test_remove_stopwords(self):
        assert analysis.tokenize("o contrato da prefeitura") == ["contrato", "prefeitura"]

    def test_ignora_acento(self):
        assert "reuniao" in analysis.tokenize("A reunião começou")

    def test_descarta_numeros_soltos(self):
        assert analysis.tokenize("2024 relatório") == ["relatorio"]

    def test_texto_vazio(self):
        assert analysis.tokenize("") == []


class TestResumo:
    def test_devolve_no_maximo_o_pedido(self, segmentos):
        resumo = analysis.summarize(segmentos, 2)
        assert len(resumo) <= 2

    def test_preserva_ordem_cronologica(self, segmentos):
        resumo = analysis.summarize(segmentos, 3)
        tempos = [item["start"] for item in resumo]
        assert tempos == sorted(tempos)

    def test_entrada_vazia(self):
        assert analysis.summarize([], 5) == []

    def test_texto_curto_devolve_tudo_util(self, segmentos):
        resumo = analysis.summarize(segmentos, 50)
        assert len(resumo) == len([s for s in segmentos if len(s["text"].split()) >= 5])


class TestPalavrasChave:
    def test_encontra_o_tema(self, segmentos):
        termos = [k["termo"].lower() for k in analysis.keywords(segmentos, 10)]
        assert any("contrato" in t or "nota" in t or "fiscal" in t for t in termos)

    def test_respeita_o_limite(self, segmentos):
        assert len(analysis.keywords(segmentos, 3)) <= 3

    def test_peso_normalizado(self, segmentos):
        pesos = [k["peso"] for k in analysis.keywords(segmentos, 5)]
        assert pesos and max(pesos) == 1.0
        assert all(0 < p <= 1.0 for p in pesos)


class TestCapitulos:
    def test_texto_curto_nao_gera_capitulos(self, segmentos):
        assert analysis.chapters(segmentos, 90.0) == []

    def test_texto_longo_gera_capitulos(self):
        import ptbr

        temas = [
            "o contrato da prefeitura foi assinado ontem pelo procurador do município",
            "a nota fiscal do fornecedor precisa ser emitida com o código correto",
            "o servidor de banco de dados caiu por falta de memória durante a madrugada",
            "o time de futebol treinou no campo com o novo preparador físico",
        ]
        segmentos = []
        tempo = 0.0
        for indice in range(40):
            texto = temas[indice // 10]
            segmentos.append({
                "id": indice + 1,
                "start": tempo,
                "end": tempo + 12.0,
                "start_str": ptbr.seconds_to_short(tempo),
                "text": texto,
                "confidence": 0.9,
            })
            tempo += 13.0
        capitulos = analysis.chapters(segmentos, 60.0)
        assert len(capitulos) >= 2
        assert all(c["titulo"] for c in capitulos)
        assert [c["start"] for c in capitulos] == sorted(c["start"] for c in capitulos)


class TestPendencias:
    def test_detecta_compromisso(self, segmentos):
        pendencias = analysis.action_items(segmentos)
        assert any("nota fiscal" in p["texto"].lower() for p in pendencias)

    def test_ignora_frase_neutra(self):
        neutras = [{"id": 1, "start": 0, "start_str": "00:00:00",
                    "text": "O céu estava azul naquela manhã de domingo."}]
        assert analysis.action_items(neutras) == []


class TestPerguntas:
    def test_encontra_interrogacao(self, segmentos):
        perguntas = analysis.questions(segmentos)
        assert len(perguntas) == 1
        assert perguntas[0]["texto"].endswith("?")


class TestEstatisticas:
    def test_campos_principais(self, segmentos):
        stats = analysis.statistics(segmentos, 24.0)
        assert stats["palavras"] > 0
        assert stats["frases"] == len(segmentos)
        assert 0 < stats["riqueza_lexical"] <= 1.0
        assert stats["palavras_por_minuto"] > 0

    def test_duracao_zero_nao_divide_por_zero(self, segmentos):
        stats = analysis.statistics(segmentos, 0.0)
        assert stats["proporcao_fala"] == 0.0


class TestFalantes:
    def test_sem_falantes_devolve_vazio(self, segmentos):
        assert analysis.speaker_statistics(segmentos) == []

    def test_com_falantes_agrega(self, segmentos):
        for indice, segmento in enumerate(segmentos):
            segmento["speaker"] = f"Falante {indice % 2 + 1}"
        por_falante = analysis.speaker_statistics(segmentos)
        assert len(por_falante) == 2
        assert por_falante[0]["tempo_s"] >= por_falante[1]["tempo_s"]


class TestFachada:
    def test_bloco_completo(self, segmentos):
        dados = analysis.analyze(segmentos, 24.0)
        for chave in ("resumo", "palavras_chave", "pendencias", "estatisticas"):
            assert chave in dados

    def test_entrada_vazia(self):
        assert analysis.analyze([], 0.0) == {}
