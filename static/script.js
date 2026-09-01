/* ============================================================
   Transcritor pt-BR — lógica da interface
   ============================================================ */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const estado = {
    arquivo: null,
    jobId: null,
    resultado: null,
    formatos: [],
    sistema: null,
    trechoAtivo: -1,
    envio: null,
    fonteEventos: null,
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
    div.textContent = t;
    return div.innerHTML;
  };

  let temporizadorAviso;
  function avisar(mensagem) {
    const el = $("aviso");
    el.textContent = mensagem;
    el.classList.remove("oculto");
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(() => el.classList.add("oculto"), 3200);
  }

  function mostrarTela(id) {
    ["telaEnvio", "telaProgresso", "telaResultado", "telaHistorico"].forEach((t) =>
      $(t).classList.toggle("oculto", t !== id)
    );
  }

  // ---------- tema ----------
  const temaSalvo = localStorage.getItem("transcritor-tema");
  if (temaSalvo) document.documentElement.dataset.tema = temaSalvo;
  $("btnTema").addEventListener("click", () => {
    const novo = document.documentElement.dataset.tema === "claro" ? "escuro" : "claro";
    document.documentElement.dataset.tema = novo;
    localStorage.setItem("transcritor-tema", novo);
    $("btnTema").textContent = novo === "claro" ? "☀️" : "🌙";
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

      $("entradaArquivo").accept = dados.extensoes.join(",");
    } catch (e) {
      $("topoHardware").textContent = "Não foi possível ler as informações do sistema.";
    }
  }

  // ---------- seleção de arquivo ----------
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
  areaSolta.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) escolherArquivo(e.dataTransfer.files[0]);
  });
  areaSolta.addEventListener("click", () => $("entradaArquivo").click());
  areaSolta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      $("entradaArquivo").click();
    }
  });
  $("entradaArquivo").addEventListener("change", (e) => {
    if (e.target.files.length) escolherArquivo(e.target.files[0]);
  });

  $("btnTrocarArquivo").addEventListener("click", () => {
    estado.arquivo = null;
    $("entradaArquivo").value = "";
    $("arquivoEscolhido").classList.add("oculto");
    areaSolta.classList.remove("oculto");
    $("btnIniciar").disabled = true;
  });

  function escolherArquivo(arquivo) {
    const ext = "." + (arquivo.name.split(".").pop() || "").toLowerCase();
    const aceitas = estado.sistema && estado.sistema.extensoes;
    if (aceitas && !aceitas.includes(ext)) {
      avisar(`O formato ${ext} não é suportado.`);
      return;
    }
    estado.arquivo = arquivo;
    $("arquivoNome").textContent = arquivo.name;
    $("arquivoInfo").textContent = formatarBytes(arquivo.size);
    $("arquivoExt").textContent = ext.slice(1).toUpperCase();
    areaSolta.classList.add("oculto");
    $("arquivoEscolhido").classList.remove("oculto");
    $("btnIniciar").disabled = false;
  }

  // ---------- envio ----------
  $("formulario").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!estado.arquivo) return;

    const dados = new FormData();
    dados.append("file", estado.arquivo);
    dados.append("model_name", $("selModelo").value);
    dados.append("profile", $("selPerfil").value);
    dados.append("language", $("selIdioma").value);
    dados.append("vocabulary", $("campoVocabulario").value);

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
      definirProgresso(pct * 0.15, "Enviando o arquivo",
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
      estado.jobId = resposta.job_id;
      definirProgresso(0, "Na fila", "Aguardando o processador...");
      acompanhar(resposta.job_id);
    };

    req.onerror = () => {
      estado.envio = null;
      falhar("A conexão com o servidor foi perdida durante o envio.");
    };

    req.send(dados);
  });

  function falhar(mensagem) {
    avisar(mensagem);
    mostrarTela("telaEnvio");
  }

  function reiniciarProgresso() {
    $("feedCorpo").innerHTML = '<p class="tenue">Os trechos aparecem aqui conforme são reconhecidos.</p>';
    $("feedContagem").textContent = "0 trechos";
    $("progEta").textContent = "";
    definirProgresso(0, "Preparando...", "Enviando o arquivo para o servidor.");
  }

  function definirProgresso(pct, etapa, detalhe) {
    $("progBarra").style.width = `${pct}%`;
    $("progAnel").style.setProperty("--pct", pct);
    $("progNumero").textContent = `${Math.round(pct)}%`;
    if (etapa) $("progEtapa").textContent = etapa;
    if (detalhe !== undefined) $("progDetalhe").textContent = detalhe;
  }

  // ---------- acompanhamento em tempo real ----------
  function acompanhar(jobId) {
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
        if (dados.status === "concluido") {
          estado.formatos = dados.formatos || dados.formats || [];
          mostrarResultado(dados.result, dados);
        } else if (dados.status === "cancelado") {
          avisar("Transcrição cancelada.");
          mostrarTela("telaEnvio");
        } else {
          falhar(dados.error || "A transcrição falhou.");
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
          if (j.status === "concluido") mostrarResultado(j.result, j);
          else if (j.status !== "processando" && j.status !== "na_fila")
            falhar(j.error || "Conexão interrompida.");
          else setTimeout(() => acompanhar(jobId), 2000);
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
  function mostrarResultado(resultado, meta = {}) {
    if (!resultado || !Array.isArray(resultado.segments)) {
      return falhar("O resultado desta transcrição não está mais disponível.");
    }
    resultado.cues = resultado.cues || [];
    estado.resultado = resultado;
    estado.formatos = meta.formats || estado.formatos;
    mostrarTela("telaResultado");

    $("resArquivo").textContent =
      `${resultado.model} · perfil ${resultado.profile} · idioma ${resultado.language}` +
      (meta.speed_factor ? ` · ${meta.speed_factor}× mais rápido que o tempo real` : "");

    const confianca = Math.round((resultado.avg_confidence || 0) * 100);
    $("resMetricas").innerHTML = [
      ["Duração", formatarDuracao(resultado.duration)],
      ["Palavras", (resultado.word_count || 0).toLocaleString("pt-BR")],
      ["Trechos", resultado.segments.length],
      ["Confiança", `${confianca}%`],
      ["Processamento", meta.elapsed ? formatarTempo(meta.elapsed) : "—"],
    ]
      .map(
        ([nome, valor]) =>
          `<div class="metrica"><div class="metrica-valor">${valor}</div><div class="metrica-nome">${nome}</div></div>`
      )
      .join("");

    $("textoCompleto").innerHTML = (resultado.paragraphs || [resultado.plain_text])
      .map((p) => `<p>${escapar(p)}</p>`)
      .join("");

    desenharTrechos();
    desenharLegendas();
    montarMenuDownloads();
    prepararPlayer(resultado);
    $("campoBusca").value = "";
    $("buscaResultado").textContent = "";
  }

  function desenharTrechos(filtro = "") {
    const lista = $("listaTrechos");
    const alvo = filtro.trim().toLowerCase();
    lista.innerHTML = "";
    let achados = 0;

    estado.resultado.segments.forEach((s, indice) => {
      if (alvo && !s.text.toLowerCase().includes(alvo)) return;
      achados++;
      const item = document.createElement("div");
      item.className = "trecho" + (s.confidence < 0.55 ? " trecho-baixa" : "");
      item.dataset.indice = indice;
      item.title = `Confiança ${Math.round(s.confidence * 100)}% — clique para ouvir`;
      const texto = alvo ? realcar(s.text, alvo) : escapar(s.text);
      item.innerHTML =
        `<div class="trecho-tempo">${s.start_str}</div>` +
        `<div class="trecho-texto">${texto}` +
        (s.confidence < 0.55 ? '<div class="trecho-aviso">⚠ trecho de baixa confiança — vale conferir</div>' : "") +
        "</div>";
      item.addEventListener("click", () => irPara(s.start));
      lista.appendChild(item);
    });

    if (!achados) lista.innerHTML = '<p class="tenue">Nenhum trecho encontrado.</p>';
    return achados;
  }

  function desenharLegendas() {
    const lista = $("listaLegendas");
    lista.innerHTML = "";
    (estado.resultado.cues || []).forEach((c) => {
      const item = document.createElement("div");
      item.className = "trecho";
      item.innerHTML =
        `<div class="trecho-tempo">${c.id}<br>${formatarTempo(c.start)}</div>` +
        `<div class="trecho-linhas">${c.lines.map((l) => `<div class="trecho-texto">${escapar(l)}</div>`).join("")}</div>`;
      item.addEventListener("click", () => irPara(c.start));
      lista.appendChild(item);
    });
    if (!lista.children.length) lista.innerHTML = '<p class="tenue">Sem blocos de legenda.</p>';
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
        .map((f) => `<button type="button" class="menu-item" data-fmt="${f.id}">${f.label}<span>${f.ext}</span></button>`)
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
    $("audio").pause();
    $("btnTrocarArquivo").click();
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
  }

  function irPara(segundos) {
    if (!audio.src) return;
    audio.currentTime = segundos;
    audio.play().catch(() => {});
  }

  audio.addEventListener("timeupdate", () => {
    const atual = audio.currentTime;
    const total = audio.duration || (estado.resultado && estado.resultado.duration) || 0;
    $("playerProgresso").style.width = `${total ? (atual / total) * 100 : 0}%`;
    $("playerTempo").textContent = `${formatarTempo(atual)} / ${formatarTempo(total)}`;

    const segmentos = (estado.resultado && estado.resultado.segments) || [];
    const indice = segmentos.findIndex((s) => atual >= s.start && atual <= s.end);
    if (indice !== estado.trechoAtivo) {
      estado.trechoAtivo = indice;
      document.querySelectorAll(".trecho.tocando").forEach((el) => el.classList.remove("tocando"));
      const alvo = $("listaTrechos").querySelector(`[data-indice="${indice}"]`);
      if (alvo) {
        alvo.classList.add("tocando");
        alvo.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  });

  audio.addEventListener("play", () => ($("btnPlay").textContent = "⏸"));
  audio.addEventListener("pause", () => ($("btnPlay").textContent = "▶"));
  $("btnPlay").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  $("selVelocidade").addEventListener("change", (e) => (audio.playbackRate = parseFloat(e.target.value)));
  $("playerTrilha").addEventListener("click", (e) => {
    const caixa = e.currentTarget.getBoundingClientRect();
    if (audio.duration) audio.currentTime = ((e.clientX - caixa.left) / caixa.width) * audio.duration;
  });

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
  $("btnHistorico").addEventListener("click", async () => {
    const lista = $("listaHistorico");
    lista.innerHTML = '<p class="tenue">Carregando...</p>';
    mostrarTela("telaHistorico");
    try {
      const { jobs } = await (await fetch("/api/jobs")).json();
      if (!jobs.length) {
        lista.innerHTML = '<p class="tenue">Nenhuma transcrição registrada ainda.</p>';
        return;
      }
      lista.innerHTML = "";
      jobs.forEach((j) => {
        const selo =
          j.status === "concluido" ? "selo-ok" : j.status === "erro" ? "selo-erro" : "selo-neutro";
        const item = document.createElement("div");
        item.className = "hist-item";
        item.innerHTML =
          `<div class="hist-dados"><strong>${escapar(j.filename)}</strong>` +
          `<span class="tenue">${new Date(j.created_at * 1000).toLocaleString("pt-BR")} · ${j.model}` +
          `${j.elapsed ? ` · ${formatarTempo(j.elapsed)}` : ""}</span></div>` +
          `<div style="display:flex;gap:8px;align-items:center">` +
          `<span class="selo ${selo}">${j.status}</span>` +
          (j.status === "concluido" ? `<button type="button" class="btn btn-suave btn-pequeno" data-abrir="${j.job_id}">Abrir</button>` : "") +
          `<button type="button" class="btn-icone" data-remover="${j.job_id}" title="Excluir">🗑</button></div>`;
        lista.appendChild(item);
      });

      lista.querySelectorAll("[data-abrir]").forEach((b) =>
        b.addEventListener("click", async () => {
          const dados = await (await fetch(`/api/progress/${b.dataset.abrir}`)).json();
          estado.jobId = dados.job_id;
          estado.formatos = dados.formats || [];
          mostrarResultado(dados.result, dados);
        })
      );
      lista.querySelectorAll("[data-remover]").forEach((b) =>
        b.addEventListener("click", async () => {
          await fetch(`/api/jobs/${b.dataset.remover}`, { method: "DELETE" });
          $("btnHistorico").click();
        })
      );
    } catch (erro) {
      lista.innerHTML = '<p class="tenue">Não foi possível carregar o histórico.</p>';
    }
  });

  $("btnFecharHistorico").addEventListener("click", () =>
    mostrarTela(estado.resultado ? "telaResultado" : "telaEnvio")
  );

  // ---------- atalhos de teclado ----------
  document.addEventListener("keydown", (e) => {
    const digitando = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
    if (digitando) return;

    if (e.key === " " && audio.src && !$("telaResultado").classList.contains("oculto")) {
      e.preventDefault();
      audio.paused ? audio.play() : audio.pause();
    }
    if (e.key === "t" || e.key === "T") $("btnTema").click();
    if (e.key === "/") {
      e.preventDefault();
      $("campoBusca").focus();
    }
    if (e.key === "ArrowLeft" && audio.src) audio.currentTime = Math.max(0, audio.currentTime - 5);
    if (e.key === "ArrowRight" && audio.src) audio.currentTime += 5;
  });

  // ---------- aviso ao sair no meio ----------
  window.addEventListener("beforeunload", (e) => {
    if (estado.fonteEventos || estado.envio) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  carregarSistema();
})();
