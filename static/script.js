/* ============================================================
   Transcritor pt-BR — lógica da interface

   Três telas (envio, progresso, resultado) e três sobreposições
   (histórico, busca global e atalhos) que flutuam sobre a tela
   atual. O estado do lote em andamento vive em `estado.lote`; o
   servidor processa um arquivo por vez, então a interface
   acompanha o atual por SSE e o resto por sondagem.
   ============================================================ */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const CORES_FALANTE = 8;
  const ESTADOS = {
    concluido: "concluído", erro: "erro", cancelado: "cancelado",
    processando: "em andamento", na_fila: "na fila",
  };
  const VELOCIDADES = [0.75, 1, 1.25, 1.5, 2];
  const LIMITE_BAIXA = 0.55;

  const estado = {
    arquivos: [],
    lote: [],
    indiceAtual: 0,
    jobId: null,
    resultado: null,
    formatos: [],
    sistema: null,
    perfil: null,
    velocidade: 1,
    trechoAtivo: -1,
    envio: null,
    fonteEventos: null,
    sondagem: null,
    edicoes: new Map(),
    falantesOcultos: new Set(),
    onda: [],
    opcoes: { falantes: true, analise: true },
    etapaAtual: 0,
    etapaDesenhada: -1,
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

  const velocidadeTexto = (v) => `${String(v).replace(".", ",")}×`;

  let temporizadorAviso;
  function avisar(mensagem) {
    const el = $("aviso");
    el.textContent = mensagem;
    el.classList.remove("oculto");
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(() => el.classList.add("oculto"), 3600);
  }

  // ---------- telas e sobreposições ----------
  const TELAS = ["telaEnvio", "telaProgresso", "telaResultado"];
  const SOBREPOSICOES = ["telaHistorico", "telaBusca", "modalAtalhos"];

  function mostrarTela(id) {
    TELAS.forEach((t) => $(t).classList.toggle("oculto", t !== id));
    if (id === "telaResultado") posicionarIndicadorAbas();
  }

  function abrirSobreposicao(id) {
    SOBREPOSICOES.forEach((s) => $(s).classList.toggle("oculto", s !== id));
    document.body.classList.add("travado");
  }

  function fecharSobreposicoes() {
    SOBREPOSICOES.forEach((s) => $(s).classList.add("oculto"));
    document.body.classList.remove("travado");
  }

  const alguemAberto = () => SOBREPOSICOES.some((s) => !$(s).classList.contains("oculto"));

  document.querySelectorAll("[data-fechar]").forEach((el) =>
    el.addEventListener("click", fecharSobreposicoes)
  );

  $("btnInicio").addEventListener("click", () => {
    fecharSobreposicoes();
    mostrarTela(estado.fonteEventos || estado.envio ? "telaProgresso" : "telaEnvio");
  });

  // ---------- barras animadas da área de arrastar ----------
  (function montarOndasArea() {
    const alvo = $("areaOndas");
    const fragmento = document.createDocumentFragment();
    for (let i = 0; i < 14; i++) {
      const barra = document.createElement("span");
      barra.style.setProperty("--d", `${(1 + Math.abs(Math.sin(i * 1.7)) * 0.9).toFixed(2)}s`);
      barra.style.setProperty("--atraso", `${(i * 0.08).toFixed(2)}s`);
      barra.style.setProperty("--o", (0.3 + Math.abs(Math.cos(i * 0.6)) * 0.55).toFixed(2));
      fragmento.appendChild(barra);
    }
    alvo.appendChild(fragmento);
  })();

  // ---------- informações do sistema ----------
  async function carregarSistema() {
    try {
      const dados = await (await fetch("/api/system")).json();
      estado.sistema = dados;

      const hw = dados.hardware;
      $("topoHardware").textContent = hw.gpu
        ? `pt-BR · placa de vídeo · ${hw.compute_type} · offline`
        : `pt-BR · ${hw.cpu_threads} núcleos · ${hw.compute_type} · offline`;

      $("avisoFfmpeg").classList.toggle("oculto", dados.ffmpeg);

      const selModelo = $("selModelo");
      selModelo.innerHTML = dados.modelos
        .map((m) => {
          const marca = m.baixado ? "" : " — download na 1ª vez";
          const rec = m.padrao ? " (recomendado)" : "";
          return `<option value="${m.id}"${m.padrao ? " selected" : ""}>${escapar(m.nome)}${rec}${marca}</option>`;
        })
        .join("");

      montarPerfis(dados.perfis);

      const atualizarDicaModelo = () => {
        const m = dados.modelos.find((x) => x.id === selModelo.value);
        $("dicaModelo").textContent = m ? `${m.resumo} · ${m.params} · ~${m.ram_gb} GB de RAM` : "";
      };
      selModelo.addEventListener("change", atualizarDicaModelo);
      atualizarDicaModelo();

      const recursos = dados.recursos || {};
      $("optFalantes").checked = recursos.diarizacao !== false;
      $("optAnalise").checked = recursos.analise !== false;
      $("btnBuscaGlobal").classList.toggle("oculto", !recursos.busca_global);

      $("entradaArquivo").accept = dados.extensoes.join(",");
    } catch (e) {
      $("topoHardware").textContent = "sistema indisponível";
    }
  }

  // O perfil vira um controle segmentado: são sempre poucas opções e
  // a escolha muda o tempo de processamento, então ela fica à vista.
  function montarPerfis(perfis) {
    const caixa = $("segPerfil");
    const indicador = $("segPerfilIndicador");
    caixa.querySelectorAll(".segmento").forEach((b) => b.remove());
    caixa.style.gridTemplateColumns = `repeat(${perfis.length}, 1fr)`;
    indicador.style.width = `calc((100% - 6px) / ${perfis.length})`;

    perfis.forEach((p) => {
      const botao = document.createElement("button");
      botao.type = "button";
      botao.className = "segmento";
      botao.dataset.perfil = p.id;
      botao.textContent = p.nome;
      botao.setAttribute("role", "radio");
      botao.addEventListener("click", () => escolherPerfil(p.id));
      caixa.appendChild(botao);
    });

    const padrao = (perfis.find((p) => p.padrao) || perfis[0] || {}).id;
    if (padrao) escolherPerfil(padrao);
  }

  function escolherPerfil(id) {
    const perfis = (estado.sistema && estado.sistema.perfis) || [];
    const indice = Math.max(0, perfis.findIndex((p) => p.id === id));
    estado.perfil = id;
    $("segPerfilIndicador").style.left =
      `calc(3px + ${indice} * (100% - 6px) / ${perfis.length || 1})`;
    $("segPerfil").querySelectorAll(".segmento").forEach((b) => {
      const ativo = b.dataset.perfil === id;
      b.classList.toggle("ativo", ativo);
      b.setAttribute("aria-checked", ativo ? "true" : "false");
    });
    const perfil = perfis[indice];
    $("dicaPerfil").textContent = perfil ? perfil.resumo : "";
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
      item.style.animationDelay = `${(indice * 0.06).toFixed(2)}s`;
      item.innerHTML =
        `<span class="arquivo-selo">${escapar(ext.slice(0, 4))}</span>` +
        `<span class="arquivo-nome">${escapar(arquivo.name)}</span>` +
        `<span class="arquivo-meta">${formatarBytes(arquivo.size)}</span>` +
        `<button type="button" class="arquivo-remover">Remover</button>`;
      item.querySelector("button").addEventListener("click", () => {
        estado.arquivos.splice(indice, 1);
        desenharArquivos();
      });
      lista.appendChild(item);
    });

    if (estado.arquivos.length > 1) {
      const total = estado.arquivos.reduce((soma, a) => soma + a.size, 0);
      const rodape = document.createElement("span");
      rodape.className = "arquivos-resumo";
      rodape.textContent = `${estado.arquivos.length} arquivos · ${formatarBytes(total)} no total. Serão processados em sequência.`;
      lista.appendChild(rodape);
    }

    const quantos = estado.arquivos.length;
    $("btnIniciar").disabled = !quantos;
    $("btnIniciarRotulo").textContent =
      quantos > 1 ? `Transcrever ${quantos} arquivos` : "Iniciar transcrição";
    $("btnIniciarDica").textContent = quantos ? "tudo roda nesta máquina" : "escolha um arquivo";
  }

  // ---------- envio ----------
  $("formulario").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!estado.arquivos.length) return;

    estado.opcoes = {
      falantes: $("optFalantes").checked,
      analise: $("optAnalise").checked,
    };

    const dados = new FormData();
    estado.arquivos.forEach((arquivo) => dados.append("files", arquivo));
    dados.append("model_name", $("selModelo").value);
    dados.append("profile", estado.perfil || "");
    dados.append("language", $("selIdioma").value);
    dados.append("vocabulary", $("campoVocabulario").value);
    dados.append("diarizar", estado.opcoes.falantes);
    dados.append("analisar", estado.opcoes.analise);
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

  // ---------- progresso ----------
  const FEED_VAZIO = '<span class="feed-vazio">Ouvindo…</span>';

  function reiniciarProgresso() {
    $("feedCorpo").innerHTML = FEED_VAZIO;
    $("feedContagem").textContent = "0 trechos";
    $("progEta").textContent = "";
    $("progArquivo").textContent = "Preparando o envio";
    $("progEyebrow").textContent = "Transcrevendo";
    $("filaLote").classList.add("oculto");
    estado.etapaAtual = 0;
    estado.etapaDesenhada = -1;
    definirProgresso(0, "Preparando", "Enviando os arquivos para o servidor.");
  }

  // As etapas visíveis dependem do que foi pedido: sem diarização não
  // faz sentido mostrar "Separando falantes" parado o tempo todo.
  function etapasDoLote() {
    const etapas = [
      {
        rotulo: "Preparando o áudio",
        chaves: ["Preparando", "Enviando", "Na fila", "Iniciando", "Analisando o arquivo",
                 "Extraindo o áudio", "Carregando o modelo", "Baixando o modelo"],
      },
      { rotulo: "Reconhecendo a fala", chaves: ["Transcrevendo", "Revisando o texto"] },
    ];
    if (estado.opcoes.falantes) {
      etapas.push({ rotulo: "Separando falantes", chaves: ["Separando os falantes"] });
    }
    etapas.push({
      rotulo: estado.opcoes.analise ? "Resumo e capítulos" : "Fechando os arquivos",
      chaves: ["Montando o documento", "Lendo o conteúdo", "Finalizando", "Concluído"],
    });
    return etapas;
  }

  function desenharEtapas(etapa, detalhe) {
    const etapas = etapasDoLote();
    if (etapa) {
      const achou = etapas.findIndex((e) => e.chaves.some((c) => etapa.startsWith(c)));
      if (achou !== -1) estado.etapaAtual = achou;
    }
    const atual = Math.min(estado.etapaAtual, etapas.length - 1);
    const lista = $("progEtapas");

    if (lista.children.length === etapas.length && estado.etapaDesenhada === atual) {
      const nota = lista.children[atual].querySelector("em");
      if (nota) nota.textContent = detalhe || "";
      return;
    }
    estado.etapaDesenhada = atual;

    lista.innerHTML = etapas
      .map((e, i) => {
        const classe = i < atual ? "feita" : i === atual ? "atual" : "";
        const nota = i < atual ? "feito" : i === atual ? detalhe || "" : "";
        return (
          `<li class="prog-etapa ${classe}"><span></span>` +
          `<span>${escapar(e.rotulo)}</span><em>${escapar(nota)}</em></li>`
        );
      })
      .join("");
  }

  function definirProgresso(pct, etapa, detalhe) {
    $("progBarra").style.width = `${pct}%`;
    $("progNumero").textContent = String(Math.floor(pct));
    desenharEtapas(etapa, detalhe);
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
          t.status === "concluido" ? "ok"
            : t.status === "erro" ? "erro"
            : i === estado.indiceAtual ? "ativo" : "";
        const rotulo =
          t.status === "concluido" ? "pronto"
            : t.status === "erro" ? "erro"
            : i === estado.indiceAtual ? `${Math.round(t.percent || 0)}%` : "na fila";
        const abrir = t.status === "concluido"
          ? `<button type="button" class="btn btn-contorno btn-pequeno" data-abrir-lote="${t.job_id}">Abrir</button>`
          : "";
        return (
          `<div class="fila-item"><span class="fila-nome">${escapar(t.filename)}</span>` +
          `<span class="fila-direita"><span class="fila-estado ${classe}">${rotulo}</span>${abrir}</span></div>`
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
    estado.etapaAtual = 0;
    estado.etapaDesenhada = -1;
    $("progEyebrow").textContent =
      estado.lote.length > 1
        ? `Transcrevendo · ${estado.indiceAtual + 1} de ${estado.lote.length}`
        : "Transcrevendo";
    $("progArquivo").textContent = tarefa.filename;
    $("feedCorpo").innerHTML = FEED_VAZIO;
    $("feedContagem").textContent = "0 trechos";
    definirProgresso(15, "Na fila", "Aguardando o processador…");
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
          ? `${dados.queue_position - 1} na frente`
          : dados.detail;
        definirProgresso(pct, dados.stage, detalhe);
        $("progEta").textContent =
          dados.eta_seconds > 0
            ? `Tempo restante estimado · ${formatarTempo(dados.eta_seconds)}`
            : "";
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
          linha.innerHTML =
            `<span class="mono">${escapar(s.start_str)}</span><p>${escapar(s.text)}</p>`;
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
      fecharSobreposicoes();
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

    const fator = meta.speed_factor || resultado.speed_factor;
    $("resEyebrow").textContent = fator
      ? `Concluída · ${fator}× mais rápido que o tempo real`
      : "Concluída";
    $("resTitulo").textContent = meta.filename || "Transcrição concluída";
    $("resMeta").textContent =
      `${resultado.model} · perfil ${resultado.profile} · ${resultado.language} ` +
      `${Math.round((resultado.language_probability || 1) * 100)}%` +
      (resultado.discarded_segments
        ? ` · ${resultado.discarded_segments} trecho(s) descartado(s) por alucinação`
        : "");

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
      ["Confiança média", `${confianca}%`],
      ["Processamento", meta.elapsed ? formatarTempo(meta.elapsed) : formatarTempo(resultado.processing_seconds)],
    ];
    if (falantes) linhas.splice(2, 0, ["Falantes", falantes]);
    $("resMetricas").innerHTML = linhas
      .map(
        ([nome, valor], i) =>
          `<div class="metrica" style="animation-delay:${(0.05 + i * 0.05).toFixed(2)}s">` +
          `<span class="metrica-valor">${escapar(String(valor))}</span>` +
          `<span class="metrica-nome">${nome}</span></div>`
      )
      .join("");
  }

  function desenharTexto(resultado) {
    const alvo = $("textoCompleto");
    const blocos = resultado.dialogue && resultado.dialogue.length ? resultado.dialogue : null;

    if (blocos) {
      alvo.innerHTML = blocos
        .map(
          (b, i) =>
            `<div class="texto-bloco" data-inicio="${b.start}" data-bloco="${i}" ` +
            `style="animation-delay:${(Math.min(i, 8) * 0.04).toFixed(2)}s">` +
            `<button type="button" class="texto-quem" data-ir="${b.start}">` +
            `<strong class="quem s${(b.speaker_id || 0) % CORES_FALANTE}">${escapar(b.speaker || "")}</strong>` +
            `<span class="mono">${escapar(b.start_str || "")}</span></button>` +
            `<p>${escapar(b.texto)}</p></div>`
        )
        .join("");
      alvo.querySelectorAll("[data-ir]").forEach((botao) =>
        botao.addEventListener("click", () => irPara(parseFloat(botao.dataset.ir)))
      );
    } else {
      const paragrafos = resultado.paragraphs || [resultado.plain_text];
      alvo.innerHTML = paragrafos
        .map(
          (p, i) =>
            `<div class="texto-bloco sozinho" style="animation-delay:${(Math.min(i, 8) * 0.04).toFixed(2)}s">` +
            `<p>${escapar(p)}</p></div>`
        )
        .join("");
    }
  }

  function desenharFiltros(resultado) {
    const falantes = (resultado.diarization || {}).falantes || [];
    $("filtros").classList.toggle("oculto", !falantes.length);
    $("chipsFalantes").innerHTML = falantes
      .map(
        (f) =>
          `<button type="button" class="chip s${f.id % CORES_FALANTE} ativo" data-falante="${escapar(f.nome)}">` +
          `<span class="chip-ponto"></span>${escapar(f.nome)}` +
          `<span class="mono">${f.percentual}%</span></button>`
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
    lista.innerHTML = "";
    let achados = 0;

    estado.resultado.segments.forEach((s, indice) => {
      if (alvo && !s.text.toLowerCase().includes(alvo)) return;
      if (soBaixa && s.confidence >= LIMITE_BAIXA) return;
      if (s.speaker && estado.falantesOcultos.has(s.speaker)) return;
      achados++;

      const baixa = s.confidence < LIMITE_BAIXA;
      const item = document.createElement("div");
      item.className = "trecho" + (baixa ? " baixa" : "");
      item.dataset.indice = indice;
      const quem = s.speaker
        ? `<span class="quem s${(s.speaker_id || 0) % CORES_FALANTE}">${escapar(s.speaker)}</span>`
        : "";
      const texto = alvo ? realcar(s.text, alvo) : escapar(s.text);
      item.innerHTML =
        `<span class="trecho-marca"></span>` +
        `<button type="button" class="trecho-tempo" title="Ouvir a partir daqui">` +
        `<span class="mono">${s.start_str}</span>${quem}</button>` +
        `<div class="trecho-corpo">` +
        `<div class="trecho-texto" contenteditable="true" spellcheck="true" data-id="${s.id}">${texto}</div>` +
        (baixa
          ? `<span class="trecho-aviso">Baixa confiança (${Math.round(s.confidence * 100)}%) — vale conferir no áudio</span>`
          : "") +
        `</div>`;

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

    if (!achados) lista.innerHTML = '<p class="vazio">Nenhum trecho encontrado com estes filtros.</p>';
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
    botao.textContent = "Salvando…";
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
        trocarAba("abaTrechos");
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
    const rapidas = new Set(qa.acima_do_cps || []);
    $("qaLegendas").innerHTML = qa.blocos
      ? `<span>${qa.blocos} blocos</span>` +
        `<span>${qa.cps_medio} caracteres por segundo em média</span>` +
        (rapidas.size
          ? `<span class="atencao">${rapidas.size} bloco(s) rápidos demais para ler</span>`
          : `<span class="ok">Ritmo de leitura dentro do recomendado</span>`) +
        `<span>Máx. 2 linhas × 42 colunas</span>`
      : "";

    const lista = $("listaLegendas");
    lista.innerHTML = "";
    (resultado.cues || []).forEach((c) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "legenda" + (rapidas.has(c.id) ? " rapida" : "");
      item.innerHTML =
        `<span class="legenda-topo"><span>${c.id}</span>` +
        `<span>${formatarTempo(c.start)} → ${formatarTempo(c.end)}</span>` +
        `<span>${c.cps || 0} cps</span></span>` +
        `<span class="legenda-tela">` +
        c.lines.map((l) => `<span>${escapar(l)}</span>`).join("") +
        `</span>`;
      item.addEventListener("click", () => irPara(c.start));
      lista.appendChild(item);
    });
    if (!lista.children.length) lista.innerHTML = '<p class="vazio">Sem blocos de legenda.</p>';
  }

  function desenharResumo(resultado) {
    const analise = resultado.analysis || {};
    const alvo = $("conteudoResumo");
    if (!analise.resumo && !analise.capitulos) {
      alvo.innerHTML = '<p class="vazio">A análise de conteúdo estava desligada nesta transcrição.</p>';
      return;
    }

    const esquerda = [];
    const direita = [];

    if (analise.resumo && analise.resumo.length) {
      esquerda.push(
        `<section class="secao secao-solta"><span class="sobrancelha">Em poucas linhas</span>` +
          analise.resumo
            .map(
              (i, n) =>
                `<div class="item-tempo resumo-linha" style="animation-delay:${(0.05 + n * 0.05).toFixed(2)}s">` +
                `<button type="button" class="ir" data-ir="${i.start}">${i.start_str}</button>` +
                `<p>${escapar(i.texto)}</p></div>`
            )
            .join("") +
          `</section>`
      );
    }

    if (analise.capitulos && analise.capitulos.length) {
      esquerda.push(
        `<section class="secao"><span class="sobrancelha secao-titulo">Capítulos</span>` +
          analise.capitulos
            .map(
              (c) =>
                `<button type="button" class="capitulo" data-ir="${c.start}">` +
                `<span class="mono">${c.start_str}</span>` +
                `<span class="capitulo-texto"><strong>${escapar(c.titulo)}</strong>` +
                `<span>${escapar(c.abertura)}…</span></span></button>`
            )
            .join("") +
          `</section>`
      );
    }

    if (analise.palavras_chave && analise.palavras_chave.length) {
      direita.push(
        `<section class="secao secao-temas"><span class="sobrancelha">Temas dominantes</span>` +
          `<div class="temas">` +
          analise.palavras_chave
            .map(
              (k) =>
                `<span class="tema">${escapar(k.termo)}` +
                `<span class="mono">${k.ocorrencias}×</span></span>`
            )
            .join("") +
          `</div></section>`
      );
    }

    if (analise.pendencias && analise.pendencias.length) {
      direita.push(
        `<section class="secao"><span class="sobrancelha">Possíveis pendências</span>` +
          `<span class="secao-nota">Frases que soam como compromisso assumido. Confira no áudio antes de cobrar alguém.</span>` +
          analise.pendencias
            .map(
              (p) =>
                `<div class="item-tempo linha-tempo">` +
                `<button type="button" class="ir" data-ir="${p.start}">${p.start_str}</button>` +
                `<p>${p.falante ? `<strong class="quem${classeFalante(p.falante)}">${escapar(p.falante)}</strong> ` : ""}${escapar(p.texto)}</p></div>`
            )
            .join("") +
          `</section>`
      );
    }

    if (analise.perguntas && analise.perguntas.length) {
      direita.push(
        `<section class="secao"><span class="sobrancelha secao-titulo">Perguntas feitas</span>` +
          analise.perguntas
            .map(
              (p) =>
                `<div class="item-tempo linha-tempo">` +
                `<button type="button" class="ir" data-ir="${p.start}">${p.start_str}</button>` +
                `<p>${escapar(p.texto)}</p></div>`
            )
            .join("") +
          `</section>`
      );
    }

    alvo.innerHTML =
      `<div class="duas-colunas"><div class="coluna">${esquerda.join("")}</div>` +
      `<div class="coluna">${direita.join("")}</div></div>`;
    alvo.querySelectorAll("[data-ir]").forEach((botao) =>
      botao.addEventListener("click", () => irPara(parseFloat(botao.dataset.ir)))
    );
  }

  function desenharDados(resultado, meta) {
    const analise = resultado.analysis || {};
    const stats = analise.estatisticas || {};
    const info = resultado.media_info || {};
    const falantes = (resultado.diarization || {}).falantes || [];
    const esquerda = [];

    if (analise.por_falante && analise.por_falante.length) {
      const maior = Math.max(...analise.por_falante.map((f) => f.tempo_s || 0)) || 1;
      esquerda.push(
        `<section class="secao"><span class="sobrancelha secao-titulo">Participação por falante</span>` +
          analise.por_falante
            .map((f, i) => {
              const ficha = falantes.find((x) => x.nome === f.nome);
              const classe = `s${((ficha && ficha.id) || i) % CORES_FALANTE}`;
              const largura = Math.max(2, Math.round((f.tempo_s / maior) * 100));
              return (
                `<div class="falante-bloco"><div class="falante-topo">` +
                `<strong class="quem ${classe}">${escapar(f.nome)}</strong>` +
                `<span class="mono">${formatarTempo(f.tempo_s)} · ${numero(f.palavras)} palavras · ${f.palavras_por_minuto} ppm</span>` +
                `</div><div class="falante-barra"><div class="${classe}" ` +
                `style="width:${largura}%;background:currentColor;animation-delay:${(i * 0.08).toFixed(2)}s"></div></div>` +
                `<span class="falante-termos">${escapar(f.termos.join(", "))}</span></div>`
              );
            })
            .join("") +
          `</section>`
      );
    }

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

    const tabela = (titulo, itens) =>
      `<section class="secao"><span class="sobrancelha secao-titulo">${titulo}</span>` +
      itens
        .map(
          ([n, v]) =>
            `<div class="dado-linha"><span>${n}</span>` +
            `<span class="mono">${escapar(String(v))}</span></div>`
        )
        .join("") +
      `</section>`;

    const direita = [tabela("A gravação em números", linhas), tabela("Detalhes técnicos", tecnicas)];
    if (!esquerda.length) {
      esquerda.push(direita.shift());
    }

    $("conteudoDados").innerHTML =
      `<div class="duas-colunas"><div class="coluna">${esquerda.join("")}</div>` +
      `<div class="coluna">${direita.join("")}</div></div>`;
  }

  // O nome do falante recebe a mesma cor em toda a tela; a lista da
  // diarização é a única fonte que carrega o índice de cor.
  function classeFalante(nome) {
    const falantes = (estado.resultado && (estado.resultado.diarization || {}).falantes) || [];
    const ficha = falantes.find((f) => f.nome === nome);
    return ficha ? ` s${ficha.id % CORES_FALANTE}` : "";
  }

  function realcar(texto, alvo) {
    const escapado = escapar(texto);
    const padrao = alvo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return escapado.replace(new RegExp(padrao, "gi"), (m) => `<mark>${m}</mark>`);
  }

  // ---------- downloads ----------
  function montarMenuDownloads() {
    const menu = $("menuBaixar");
    const itens = estado.formatos.length
      ? estado.formatos
      : [{ id: "txt", label: "Texto", ext: ".txt" }];
    menu.innerHTML =
      itens
        .map((f) => `<button type="button" class="menu-item" data-fmt="${f.id}">${escapar(f.label)}<span>${f.ext}</span></button>`)
        .join("") +
      '<button type="button" class="menu-item separado" data-fmt="__zip">Todos os formatos<span>.zip</span></button>';

    menu.querySelectorAll(".menu-item").forEach((botao) =>
      botao.addEventListener("click", () => {
        const fmt = botao.dataset.fmt;
        const url = fmt === "__zip"
          ? `/api/download/${estado.jobId}`
          : `/api/download/${estado.jobId}/${fmt}`;
        window.location.href = url;
        fecharMenu();
      })
    );
  }

  function fecharMenu() {
    $("menuBaixar").classList.add("oculto");
    $("btnBaixar").parentElement.classList.remove("aberto");
  }

  $("btnBaixar").addEventListener("click", (e) => {
    e.stopPropagation();
    const escondido = $("menuBaixar").classList.toggle("oculto");
    $("btnBaixar").parentElement.classList.toggle("aberto", !escondido);
  });
  document.addEventListener("click", fecharMenu);

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
    audio.playbackRate = estado.velocidade;
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
    const altura = canvas.clientHeight || 44;
    if (canvas.width !== Math.round(largura * escala)) {
      canvas.width = Math.round(largura * escala);
      canvas.height = Math.round(altura * escala);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(escala, 0, 0, escala, 0, 0);
    ctx.clearRect(0, 0, largura, altura);

    const estilo = getComputedStyle(document.documentElement);
    const corTocada = estilo.getPropertyValue("--tinta").trim() || "#1c1b18";
    const corRestante = estilo.getPropertyValue("--linha-media").trim() || "#d8d3c6";
    const total = audio.duration || (estado.resultado && estado.resultado.duration) || 1;
    const progresso = (audio.currentTime || 0) / total;

    const passoPx = 4;
    const barras = Math.max(1, Math.min(estado.onda.length, Math.floor(largura / passoPx)));
    const passo = estado.onda.length / barras;
    for (let i = 0; i < barras; i++) {
      const valor = estado.onda[Math.floor(i * passo)] || 0;
      const h = Math.max(2, valor * (altura - 4));
      const x = i * passoPx;
      ctx.fillStyle = i / barras <= progresso ? corTocada : corRestante;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, (altura - h) / 2, 2, h, 1);
      else ctx.rect(x, (altura - h) / 2, 2, h);
      ctx.fill();
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
      realcarTrechoAtivo(indice, segmentos[indice]);
    }
  });

  function realcarTrechoAtivo(indice, segmento) {
    document.querySelectorAll(".trecho.tocando").forEach((el) => el.classList.remove("tocando"));
    const alvo = $("listaTrechos").querySelector(`[data-indice="${indice}"]`);
    if (alvo) {
      alvo.classList.add("tocando");
      // Rolar um painel escondido faria a página inteira pular sem motivo.
      if ($("abaTrechos").classList.contains("ativo")) {
        alvo.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }

    // No texto corrido, o bloco que está tocando fica escuro e o resto recua.
    const texto = $("textoCompleto");
    texto.querySelectorAll(".texto-bloco.ativo").forEach((el) => el.classList.remove("ativo"));
    if (!segmento) {
      texto.classList.remove("tem-ativo");
      return;
    }
    const blocos = (estado.resultado && estado.resultado.dialogue) || [];
    const bloco = blocos.findIndex((b) => segmento.start >= b.start && segmento.start <= b.end);
    const el = bloco !== -1 ? texto.querySelector(`[data-bloco="${bloco}"]`) : null;
    texto.classList.toggle("tem-ativo", Boolean(el));
    if (el) el.classList.add("ativo");
  }

  audio.addEventListener("play", () => {
    $("btnPlay").classList.add("tocando");
    $("btnPlay").setAttribute("aria-label", "Pausar");
  });
  audio.addEventListener("pause", () => {
    $("btnPlay").classList.remove("tocando");
    $("btnPlay").setAttribute("aria-label", "Reproduzir");
  });
  $("btnPlay").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));

  function definirVelocidade(indice) {
    const i = Math.min(VELOCIDADES.length - 1, Math.max(0, indice));
    estado.velocidade = VELOCIDADES[i];
    audio.playbackRate = estado.velocidade;
    $("btnVelocidade").textContent = velocidadeTexto(estado.velocidade);
  }

  $("btnVelocidade").addEventListener("click", () => {
    const atual = VELOCIDADES.indexOf(estado.velocidade);
    definirVelocidade((atual + 1) % VELOCIDADES.length);
  });

  function mudarVelocidade(delta) {
    definirVelocidade(VELOCIDADES.indexOf(estado.velocidade) + delta);
    avisar(`Velocidade ${velocidadeTexto(estado.velocidade)}`);
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

  function posicionarIndicadorAbas() {
    const abas = document.querySelectorAll(".aba");
    if (!abas.length) return;
    const indice = Array.from(abas).findIndex((b) => b.classList.contains("ativa"));
    const fatia = 100 / abas.length;
    const indicador = $("abaIndicador");
    indicador.style.width = `${fatia}%`;
    indicador.style.left = `${Math.max(0, indice) * fatia}%`;
  }

  function trocarAba(id) {
    document.querySelectorAll(".aba").forEach((b) => b.classList.toggle("ativa", b.dataset.aba === id));
    document.querySelectorAll(".painel").forEach((p) => p.classList.toggle("ativo", p.id === id));
    posicionarIndicadorAbas();
  }
  document.querySelectorAll(".aba").forEach((botao) =>
    botao.addEventListener("click", () => trocarAba(botao.dataset.aba))
  );
  window.addEventListener("resize", posicionarIndicadorAbas);

  // ---------- histórico ----------
  async function abrirHistorico() {
    const lista = $("listaHistorico");
    lista.innerHTML = '<p class="vazio">Carregando…</p>';
    abrirSobreposicao("telaHistorico");
    try {
      const [{ jobs }, totais] = await Promise.all([
        (await fetch("/api/jobs?limit=200")).json(),
        (await fetch("/api/stats")).json().catch(() => ({})),
      ]);

      $("histResumo").textContent = totais && totais.tarefas
        ? `${totais.tarefas} transcrições · ${totais.horas_audio} h de áudio · ${numero(totais.palavras)} palavras · ${totais.fator_velocidade}× o tempo real`
        : "";

      if (!jobs.length) {
        lista.innerHTML = '<p class="vazio">Nenhuma transcrição registrada ainda.</p>';
        return;
      }
      lista.innerHTML = "";
      jobs.forEach((j, i) => {
        const classe = j.status === "concluido" ? "ok" : j.status === "erro" ? "erro" : "";
        const rotulo = ESTADOS[j.status] || j.status;
        const extras = [
          new Date(j.created_at * 1000).toLocaleString("pt-BR"),
          j.model,
          j.elapsed ? formatarTempo(j.elapsed) : null,
          j.speakers ? `${j.speakers} falantes` : null,
        ].filter(Boolean);
        const item = document.createElement("div");
        item.className = "hist-item";
        item.style.animationDelay = `${Math.min(i, 8) * 0.04}s`;
        item.innerHTML =
          `<div class="hist-dados"><strong>${escapar(j.filename)}</strong>` +
          `<span class="mono">${escapar(extras.join(" · "))}</span></div>` +
          `<div class="hist-acoes">` +
          `<span class="hist-estado ${classe}">${escapar(rotulo)}</span>` +
          (j.status === "concluido"
            ? `<button type="button" class="btn btn-contorno btn-pequeno" data-abrir="${j.job_id}">Abrir</button>`
            : "") +
          `<button type="button" class="hist-remover" data-remover="${j.job_id}" title="Excluir">Excluir</button></div>`;
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
      lista.innerHTML = '<p class="vazio">Não foi possível carregar o histórico.</p>';
    }
  }

  $("btnHistorico").addEventListener("click", abrirHistorico);

  // ---------- busca global ----------
  const DICA_BUSCA =
    '<p class="paleta-dica">Acentos são ignorados (sera encontra será), prefixos funcionam ' +
    "(contrat encontra contratação) e aspas procuram a frase exata.</p>";

  let temporizadorGlobal;
  function abrirBuscaGlobal() {
    abrirSobreposicao("telaBusca");
    $("campoBuscaGlobal").focus();
    $("campoBuscaGlobal").select();
  }
  $("btnBuscaGlobal").addEventListener("click", abrirBuscaGlobal);

  $("campoBuscaGlobal").addEventListener("input", (e) => {
    clearTimeout(temporizadorGlobal);
    const termo = e.target.value.trim();
    const alvo = $("resultadosBusca");
    if (termo.length < 2) {
      alvo.innerHTML = DICA_BUSCA;
      return;
    }
    temporizadorGlobal = setTimeout(async () => {
      alvo.innerHTML = '<p class="paleta-vazio">Procurando…</p>';
      try {
        const dados = await (await fetch(`/api/search?q=${encodeURIComponent(termo)}`)).json();
        const itens = dados.resultados || [];
        if (!itens.length) {
          alvo.innerHTML = '<p class="paleta-vazio">Nada encontrado.</p>';
          return;
        }
        alvo.innerHTML = itens
          .map(
            (r, i) =>
              `<button type="button" class="achado" data-job="${r.job_id}" data-inicio="${r.start}" ` +
              `style="animation-delay:${(Math.min(i, 10) * 0.03).toFixed(2)}s">` +
              `<span class="mono">${formatarTempo(r.start)}</span>` +
              `<span class="achado-corpo"><p>${r.trecho}</p>` +
              `<span class="achado-arquivo">${escapar(r.arquivo)}</span></span></button>`
          )
          .join("");

        alvo.querySelectorAll("[data-job]").forEach((botao) =>
          botao.addEventListener("click", async () => {
            await abrirTarefa(botao.dataset.job);
            irPara(parseFloat(botao.dataset.inicio));
          })
        );
      } catch (erro) {
        alvo.innerHTML = '<p class="paleta-vazio">A busca falhou.</p>';
      }
    }, 260);
  });

  // ---------- atalhos ----------
  $("btnAtalhos").addEventListener("click", () => abrirSobreposicao("modalAtalhos"));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      fecharSobreposicoes();
      fecharMenu();
      return;
    }
    const digitando =
      ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
    if (digitando || e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === "h" || e.key === "H") return abrirHistorico();
    if (e.key === "g" || e.key === "G") return abrirBuscaGlobal();
    if (e.key === "?") {
      return $("modalAtalhos").classList.contains("oculto")
        ? abrirSobreposicao("modalAtalhos")
        : fecharSobreposicoes();
    }
    if (alguemAberto()) return;

    const noResultado = !$("telaResultado").classList.contains("oculto");

    if (e.key === "/") {
      e.preventDefault();
      $("campoBusca").focus();
      return;
    }
    if (e.key === " " && audio.src && noResultado) {
      e.preventDefault();
      audio.paused ? audio.play() : audio.pause();
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

  desenharArquivos();
  carregarSistema();
})();
