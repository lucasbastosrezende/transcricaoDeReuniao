# Transcritor pt-BR

Transcrição de vídeos e áudios em português do Brasil. Roda inteiramente na sua
máquina: nada é enviado para a internet, não há chave de API, cota nem custo.

```
iniciar.bat
```

Abre em <http://localhost:8000>. Na primeira execução o script cria o ambiente
Python, instala as dependências e, se necessário, o FFmpeg. Em Linux e macOS,
use `./iniciar.sh`.

---

## O que ele entrega

| | |
|---|---|
| **Entrada** | MP4, MKV, MOV, AVI, WEBM, MP3, WAV, M4A, FLAC, OPUS e outros — vários arquivos de uma vez, em fila |
| **Saída** | TXT, TXT com tempos, SRT, VTT, ASS, DOCX, PDF, HTML, Markdown, resumo, CSV, TSV, JSON — ou tudo em um ZIP |
| **Durante** | texto aparecendo ao vivo, barra de progresso, tempo restante, posição na fila, cancelamento |
| **Depois** | quem falou cada trecho, resumo automático, capítulos, pendências, player com forma de onda, edição salva, busca em todas as transcrições |

---

## Como a precisão foi obtida

**Modelo `large-v3-turbo`.** Usa o mesmo codificador do `large-v3` — a parte da
rede que de fato interpreta o áudio — com 4 camadas de decodificação em vez de
32. O resultado é a precisão da família `large` em português a uma fração do
custo de processamento. É bastante superior ao `medium` em nomes próprios,
acentuação e pontuação.

**Busca em feixe (*beam search*).** O perfil padrão avalia 5 hipóteses por
trecho em vez de aceitar a primeira. Custa cerca de 20% mais tempo e corrige
erros reais de preposição e vírgula.

**Detecção de voz antes de decodificar.** O Silero VAD recorta os silêncios, que
é onde o Whisper mais inventa texto.

**Tratamento do áudio.** Antes de reconhecer, o FFmpeg corta o ronco de rede
elétrica abaixo de 60 Hz e nivela o volume (`loudnorm`). Em reunião gravada por
um microfone só, isso é o que salva a fala de quem estava longe da mesa. O áudio
do player continua sendo o original, sem filtro — quem confere ouvindo precisa
ouvir o que foi realmente gravado. Desligue com `TRANSCRITOR_TRATAR_AUDIO=nao`.

**Barreiras contra alucinação.** Limiares de probabilidade, razão de compressão,
penalidade de repetição e um filtro dos bordões que o modelo costuma inserir
sobre silêncio (*"Legendas pela comunidade Amara.org"*, *"Inscreva-se no
canal"*, *"[Música]"*). Laços de repetição são detectados e removidos.

**Vocabulário do seu contexto.** O campo na tela alimenta o parâmetro `hotwords`
do modelo. Nomes de pessoas, siglas internas e jargão técnico — `SEMEC`,
`SISCOMEX`, `Dra. Marcela Bittencourt` — passam a ser esperados em vez de
adivinhados foneticamente. Depois da transcrição, a grafia que você digitou é
reaplicada ao texto: o modelo escreveria "siscomex", e sai `SISCOMEX`. É o
ajuste que mais melhora transcrições de reuniões.

**Pós-processamento em português.** Espaço antes de vírgula, maiúscula depois de
ponto, reticências, moeda, porcentagem, unidades — tudo normalizado, preservando
`1,5` e `10:30`. Abreviações (`Dr.`, `Sra.`, `etc.`) não são confundidas com fim
de frase, o que antes partia uma frase no meio e jogava o clique do trecho para
o lugar errado. O texto é dividido em parágrafos nas pausas reais da fala e na
troca de falante.

**Legendas com quebra profissional.** Os tempos por palavra permitem montar
blocos de no máximo 2 linhas de 42 colunas, com duração mínima de 1 s, sem
sobreposição e cortando de preferência depois de uma pontuação. As linhas são
equilibradas para evitar a "escadinha". A aba **Legendas** mostra um controle de
qualidade: blocos rápidos demais para ler, linhas longas e sobreposições.

---

## Separação de falantes

Cada trecho recebe um rótulo — *Falante 1*, *Falante 2* — e a tela mostra quanto
cada pessoa falou, com que ritmo e sobre quais termos. A legenda ASS sai colorida
por voz e o DOCX vira um diálogo.

Funciona **sem pyannote, sem PyTorch e sem token do Hugging Face**. O caminho é
outro: os mesmos 16 kHz que alimentam o reconhecimento são convertidos em MFCC
(a assinatura de timbre de uma voz), cada trecho vira um vetor de média e desvio
desses coeficientes, e uma clusterização aglomerativa agrupa as vozes parecidas.
O número de falantes não é informado — sai do ponto em que o dendrograma é
cortado.

É uma aproximação, e vale saber onde ela erra: **acerta bem** quantas vozes
existem e quem falou mais em reuniões e entrevistas com microfone razoável;
**erra mais** com vozes muito parecidas, muita fala sobreposta ou ruído alto.
Ajuste `TRANSCRITOR_FALANTES_LIMIAR` (maior = menos falantes distintos) ou
desligue em **Processamento extra**.

---

## Leitura automática do conteúdo

Também offline e determinística — rodar duas vezes dá exatamente o mesmo texto.

| Recurso | Como é feito |
|---|---|
| **Resumo** | TextRank: as frases viram vetores TF-IDF, a semelhança forma um grafo e o PageRank encontra as mais representativas. Um passo de diversidade evita cinco versões da mesma frase |
| **Temas** | TF-IDF com detecção de expressões — "nota fiscal" vira uma entrada só, não duas |
| **Capítulos** | Onde o vocabulário muda de eixo entre blocos vizinhos, o assunto mudou |
| **Pendências** | Frases com cara de compromisso assumido: "ficou de", "vou enviar", "até sexta" |
| **Perguntas** | Tudo que foi perguntado, na ordem |
| **Números** | Ritmo em palavras por minuto, proporção de fala e silêncio, riqueza lexical, maior pausa, vícios de linguagem, trechos de baixa confiança |

---

## Edição que vale

O texto transcrito quase sempre precisa de um ajuste — um nome próprio, uma
sigla. Na aba **Trechos**, clique em qualquer linha e corrija. Ao salvar, o
servidor refaz parágrafos, legendas, resumo e **todos os arquivos de saída** a
partir do texto corrigido, mantendo os tempos originais. O DOCX que você baixar
depois disso tem a correção.

---

## Busca em tudo que já foi transcrito

O histórico vive num SQLite com índice **FTS5**. A lupa no topo procura uma
palavra ou frase em todas as gravações já processadas e devolve o trecho e o
minuto exato — clicar leva direto ao ponto do áudio.

Acentos são ignorados (`sera` encontra `será`), busca por prefixo é automática
(`contrat` encontra `contrato` e `contratação`) e aspas procuram a frase exata.

---

## Perfis

| Perfil | Quando usar |
|---|---|
| **Rápido** | Rascunho, quando você só precisa saber o que foi dito |
| **Equilibrado** *(padrão)* | Uso normal — melhor relação entre tempo e precisão |
| **Precisão máxima** | Áudio ruim, muitos interlocutores, sotaque carregado |

## Modelos

| Modelo | Tamanho | Precisão em pt-BR | Velocidade em CPU |
|---|---|---|---|
| `large-v3-turbo` *(padrão)* | 809 M | ótima | boa |
| `large-v3` | 1,55 B | ótima | muito lenta sem GPU |
| `medium` | 769 M | razoável | média |
| `small` | 244 M | limitada | alta |
| `base` | 74 M | só para teste | imediata |

O modelo é baixado uma única vez e fica em cache. Uma placa de vídeo NVIDIA, se
houver, é detectada automaticamente e usada em `float16`.

---

## Linha de comando

Para uma pasta inteira de madrugada, ou dentro de um script maior:

```bat
.venv\Scripts\python.exe cli.py reuniao.mp4
.venv\Scripts\python.exe cli.py gravacoes\ --perfil precisao --saida transcricoes
.venv\Scripts\python.exe cli.py entrevista.wav --vocabulario "SISCOMEX, Dra. Marcela"
.venv\Scripts\python.exe cli.py --buscar "contrato da prefeitura"
.venv\Scripts\python.exe cli.py --ambiente
```

`--formatos txt,srt` limita o que é gerado, `--silencioso` imprime só os
caminhos criados (bom para encadear com outro comando), `--sem-falantes` e
`--sem-analise` cortam o processamento extra.

---

## API

Todas as rotas respondem JSON. Com `TRANSCRITOR_TOKEN` definido, exigem
`Authorization: Bearer <token>`.

| Rota | O que faz |
|---|---|
| `GET /api/health` | Sinal de vida, versão, hardware, estado da fila |
| `GET /api/system` | Modelos, perfis, extensões e recursos disponíveis |
| `POST /api/transcribe` | Envia um ou vários arquivos e enfileira as tarefas |
| `GET /api/events/{id}` | Fluxo SSE com progresso e trechos ao vivo |
| `GET /api/progress/{id}` | Estado e resultado de uma tarefa |
| `PATCH /api/jobs/{id}/text` | Salva correções e regera todas as saídas |
| `GET /api/search?q=` | Busca em todas as transcrições |
| `GET /api/download/{id}[/{fmt}]` | Um formato, ou tudo em ZIP |
| `GET /api/stats` | Totais acumulados da máquina |
| `DELETE /api/jobs/{id}` | Remove a transcrição e os arquivos gerados |

---

## Arquivos do projeto

| Arquivo | Responsabilidade |
|---|---|
| `app.py` | Servidor web, fila de tarefas, eventos em tempo real, downloads |
| `transcriber.py` | Motor de transcrição sobre o faster-whisper |
| `diarize.py` | Separação de falantes: MFCC e clusterização, em numpy puro |
| `analysis.py` | Resumo, temas, capítulos, pendências e estatísticas |
| `ptbr.py` | Limpeza do texto em português e montagem das legendas |
| `exporters.py` | Geração de TXT, SRT, VTT, ASS, DOCX, PDF, HTML, MD, CSV, TSV e JSON |
| `pdfwriter.py` | Gerador de PDF mínimo, sem dependências |
| `store.py` | Índice SQLite/FTS5 do histórico e da busca global |
| `media.py` | FFmpeg e FFprobe: metadados, extração e leitura do áudio |
| `config.py` | Todos os ajustes, sobrescritíveis por `.env` ou variável de ambiente |
| `cli.py` | Transcrição pela linha de comando |
| `test_setup.py` | Diagnóstico do ambiente |
| `tests/` | Bateria de testes das camadas puras |

---

## Ajustes

Copie `.env.example` para `.env` e edite, ou use variáveis de ambiente:

```bat
set TRANSCRITOR_PORT=9000
set TRANSCRITOR_MODELO=large-v3
set TRANSCRITOR_LEGENDA_COLUNAS=38
set TRANSCRITOR_RETENTION_HORAS=168
set TRANSCRITOR_FALANTES=nao
iniciar.bat
```

---

## Testes

```bat
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest
```

Cobrem as camadas que não dependem de modelo nem de rede: limpeza de texto e
legendagem, análise de conteúdo, exportadores (incluindo a validade do DOCX e do
PDF gerados), o índice SQLite e a diarização sobre um WAV sintético com duas
vozes. É onde moram os erros silenciosos — os que produzem um arquivo que abre,
mas com o conteúdo errado.

---

## Notas de funcionamento

* **Uma tarefa por vez.** A transcrição satura todos os núcleos; duas em
  paralelo terminariam depois do que em sequência. Vários arquivos podem ser
  enviados juntos: eles entram numa fila e a tela mostra a posição de cada um.
* **A pasta `uploads/` é temporária.** O vídeo enviado é apagado assim que a
  transcrição termina; o que sobrar de execuções interrompidas é limpo na
  inicialização. Guarde os originais em outro lugar.
* **Resultados ficam 72 horas** em `outputs/`, e reaparecem no histórico mesmo
  depois de reiniciar o programa. Ajuste com `TRANSCRITOR_RETENTION_HORAS`.
* **Atalhos:** `espaço` reproduz/pausa, `←` `→` movem 5 s, `J` `L` movem 10 s,
  `,` `.` pulam de trecho, `-` `+` mudam a velocidade, `/` busca aqui, `G` busca
  em tudo, `H` abre o histórico, `T` alterna o tema, `?` mostra a lista.

---

## Se algo der errado

| Sintoma | Causa provável |
|---|---|
| "FFmpeg não foi encontrado" | `winget install Gyan.FFmpeg`, depois reabra |
| "Este arquivo não possui faixa de áudio" | O vídeo é mudo ou a faixa está corrompida |
| "Nenhuma fala foi reconhecida" | Só música ou ruído; ou o idioma fixado está errado |
| Primeira transcrição demora demais | O modelo está sendo baixado; acontece uma vez só |
| Porta 8000 ocupada | Outra instância está aberta, ou use `set TRANSCRITOR_PORT=8001` |
| Texto com palavras erradas | Preencha o vocabulário e use o perfil de precisão máxima |
| Falantes trocados no meio | Vozes parecidas ou fala sobreposta; ajuste `TRANSCRITOR_FALANTES_LIMIAR` |
| O programa não abre depois de mover a pasta | `iniciar.bat` detecta o ambiente quebrado e o recria sozinho |
