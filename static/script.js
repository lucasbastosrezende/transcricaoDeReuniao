/* ============================================================
   Transcritor pt-BR — lógica da interface

   Três telas (envio, progresso, resultado) mais duas gavetas
   (histórico e busca global). O estado do lote em andamento vive
   em `estado.lote`; o servidor processa um arquivo por vez, então
   a interface acompanha o atual por SSE e o resto por sondagem.
   ============================================================ */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const CORES_FALANTE = 8;

  const estado = {
    arquivos: [],
    lote: [],
    indiceAtual: 0,
    jobId: null,
    resultado: null,
    formatos: [],
    sistema: null,
    trechoAtivo: -1,
    envio: null,
    fonteEventos: null,
    sondagem: null,
    edicoes: new Map(),
    falantesOcultos: new Set(),
    onda: [],
  };

  // ---------- utilidades ----------
  const formatarBytes = (b) => {
    if (!b) return "0 B";
    const unidades = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(b) / Math.log(1024));
    return `${(b / 1024 ** i).toFixed(i ? 1 : 0)} ${unidades[i]}`;
  };

  const formatarTempo = (s) => {
    if (!isFinite(s) || s < 0) s = 0;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const seg = Math.floor(s % 60);
    const base = `${String(m).padStart(2, "0")}:${String(seg).padStart(2, "0")}`;
    return h ? `${h}:${base}` : base;
  };

  const formatarDuracao = (s) => {
    if (!s) return "—";
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return h ? `${h}h ${m}min` : `${Math.max(1, m)}min`;
  };

  const escapar = (t) => {
    const div = document.createElement("div");
    div.textContent = t == null ? "" : t;
    return div.innerHTML;
  };

  const numero = (n) => (n || 0).toLocaleString("pt-BR");

  let temporizadorAviso;
  function avisar(mensagem) {
    const el = $("aviso");
    el.textContent = mensagem;
    el.classList.remove("oculto");
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(() => el.classList.add("oculto"), 3600);
  }

  const TELAS = ["telaEnvio", "telaProgresso", "telaResultado", "telaHistorico", "telaBusca"];
  function mostrarTela(id) {
    TELAS.forEach((t) => $(t).classList.toggle("oculto", t !== id));
  }

  // ---------- tema ----------
  const temaSalvo = localStorage.getItem("transcritor-tema");
  if (temaSalvo) document.documentElement.dataset.tema = temaSalvo;
  $("btnTema").textContent = document.documentElement.dataset.tema === "claro" ? "☀️" : "🌙";
  $("btnTema").addEventListener("click", () => {
    const novo = document.documentElement.dataset.tema === "claro" ? "escuro" : "claro";
    document.documentElement.dataset.tema = novo;
    localStorage.setItem("transcritor-tema", novo);
    $("btnTema").textContent = novo === "claro" ? "☀️" : "🌙";
    desenharOnda();
  });

  // ---------- informações do sistema ----------
  async function carregarSistema() {
    try {
      const dados = await (await fetch("/api/system")).json();
      estado.sistema = dados;

      const hw = dados.hardware;
      $("topoHardware").textContent = hw.gpu
        ? `Placa de vídeo detectada · ${hw.compute_type} · processamento acelerado`
        : `${hw.cpu_threads} núcleos de CPU · ${hw.compute_type} · 100% offline e gratuito`;

      $("avisoFfmpeg").classList.toggle("oculto", dados.ffmpeg);

      const selModelo = $("selModelo");
      selModelo.innerHTML = dados.modelos
        .map((m) => {
          const marca = m.baixado ? "" : " — download na 1ª vez";
          const rec = m.padrao ? " (recomendado)" : "";
          return `<option value="${m.id}"${m.padrao ? " selected" : ""}>${m.nome}${rec}${marca}</option>`;
        })
        .join("");

      const selPerfil = $("selPerfil");
      selPerfil.innerHTML = dados.perfis
        .map((p) => `<option value="${p.id}"${p.padrao ? " selected" : ""}>${p.nome}</option>`)
        .join("");

      const atualizarDicas = () => {
        const m = dados.modelos.find((x) => x.id === selModelo.value);
        const p = dados.perfis.find((x) => x.id === selPerfil.value);
        $("dicaModelo").textContent = m ? `${m.resumo} · ${m.params} · ~${m.ram_gb} GB de RAM` : "";
        $("dicaPerfil").textContent = p ? p.resumo : "";
      };
      selModelo.addEventListener("change", atualizarDicas);
      selPerfil.addEventListener("change", atualizarDicas);
      atualizarDicas();

      const recursos = dados.recursos || {};
      $("optFalantes").checked = recursos.diarizacao !== false;
      $("optAnalise").checked = recursos.analise !== false;
      $("btnBuscaGlobal").classList.toggle("oculto", !recursos.busca_global);

      $("entradaArquivo").accept = dados.extensoes.join(",");
    } catch (e) {
      $("topoHardware").textContent = "Não foi possível ler as informações do sistema.";
    }
  }

  // ---------- seleção de arquivos ----------
  const areaSolta = $("areaSolta");

  ["dragenter", "dragover"].forEach((ev) =>
    areaSolta.addEventListener(ev, (e) => {
      e.preventDefault();
      areaSolta.classList.add("sobre");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    areaSolta.addEventListener(ev, (e) => {
      e.preventDefault();
      areaSolta.classList.remove("sobre");
    })
  );
  areaSolta.addEventListener("drop", (e) => adicionarArquivos(e.dataTransfer.files));
  areaSolta.addEventListener("click", () => $("entradaArquivo").click());
  areaSolta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      $("entradaArquivo").click();
    }
  });
  $("entradaArquivo").addEventListener("change", (e) => adicionarArquivos(e.target.files));

  function adicionarArquivos(lista) {
    const aceitas = (estado.sistema && estado.sistema.extensoes) || null;
    let recusados = 0;
    Array.from(lista || []).forEach((arquivo) => {
      const ext = "." + (arquivo.name.split(".").pop() || "").toLowerCase();
      if (aceitas && !aceitas.includes(ext)) {
        recusados++;
        return;
      }
      const repetido = estado.arquivos.some(
        (a) => a.name === arquivo.name && a.size === arquivo.size
      );
      if (!repetido) estado.arquivos.push(arquivo);
    });
    if (recusados) avisar(`${recusados} arquivo(s) em formato não suportado foram ignorados.`);
    desenharArquivos();
  }

  function desenharArquivos() {
    const lista = $("listaArquivos");
    lista.innerHTML = "";
    lista.classList.toggle("oculto", !estado.arquivos.length);
    areaSolta.classList.toggle("compacta", estado.arquivos.length > 0);

    estado.arquivos.forEach((arquivo, indice) => {
      const ext = (arquivo.name.split(".").pop() || "").toUpperCase();
      const item = document.createElement("div");
      item.className = "arquivo";
      item.innerHTML =
        `<span class="arquivo-selo">${escapar(ext.slice(0, 4))}</span>` +
        `<div class="arquivo-dados"><strong>${escapar(arquivo.name)}</strong>` +
        `<span class="tenue">${formatarBytes(arquivo.size)}</span></div>` +
        `<button type="button" class="btn-icone" title="Remover">✕</button>`;
      item.querySelector("button").addEventListener("click", () => {
        estado.arquivos.splice(indice, 1);
        desenharArquivos();
      });
      lista.appendChild(item);
    });

    if (estado.arquivos.length > 1) {
      const total = estado.arquivos.reduce((soma, a) => soma + a.size, 0);
      const rodape = document.createElement("p");
      rodape.className = "tenue";
      rodape.textContent = `${estado.arquivos.length} arquivos · ${formatarBytes(total)} no total. Serão processados em sequência.`;
      lista.appendChild(rodape);
    }

    $("btnIniciar").disabled = !estado.arquivos.length;
    $("btnIniciar").textContent =
      estado.arquivos.length > 1
        ? `Transcrever ${estado.arquivos.length} arquivos`
        : "Iniciar transcrição";
  }

  // ---------- envio ----------
  $("formulario").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!estado.arquivos.length) return;

    const dados = new FormData();
    estado.arquivos.forEach((arquivo) => dados.append("files", arquivo));
    dados.append("model_name", $("selModelo").value);
    dados.append("profile", $("selPerfil").value);
    dados.append("language", $("selIdioma").value);
    dados.append("vocabulary", $("campoVocabulario").value);
    dados.append("diarizar", $("optFalantes").checked);
    dados.append("analisar", $("optAnalise").checked);
    dados.append("remover_vicios", $("optVicios").checked);

    mostrarTela("telaProgresso");
    reiniciarProgresso();

    // XMLHttpRequest em vez de fetch: só ele reporta o progresso do upload,
    // que importa muito em arquivos de centenas de megabytes.
    const req = new XMLHttpRequest();
    estado.envio = req;
    req.open("POST", "/api/transcribe");

    req.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      const pct = (ev.loaded / ev.total) * 100;
      definirProgresso(pct * 0.15, "Enviando os arquivos",
        `${formatarBytes(ev.loaded)} de ${formatarBytes(ev.total)}`);
    };

    req.onload = () => {
      estado.envio = null;
      let resposta;
      try {
        resposta = JSON.parse(req.responseText);
      } catch (erro) {
        resposta = {};
      }
      if (req.status >= 400) {
        falhar(resposta.detail || "Não foi possível iniciar a transcrição.");
        return;
      }
      (resposta.recusadas || []).forEach((r) => avisar(`${r.filename}: ${r.motivo}`));

      estado.lote = (resposta.tarefas || [{ job_id: resposta.job_id, filename: resposta.filename }])
        .map((t) => ({ ...t, status: "na_fila", percent: 0, stage: "Na fila" }));
      estado.indiceAtual = 0;
      desenharFila();
      acompanharAtual();
      iniciarSondagem();
    };

    req.onerror = () => {
      estado.envio = null;
      falhar("A conexão com o servidor foi perdida durante o envio.");
    };

    req.send(dados);
  });

  function falhar(mensagem) {
    avisar(mensagem);
    pararSondagem();
    mostrarTela("telaEnvio");
  }

  function reiniciarProgresso() {
    $("feedCorpo").innerHTML = '<p class="tenue">Os trechos aparecem aqui conforme são reconhecidos.</p>';
    $("feedContagem").textContent = "0 trechos";
    $("progEta").textContent = "";
    $("progArquivo").textContent = "";
    $("filaLote").classList.add("oculto");
    definirProgresso(0, "Preparando...", "Enviando os arquivos para o servidor.");
  }

  function definirProgresso(pct, etapa, detalhe) {
    $("progBarra").style.width = `${pct}%`;
    $("progAnel").style.setProperty("--pct", pct);
    $("progNumero").textContent = `${Math.round(pct)}%`;
    if (etapa) $("progEtapa").textContent = etapa;
    if (detalhe !== undefined) $("progDetalhe").textContent = detalhe;
  }

  // ---------- fila do lote ----------
  function desenharFila() {
    const caixa = $("filaLote");
    caixa.classList.toggle("oculto", estado.lote.length < 2);
    if (estado.lote.length < 2) return;

    const prontos = estado.lote.filter((t) => t.status === "concluido").length;
    $("filaContagem").textContent = `${prontos} de ${estado.lote.length} prontos`;
    $("filaItens").innerHTML = estado.lote
      .map((t, i) => {
        const classe =
          t.status === "concluido" ? "selo-ok"
            : t.status === "erro" ? "selo-erro"
            : i === estado.indiceAtual ? "selo-ativo" : "selo-neutro";
        const rotulo =
          t.status === "concluido" ? "pronto"
            : t.status === "erro" ? "erro"
            : i === estado.indiceAtual ? `${Math.round(t.percent || 0)}%` : "na fila";
        const abrir = t.status === "concluido"
          ? `<button type="button" class="btn btn-suave btn-pequeno" data-abrir-lote="${t.job_id}">Abrir</button>`
          : "";
        return (
          `<div class="fila-item"><span class="fila-nome">${escapar(t.filename)}</span>` +
          `<span class="selo ${classe}">${rotulo}</span>${abrir}</div>`
        );
      })
      .join("");

    $("filaItens").querySelectorAll("[data-abrir-lote]").forEach((botao) =>
      botao.addEventListener("click", () => abrirTarefa(botao.dataset.abrirLote))
    );
  }

  function iniciarSondagem() {
    pararSondagem();
    if (estado.lote.length < 2) return;
    estado.sondagem = setInterval(async () => {
      try {
        const { jobs } = await (await fetch("/api/jobs?limit=200")).json();
        const porId = new Map(jobs.map((j) => [j.job_id, j]));
        estado.lote.forEach((t) => {
          const atual = porId.get(t.job_id);
          if (atual) {
            t.status = atual.status;
            t.percent = atual.percent;
            t.stage = atual.stage;
          }
        });
        desenharFila();
      } catch (e) {
        /* uma sondagem perdida não é motivo para interromper nada */
      }
    }, 4000);
  }

  function pararSondagem() {
    if (estado.sondagem) clearInterval(estado.sondagem);
    estado.sondagem = null;
  }

  function acompanharAtual() {
    const tarefa = estado.lote[estado.indiceAtual];
    if (!tarefa) return;
    estado.jobId = tarefa.job_id;
    $("progArquivo").textContent =
      estado.lote.length > 1
        ? `${estado.indiceAtual + 1}/${estado.lote.length} · ${tarefa.filename}`
        : tarefa.filename;
    $("feedCorpo").innerHTML = '<p class="tenue">Os trechos aparecem aqui conforme são reconhecidos.</p>';
    $("feedContagem").textContent = "0 trechos";
    definirProgresso(15, "Na fila", "Aguardando o processador...");
    acompanhar(tarefa.job_id);
  }

  function proximaTarefa() {
    const restantes = estado.lote.slice(estado.indiceAtual + 1);
    const proximo = restantes.findIndex((t) => t.status !== "concluido" && t.status !== "erro" && t.status !== "cancelado");
    if (proximo === -1) return false;
    estado.indiceAtual = estado.indiceAtual + 1 + proximo;
    acompanharAtual();
    return true;
  }

  // ---------- acompanhamento em tempo real ----------
  function acompanhar(jobId) {
    if (estado.fonteEventos) estado.fonteEventos.close();
    const fonte = new EventSource(`/api/events/${jobId}`);
    estado.fonteEventos = fonte;
    let primeiroTrecho = true;

    fonte.onmessage = (evento) => {
      const dados = JSON.parse(evento.data);

      if (dados.type === "status") {
        // 15% da barra pertencem ao upload; o resto ao processamento.
        const pct = 15 + dados.percent * 0.85;
        const detalhe = dados.queue_position > 1
          ? `${dados.queue_position - 1} tarefa(s) na frente desta.`
          : dados.detail;
        definirProgresso(pct, dados.stage, detalhe);
        $("progEta").textContent =
          dados.eta_seconds > 0 ? `Tempo restante estimado: ${formatarTempo(dados.eta_seconds)}` : "";
        const tarefa = estado.lote[estado.indiceAtual];
        if (tarefa) {
          tarefa.percent = dados.percent;
          tarefa.status = dados.status;
          desenharFila();
        }
      }

      if (dados.type === "segmentos") {
        const corpo = $("feedCorpo");
        if (primeiroTrecho) {
          corpo.innerHTML = "";
          primeiroTrecho = false;
        }
        dados.segments.forEach((s) => {
          const linha = document.createElement("div");
          linha.className = "feed-linha";
          linha.innerHTML = `<span class="mono">${s.start_str}</span><span>${escapar(s.text)}</span>`;
          corpo.appendChild(linha);
        });
        corpo.scrollTop = corpo.scrollHeight;
        $("feedContagem").textContent = `${corpo.children.length} trechos`;
      }

      if (dados.type === "fim") {
        fonte.close();
        estado.fonteEventos = null;
        const tarefa = estado.lote[estado.indiceAtual];
        if (tarefa) tarefa.status = dados.status;
        desenharFila();

        if (dados.status === "erro") avisar(dados.error || "A transcrição falhou.");
        if (dados.status === "cancelado") avisar("Transcrição cancelada.");

        if (proximaTarefa()) return;
        pararSondagem();

        const concluidas = estado.lote.filter((t) => t.status === "concluido");
        if (dados.status === "concluido") {
          estado.formatos = dados.formats || [];
          mostrarResultado(dados.result, dados);
        } else if (concluidas.length) {
          abrirTarefa(concluidas[concluidas.length - 1].job_id);
        } else {
          mostrarTela("telaEnvio");
        }
      }
    };

    fonte.onerror = () => {
      fonte.close();
      estado.fonteEventos = null;
      // Cai para consulta pontual: a tarefa continua rodando no servidor.
      fetch(`/api/progress/${jobId}`)
        .then((r) => r.json())
        .then((j) => {
          if (j.status === "concluido") {
            estado.formatos = j.formats || [];
            mostrarResultado(j.result, j);
          } else if (j.status !== "processando" && j.status !== "na_fila") {
            falhar(j.error || "Conexão interrompida.");
          } else {
            setTimeout(() => acompanhar(jobId), 2000);
          }
        })
        .catch(() => falhar("Conexão com o servidor perdida."));
    };
  }

  $("btnCancelar").addEventListener("click", async () => {
    if (estado.envio) {
      estado.envio.abort();
      estado.envio = null;
      mostrarTela("telaEnvio");
      return;
    }
    if (!estado.jobId) return;
    $("btnCancelar").disabled = true;
    await fetch(`/api/cancel/${estado.jobId}`, { method: "POST" }).catch(() => {});
    setTimeout(() => ($("btnCancelar").disabled = false), 1500);
  });

  // ---------- resultado ----------
  async function abrirTarefa(jobId) {
    try {
      const dados = await (await fetch(`/api/progress/${jobId}`)).json();
      estado.jobId = dados.job_id;
      estado.formatos = dados.formats || [];
      mostrarResultado(dados.result, dados);
    } catch (e) {
      avisar("Não foi possível abrir esta transcrição.");
    }
  }

  function mostrarResultado(resultado, meta = {}) {
    if (!resultado || !Array.isArray(resultado.segments)) {
      return falhar("O resultado desta transcrição não está mais disponível.");
    }
    resultado.cues = resultado.cues || [];
    estado.resultado = resultado;
    estado.formatos = meta.formats || estado.formatos;
    estado.edicoes.clear();
    estado.falantesOcultos.clear();
    estado.onda = resultado.waveform || [];
    mostrarTela("telaResultado");

    $("resTitulo").textContent = meta.filename ? `Transcrição · ${meta.filename}` : "Transcrição concluída";
    const fator = meta.speed_factor || resultado.speed_factor;
    $("resArquivo").textContent =
      `${resultado.model} · perfil ${resultado.profile} · idioma ${resultado.language}` +
      (fator ? ` · ${fator}× mais rápido que o tempo real` : "") +
      (resultado.discarded_segments ? ` · ${resultado.discarded_segments} trecho(s) descartado(s) por alucinação` : "");

    desenharMetricas(resultado, meta);
    desenharTexto(resultado);
    desenharFiltros(resultado);
    desenharTrechos();
    desenharLegendas(resultado);
    desenharResumo(resultado);
    desenharDados(resultado, meta);
    montarMenuDownloads();
    prepararPlayer(resultado);
    trocarAba("abaTexto");
    $("campoBusca").value = "";
    $("buscaResultado").textContent = "";
    marcarEdicoes();
  }

  function desenharMetricas(resultado, meta) {
    const confianca = Math.round((resultado.avg_confidence || 0) * 100);
    const falantes = (resultado.diarization || {}).total || 0;
    const linhas = [
      ["Duração", formatarDuracao(resultado.duration)],
      ["Palavras", numero(resultado.word_count)],
      ["Trechos", numero(resultado.segments.length)],
      ["Confiança", `${confianca}%`],
      ["Processamento", meta.elapsed ? formatarTempo(meta.elapsed) : formatarTempo(resultado.processing_seconds)],
    ];
    if (falantes) linhas.push(["Falantes", falantes]);
    $("resMetricas").innerHTML = linhas
      .map(
        ([nome, valor]) =>
          `<div class="metrica"><div class="metrica-valor">${valor}</div><div class="metrica-nome">${nome}</div></div>`
      )
      .join("");
  }

  function desenharTexto(resultado) {
    const blocos = resultado.dialogue && resultado.dialogue.length
      ? resultado.dialogue.map(
          (b) =>
            `<p><span class="quem s${(b.speaker_id || 0) % CORES_FALANTE}">${escapar(b.speaker || "")}</span> ${escapar(b.texto)}</p>`
        )
      : (resultado.paragraphs || [resultado.plain_text]).map((p) => `<p>${escapar(p)}</p>`);
    $("textoCompleto").innerHTML = blocos.join("");
  }

  function desenharFiltros(resultado) {
    const falantes = (resultado.diarization || {}).falantes || [];
    $("filtros").classList.toggle("oculto", !falantes.length);
    $("chipsFalantes").innerHTML = falantes
      .map(
        (f) =>
          `<button type="button" class="chip s${f.id % CORES_FALANTE} ativo" data-falante="${escapar(f.nome)}">` +
          `${escapar(f.nome)} <span class="tenue">${f.percentual}%</span></button>`
      )
      .join("");
    $("chipsFalantes").querySelectorAll("[data-falante]").forEach((chip) =>
      chip.addEventListener("click", () => {
        const nome = chip.dataset.falante;
        if (estado.falantesOcultos.has(nome)) estado.falantesOcultos.delete(nome);
        else estado.falantesOcultos.add(nome);
        chip.classList.toggle("ativo", !estado.falantesOcultos.has(nome));
        desenharTrechos($("campoBusca").value);
      })
    );
  }

  function desenharTrechos(filtro = "") {
    const lista = $("listaTrechos");
    const alvo = filtro.trim().toLowerCase();
    const soBaixa = $("optSoBaixa").checked;
    const limite = 0.55;
    lista.innerHTML = "";
    let achados = 0;

    estado.resultado.segments.forEach((s, indice) => {
      if (alvo && !s.text.toLowerCase().includes(alvo)) return;
      if (soBaixa && s.confidence >= limite) return;
      if (s.speaker && estado.falantesOcultos.has(s.speaker)) return;
      achados++;

      const item = document.createElement("div");
      item.className = "trecho" + (s.confidence < limite ? " trecho-baixa" : "");
      item.dataset.indice = indice;
      const quem = s.speaker
        ? `<div class="quem s${(s.speaker_id || 0) % CORES_FALANTE}">${escapar(s.speaker)}</div>`
        : "";
      const texto = alvo ? realcar(s.text, alvo) : escapar(s.text);
      item.innerHTML =
        `<div class="trecho-tempo" title="Confiança ${Math.round(s.confidence * 100)}% — clique para ouvir">` +
        `${s.start_str}${quem}</div>` +
        `<div class="trecho-corpo">` +
        `<div class="trecho-texto" contenteditable="true" spellcheck="true" data-id="${s.id}">${texto}</div>` +
        (s.confidence < limite ? '<div class="trecho-aviso">⚠ baixa confiança — vale conferir no áudio</div>' : "") +
        "</div>";

      item.querySelector(".trecho-tempo").addEventListener("click", () => irPara(s.start));
      const campo = item.querySelector(".trecho-texto");
      campo.addEventListener("input", () => {
        const novo = campo.textContent.trim();
        if (novo && novo !== s.text) estado.edicoes.set(s.id, novo);
        else estado.edicoes.delete(s.id);
        marcarEdicoes();
      });
      campo.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          campo.blur();
        }
      });
      lista.appendChild(item);
    });

    if (!achados) lista.innerHTML = '<p class="tenue">Nenhum trecho encontrado com estes filtros.</p>';
    return achados;
  }

  function marcarEdicoes() {
    const botao = $("btnSalvarEdicao");
    const total = estado.edicoes.size;
    botao.classList.toggle("oculto", total === 0);
    botao.textContent = total === 1 ? "Salvar 1 correção" : `Salvar ${total} correções`;
  }

  $("optSoBaixa").addEventListener("change", () => desenharTrechos($("campoBusca").value));

  $("btnSalvarEdicao").addEventListener("click", async () => {
    if (!estado.edicoes.size || !estado.jobId) return;
    const botao = $("btnSalvarEdicao");
    botao.disabled = true;
    botao.textContent = "Salvando...";
    try {
      const corpo = {
        segments: Array.from(estado.edicoes, ([id, text]) => ({ id, text })),
      };
      const resposta = await fetch(`/api/jobs/${estado.jobId}/text`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corpo),
      });
      const dados = await resposta.json();
      if (!resposta.ok) throw new Error(dados.detail || "Falha ao salvar.");
      if (!dados.salvo) {
        avisar(dados.motivo || "Nada mudou.");
      } else {
        avisar(`${dados.trechos_alterados} trecho(s) salvos. Arquivos regerados.`);
        estado.edicoes.clear();
        await abrirTarefa(estado.jobId);
      }
    } catch (erro) {
      avisar(erro.message || "Não foi possível salvar as correções.");
    } finally {
      botao.disabled = false;
      marcarEdicoes();
    }
  });

  function desenharLegendas(resultado) {
    const qa = resultado.legendas_qa || {};
    $("qaLegendas").innerHTML = qa.blocos
      ? `<span>${qa.blocos} blocos</span><span>${qa.cps_medio} caracteres por segundo em média</span>` +
        (qa.acima_do_cps && qa.acima_do_cps.length
          ? `<span class="alerta-inline">${qa.acima_do_cps.length} bloco(s) rápidos demais para ler</span>`
          : "<span class=\"ok-inline\">ritmo de leitura dentro do recomendado</span>")
      : "";

    const lista = $("listaLegendas");
    lista.innerHTML = "";
    const rapidas = new Set(qa.acima_do_cps || []);
    (resultado.cues || []).forEach((c) => {
      const item = document.createElement("div");
      item.className = "trecho" + (rapidas.has(c.id) ? " trecho-baixa" : "");
      item.innerHTML =
        `<div class="trecho-tempo">${c.id}<br>${formatarTempo(c.start)}<br><span class="tenue">${c.cps || 0} cps</span></div>` +
        `<div class="trecho-linhas">${c.lines.map((l) => `<div class="trecho-texto">${escapar(l)}</div>`).join("")}</div>`;
      item.addEventListener("click", () => irPara(c.start));
      lista.appendChild(item);
    });
    if (!lista.children.length) lista.innerHTML = '<p class="tenue">Sem blocos de legenda.</p>';
  }

  function desenharResumo(resultado) {
    const analise = resultado.analysis || {};
    const alvo = $("conteudoResumo");
    if (!analise.resumo && !analise.capitulos) {
      alvo.innerHTML = '<p class="tenue">A análise de conteúdo estava desligada nesta transcrição.</p>';
      return;
    }

    const secoes = [];

    if (analise.resumo && analise.resumo.length) {
      secoes.push(
        `<div class="bloco"><h3>Em poucas linhas</h3><ul class="lista-tempo">` +
          analise.resumo
            .map(
              (i) =>
                `<li><button type="button" class="ir" data-ir="${i.start}">${i.start_str}</button> ${escapar(i.texto)}</li>`
            )
            .join("") +
          `</ul></div>`
      );
    }

    if (analise.palavras_chave && analise.palavras_chave.length) {
      secoes.push(
        `<div class="bloco"><h3>Temas dominantes</h3><div class="chips">` +
          analise.palavras_chave
            .map(
              (k) =>
                `<span class="chip" style="--peso:${k.peso}">${escapar(k.termo)} <span class="tenue">${k.ocorrencias}×</span></span>`
            )
            .join("") +
          `</div></div>`
      );
    }

    if (analise.capitulos && analise.capitulos.length) {
      secoes.push(
        `<div class="bloco"><h3>Capítulos</h3><ul class="lista-tempo">` +
          analise.capitulos
            .map(
              (c) =>
                `<li><button type="button" class="ir" data-ir="${c.start}">${c.start_str}</button> ` +
                `<strong>${escapar(c.titulo)}</strong><br><span class="tenue">${escapar(c.abertura)}…</span></li>`
            )
            .join("") +
          `</ul></div>`
      );
    }

    if (analise.pendencias && analise.pendencias.length) {
      secoes.push(
        `<div class="bloco"><h3>Possíveis pendências</h3>` +
          `<p class="tenue">Frases que soam como compromisso assumido. Confira no áudio antes de cobrar alguém.</p>` +
          `<ul class="lista-tempo">` +
          analise.pendencias
            .map(
              (p) =>
                `<li><button type="button" class="ir" data-ir="${p.start}">${p.start_str}</button> ` +
                (p.falante ? `<strong>${escapar(p.falante)}:</strong> ` : "") +
                escapar(p.texto) +
                `</li>`
            )
            .join("") +
          `</ul></div>`
      );
    }

    if (analise.perguntas && analise.perguntas.length) {
      secoes.push(
        `<div class="bloco"><h3>Perguntas feitas</h3><ul class="lista-tempo">` +
          analise.perguntas
            .map(
              (p) =>
                `<li><button type="button" class="ir" data-ir="${p.start}">${p.start_str}</button> ${escapar(p.texto)}</li>`
            )
            .join("") +
          `</ul></div>`
      );
    }

    alvo.innerHTML = secoes.join("");
    alvo.querySelectorAll("[data-ir]").forEach((botao) =>
      botao.addEventListener("click", () => irPara(parseFloat(botao.dataset.ir)))
    );
  }

  function desenharDados(resultado, meta) {
    const analise = resultado.analysis || {};
    const stats = analise.estatisticas || {};
    const info = resultado.media_info || {};
    const secoes = [];

    const linhas = [
      ["Palavras por minuto", stats.palavras_por_minuto || "—"],
      ["Tempo de fala", formatarTempo(stats.tempo_fala_s || resultado.speech_duration)],
      ["Silêncio", formatarTempo(stats.tempo_silencio_s)],
      ["Proporção de fala", stats.proporcao_fala ? `${Math.round(stats.proporcao_fala * 100)}%` : "—"],
      ["Vocabulário distinto", numero(stats.vocabulario_unico)],
      ["Riqueza lexical", stats.riqueza_lexical || "—"],
      ["Maior pausa", `${stats.maior_pausa_s || 0} s`],
      ["Vícios de linguagem", numero(stats.vicios_de_linguagem)],
      ["Trechos de baixa confiança", numero(stats.trechos_baixa_confianca)],
      ["Idioma detectado", `${resultado.language} (${Math.round((resultado.language_probability || 1) * 100)}%)`],
    ];
    secoes.push(
      `<div class="bloco"><h3>A gravação em números</h3><table class="tabela">` +
        linhas.map(([n, v]) => `<tr><td>${n}</td><td class="mono">${v}</td></tr>`).join("") +
        `</table></div>`
    );

    if (analise.por_falante && analise.por_falante.length) {
      secoes.push(
        `<div class="bloco"><h3>Participação por falante</h3><table class="tabela">` +
          `<tr><th>Falante</th><th>Tempo</th><th>Palavras</th><th>Ritmo</th><th>Termos</th></tr>` +
          analise.por_falante
            .map(
              (f) =>
                `<tr><td>${escapar(f.nome)}</td><td class="mono">${formatarTempo(f.tempo_s)}</td>` +
                `<td class="mono">${numero(f.palavras)}</td><td class="mono">${f.palavras_por_minuto} ppm</td>` +
                `<td class="tenue">${escapar(f.termos.join(", "))}</td></tr>`
            )
            .join("") +
          `</table></div>`
      );
    }

    const tecnicas = [
      ["Arquivo", meta.filename || "—"],
      ["Container", info.container || "—"],
      ["Codec de áudio", info.audio_codec || "—"],
      ["Canais originais", info.channels || "—"],
      ["Taxa de amostragem", info.sample_rate ? `${info.sample_rate} Hz` : "—"],
      ["Modelo", `${resultado.model} (${resultado.compute_type || ""} em ${resultado.device || ""})`],
      ["Perfil", resultado.profile],
      ["Vocabulário aplicado", (resultado.vocabulary || []).join(", ") || "—"],
    ];
    secoes.push(
      `<div class="bloco"><h3>Detalhes técnicos</h3><table class="tabela">` +
        tecnicas.map(([n, v]) => `<tr><td>${n}</td><td class="mono">${escapar(String(v))}</td></tr>`).join("") +
        `</table></div>`
    );

    $("conteudoDados").innerHTML = secoes.join("");
  }

  function realcar(texto, alvo) {
    const escapado = escapar(texto);
    const padrao = alvo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return escapado.replace(new RegExp(padrao, "gi"), (m) => `<mark>${m}</mark>`);
  }

  function montarMenuDownloads() {
    const menu = $("menuBaixar");
    const itens = estado.formatos.length
      ? estado.formatos
      : [{ id: "txt", label: "Texto", ext: ".txt" }];
    menu.innerHTML =
      itens
        .map((f) => `<button type="button" class="menu-item" data-fmt="${f.id}">${escapar(f.label)}<span>${f.ext}</span></button>`)
        .join("") +
      '<div class="menu-separador"></div>' +
      '<button type="button" class="menu-item" data-fmt="__zip">Todos os formatos<span>.zip</span></button>';

    menu.querySelectorAll(".menu-item").forEach((botao) =>
      botao.addEventListener("click", () => {
        const fmt = botao.dataset.fmt;
        const url = fmt === "__zip"
          ? `/api/download/${estado.jobId}`
          : `/api/download/${estado.jobId}/${fmt}`;
        window.location.href = url;
        menu.classList.add("oculto");
      })
    );
  }

  $("btnBaixar").addEventListener("click", (e) => {
    e.stopPropagation();
    $("menuBaixar").classList.toggle("oculto");
  });
  document.addEventListener("click", () => $("menuBaixar").classList.add("oculto"));

  $("btnCopiar").addEventListener("click", () => {
    navigator.clipboard
      .writeText($("textoCompleto").innerText.trim())
      .then(() => avisar("Texto copiado."))
      .catch(() => avisar("O navegador bloqueou a cópia."));
  });

  $("btnNova").addEventListener("click", () => {
    estado.resultado = null;
    estado.jobId = null;
    estado.arquivos = [];
    estado.lote = [];
    $("audio").pause();
    desenharArquivos();
    mostrarTela("telaEnvio");
  });

  // ---------- player sincronizado ----------
  const audio = $("audio");

  function prepararPlayer(resultado) {
    const painel = $("player");
    if (!resultado.audio_preview) {
      painel.classList.add("oculto");
      return;
    }
    painel.classList.remove("oculto");
    audio.src = `/api/media/${estado.jobId}`;
    audio.playbackRate = parseFloat($("selVelocidade").value);
    estado.trechoAtivo = -1;
    requestAnimationFrame(desenharOnda);
  }

  function irPara(segundos) {
    if (!audio.src) {
      avisar("O áudio desta transcrição não está mais disponível.");
      return;
    }
    audio.currentTime = Math.max(0, segundos);
    audio.play().catch(() => {});
  }

  // A onda é redesenhada a cada quadro de reprodução: são poucas centenas de
  // barras, mais barato que manter dois canvas sobrepostos.
  function desenharOnda() {
    const canvas = $("onda");
    if (!canvas || !estado.onda.length) return;
    const escala = window.devicePixelRatio || 1;
    const largura = canvas.clientWidth || 600;
    const altura = canvas.clientHeight || 54;
    if (canvas.width !== largura * escala) {
      canvas.width = largura * escala;
      canvas.height = altura * escala;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(escala, 0, 0, escala, 0, 0);
    ctx.clearRect(0, 0, largura, altura);

    const estilo = getComputedStyle(document.documentElement);
    const corTocada = estilo.getPropertyValue("--acento").trim() || "#4f8cff";
    const corRestante = estilo.getPropertyValue("--borda-forte").trim() || "#33405a";
    const total = audio.duration || (estado.resultado && estado.resultado.duration) || 1;
    const progresso = (audio.currentTime || 0) / total;

    const barras = Math.min(estado.onda.length, Math.floor(largura / 3));
    const passo = estado.onda.length / barras;
    for (let i = 0; i < barras; i++) {
      const valor = estado.onda[Math.floor(i * passo)] || 0;
      const h = Math.max(2, valor * (altura - 6));
      const x = i * 3;
      ctx.fillStyle = i / barras <= progresso ? corTocada : corRestante;
      ctx.fillRect(x, (altura - h) / 2, 2, h);
    }
  }

  $("onda").addEventListener("click", (e) => {
    const caixa = e.currentTarget.getBoundingClientRect();
    if (audio.duration) audio.currentTime = ((e.clientX - caixa.left) / caixa.width) * audio.duration;
  });
  window.addEventListener("resize", desenharOnda);

  audio.addEventListener("timeupdate", () => {
    const atual = audio.currentTime;
    const total = audio.duration || (estado.resultado && estado.resultado.duration) || 0;
    $("playerTempo").textContent = `${formatarTempo(atual)} / ${formatarTempo(total)}`;
    desenharOnda();

    const segmentos = (estado.resultado && estado.resultado.segments) || [];
    const indice = segmentos.findIndex((s) => atual >= s.start && atual <= s.end);
    if (indice !== estado.trechoAtivo) {
      estado.trechoAtivo = indice;
      document.querySelectorAll(".trecho.tocando").forEach((el) => el.classList.remove("tocando"));
      const alvo = $("listaTrechos").querySelector(`[data-indice="${indice}"]`);
      if (alvo) {
        alvo.classList.add("tocando");
        // Rolar um painel escondido faria a página inteira pular sem motivo.
        if ($("abaTrechos").classList.contains("ativo")) {
          alvo.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      }
    }
  });

  audio.addEventListener("play", () => ($("btnPlay").textContent = "⏸"));
  audio.addEventListener("pause", () => ($("btnPlay").textContent = "▶"));
  $("btnPlay").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  $("selVelocidade").addEventListener("change", (e) => (audio.playbackRate = parseFloat(e.target.value)));

  function mudarVelocidade(delta) {
    const opcoes = Array.from($("selVelocidade").options).map((o) => parseFloat(o.value));
    const atual = opcoes.indexOf(parseFloat($("selVelocidade").value));
    const novo = Math.min(opcoes.length - 1, Math.max(0, atual + delta));
    $("selVelocidade").value = String(opcoes[novo]);
    audio.playbackRate = opcoes[novo];
    avisar(`Velocidade ${String(opcoes[novo]).replace(".", ",")}×`);
  }

  function pularTrecho(direcao) {
    const segmentos = (estado.resultado && estado.resultado.segments) || [];
    if (!segmentos.length) return;
    const atual = audio.currentTime;
    if (direcao > 0) {
      const proximo = segmentos.find((s) => s.start > atual + 0.15);
      if (proximo) irPara(proximo.start);
    } else {
      const anteriores = segmentos.filter((s) => s.start < atual - 1.2);
      if (anteriores.length) irPara(anteriores[anteriores.length - 1].start);
      else irPara(0);
    }
  }

  // ---------- busca e abas ----------
  let temporizadorBusca;
  $("campoBusca").addEventListener("input", (e) => {
    clearTimeout(temporizadorBusca);
    const alvo = e.target.value;
    temporizadorBusca = setTimeout(() => {
      if (!estado.resultado) return;
      const achados = desenharTrechos(alvo);
      $("buscaResultado").textContent = alvo.trim() ? `${achados} trecho(s)` : "";
      if (alvo.trim()) trocarAba("abaTrechos");
    }, 180);
  });

  function trocarAba(id) {
    document.querySelectorAll(".aba").forEach((b) => b.classList.toggle("ativa", b.dataset.aba === id));
    document.querySelectorAll(".painel").forEach((p) => p.classList.toggle("ativo", p.id === id));
  }
  document.querySelectorAll(".aba").forEach((botao) =>
    botao.addEventListener("click", () => trocarAba(botao.dataset.aba))
  );

  // ---------- histórico ----------
  async function abrirHistorico() {
    const lista = $("listaHistorico");
    lista.innerHTML = '<p class="tenue">Carregando...</p>';
    mostrarTela("telaHistorico");
    try {
      const [{ jobs }, totais] = await Promise.all([
        (await fetch("/api/jobs?limit=200")).json(),
        (await fetch("/api/stats")).json().catch(() => ({})),
      ]);

      $("histResumo").textContent = totais && totais.tarefas
        ? `${totais.tarefas} transcrições · ${totais.horas_audio} h de áudio · ${numero(totais.palavras)} palavras · ${totais.fator_velocidade}× o tempo real`
        : "";

      if (!jobs.length) {
        lista.innerHTML = '<p class="tenue">Nenhuma transcrição registrada ainda.</p>';
        return;
      }
      lista.innerHTML = "";
      jobs.forEach((j) => {
        const selo =
          j.status === "concluido" ? "selo-ok" : j.status === "erro" ? "selo-erro" : "selo-neutro";
        const extras = [
          j.model,
          j.elapsed ? formatarTempo(j.elapsed) : null,
          j.speakers ? `${j.speakers} falantes` : null,
        ].filter(Boolean);
        const item = document.createElement("div");
        item.className = "hist-item";
        item.innerHTML =
          `<div class="hist-dados"><strong>${escapar(j.filename)}</strong>` +
          `<span class="tenue">${new Date(j.created_at * 1000).toLocaleString("pt-BR")} · ${escapar(extras.join(" · "))}</span></div>` +
          `<div class="hist-acoes">` +
          `<span class="selo ${selo}">${j.status}</span>` +
          (j.status === "concluido" ? `<button type="button" class="btn btn-suave btn-pequeno" data-abrir="${j.job_id}">Abrir</button>` : "") +
          `<button type="button" class="btn-icone" data-remover="${j.job_id}" title="Excluir">🗑</button></div>`;
        lista.appendChild(item);
      });

      lista.querySelectorAll("[data-abrir]").forEach((b) =>
        b.addEventListener("click", () => abrirTarefa(b.dataset.abrir))
      );
      lista.querySelectorAll("[data-remover]").forEach((b) =>
        b.addEventListener("click", async () => {
          if (!confirm("Excluir esta transcrição e todos os arquivos gerados?")) return;
          await fetch(`/api/jobs/${b.dataset.remover}`, { method: "DELETE" });
          abrirHistorico();
        })
      );
    } catch (erro) {
      lista.innerHTML = '<p class="tenue">Não foi possível carregar o histórico.</p>';
    }
  }

  $("btnHistorico").addEventListener("click", abrirHistorico);
  $("btnFecharHistorico").addEventListener("click", () =>
    mostrarTela(estado.resultado ? "telaResultado" : "telaEnvio")
  );

  // ---------- busca global ----------
  let temporizadorGlobal;
  $("btnBuscaGlobal").addEventListener("click", () => {
    mostrarTela("telaBusca");
    $("campoBuscaGlobal").focus();
  });
  $("btnFecharBusca").addEventListener("click", () =>
    mostrarTela(estado.resultado ? "telaResultado" : "telaEnvio")
  );

  $("campoBuscaGlobal").addEventListener("input", (e) => {
    clearTimeout(temporizadorGlobal);
    const termo = e.target.value.trim();
    const alvo = $("resultadosBusca");
    if (termo.length < 2) {
      alvo.innerHTML = "";
      return;
    }
    temporizadorGlobal = setTimeout(async () => {
      alvo.innerHTML = '<p class="tenue">Procurando...</p>';
      try {
        const dados = await (await fetch(`/api/search?q=${encodeURIComponent(termo)}`)).json();
        const itens = dados.resultados || [];
        if (!itens.length) {
          alvo.innerHTML = '<p class="tenue">Nada encontrado.</p>';
          return;
        }
        const porArquivo = new Map();
        itens.forEach((r) => {
          if (!porArquivo.has(r.job_id)) porArquivo.set(r.job_id, { arquivo: r.arquivo, itens: [] });
          porArquivo.get(r.job_id).itens.push(r);
        });

        alvo.innerHTML = Array.from(porArquivo, ([jobId, grupo]) =>
          `<div class="bloco"><h3>${escapar(grupo.arquivo)} <span class="tenue">${grupo.itens.length} ocorrência(s)</span></h3>` +
          `<ul class="lista-tempo">` +
          grupo.itens
            .map(
              (r) =>
                `<li><button type="button" class="ir" data-job="${jobId}" data-inicio="${r.start}">` +
                `${formatarTempo(r.start)}</button> ${r.trecho}</li>`
            )
            .join("") +
          `</ul></div>`
        ).join("");

        alvo.querySelectorAll("[data-job]").forEach((botao) =>
          botao.addEventListener("click", async () => {
            await abrirTarefa(botao.dataset.job);
            irPara(parseFloat(botao.dataset.inicio));
          })
        );
      } catch (erro) {
        alvo.innerHTML = '<p class="tenue">A busca falhou.</p>';
      }
    }, 260);
  });

  // ---------- atalhos ----------
  const modal = $("modalAtalhos");
  $("btnAtalhos").addEventListener("click", () => modal.classList.remove("oculto"));
  $("btnFecharAtalhos").addEventListener("click", () => modal.classList.add("oculto"));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("oculto");
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      modal.classList.add("oculto");
      $("menuBaixar").classList.add("oculto");
      return;
    }
    const digitando =
      ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
    if (digitando || e.ctrlKey || e.metaKey || e.altKey) return;

    const noResultado = !$("telaResultado").classList.contains("oculto");

    if (e.key === " " && audio.src && noResultado) {
      e.preventDefault();
      audio.paused ? audio.play() : audio.pause();
    }
    if (e.key === "t" || e.key === "T") $("btnTema").click();
    if (e.key === "h" || e.key === "H") abrirHistorico();
    if (e.key === "g" || e.key === "G") $("btnBuscaGlobal").click();
    if (e.key === "?") modal.classList.toggle("oculto");
    if (e.key === "/") {
      e.preventDefault();
      $("campoBusca").focus();
    }
    if (!audio.src) return;
    if (e.key === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 5);
    if (e.key === "ArrowRight") audio.currentTime += 5;
    if (e.key === "j" || e.key === "J") audio.currentTime = Math.max(0, audio.currentTime - 10);
    if (e.key === "l" || e.key === "L") audio.currentTime += 10;
    if (e.key === ",") pularTrecho(-1);
    if (e.key === ".") pularTrecho(1);
    if (e.key === "-") mudarVelocidade(-1);
    if (e.key === "+" || e.key === "=") mudarVelocidade(1);
  });

  // ---------- aviso ao sair no meio ----------
  window.addEventListener("beforeunload", (e) => {
    if (estado.fonteEventos || estado.envio || estado.edicoes.size) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  carregarSistema();
})();
