# Histórico de mudanças

## 2.0.0

Transcrever passou a ser o começo do trabalho, não o fim dele: além do texto, a
versão 2 diz quem falou, do que se falou e o que ficou pendente — e deixa
corrigir o resultado sem perder os arquivos gerados.

### Novidades

* **Separação de falantes** (`diarize.py`), offline e sem pyannote, PyTorch ou
  token do Hugging Face. MFCC calculado em streaming sobre o mesmo WAV do
  reconhecimento, assinatura por trecho e clusterização aglomerativa que
  descobre sozinha quantas vozes existem.
* **Leitura automática do conteúdo** (`analysis.py`): resumo por TextRank com
  passo de diversidade, temas com detecção de expressões, capítulos por deriva
  de vocabulário, pendências, perguntas e estatísticas de fala — incluindo
  participação por falante.
* **Histórico em SQLite com FTS5** (`store.py`) e **busca em todas as
  transcrições**, sem acento, por prefixo ou por frase exata, com salto direto
  para o minuto da ocorrência.
* **Edição que vale**: corrigir um trecho na tela regera parágrafos, legendas,
  resumo e todos os arquivos de saída, preservando os tempos.
* **Fila de vários arquivos** num envio só, com posição e progresso individual.
* **Novos formatos**: ASS (colorida por falante), PDF (gerador próprio, sem
  dependências), HTML autocontido com busca, TSV e um resumo em Markdown.
  O DOCX ganhou folha de estilos, títulos navegáveis e formato de diálogo.
* **Forma de onda** no player, desenhada a partir do envelope do áudio.
* **Tratamento do áudio** antes de reconhecer (corte de graves e nivelamento de
  volume), aplicado só ao que alimenta o modelo — o player continua tocando o
  original.
* **Linha de comando** (`cli.py`) para pastas inteiras, scripts e máquinas sem
  navegador; `--ambiente` e `--buscar` inclusos.
* **Configuração por arquivo `.env`**, autenticação opcional por token, CORS
  configurável e limite de upload.
* **Bateria de testes** (`tests/`) cobrindo texto, legendas, análise,
  exportadores, índice e diarização.
* **Atalhos de teclado** ampliados, filtro por falante, filtro de trechos de
  baixa confiança e controle de qualidade das legendas.

### Correções

* **Cancelar não funcionava em corrida.** O evento de cancelamento era criado
  *depois* de a tarefa entrar na fila; se o trabalhador a pegasse antes, ele
  criava o próprio evento e o botão passava a acionar um objeto descartado.
  Agora o evento nasce antes do enfileiramento.
* **Abreviações partiam a frase.** `Dr.`, `Sra.` e `etc.` eram lidos como ponto
  final, quebrando o trecho no meio e mandando o clique para o tempo errado.
* **Caminho interno vazava para o navegador.** O `upload_path` do servidor ia em
  toda resposta da API.
* **Identificador de tarefa não era validado.** Como ele vira nome de pasta,
  agora só um UUID nosso é aceito.
* **Confiança do trecho estava errada depois da divisão em frases.** Cada frase
  herdava a média do bloco de 30 segundos inteiro; agora usa a probabilidade das
  próprias palavras.
* **Resposta gigante à toa.** Os tempos por palavra — até 80% do JSON — eram
  enviados ao navegador, que não os usa. Continuam íntegros no arquivo exportado.
* **Um formato quebrado derrubava os outros.** A exportação agora isola cada
  formato.
* **Áudio sem fala terminava "com sucesso"** e um resultado vazio; agora explica
  o que aconteceu.
* **O trabalhador podia morrer** com uma exceção inesperada e deixar a fila
  parada para sempre.
* **`iniciar.bat` não se recuperava de um ambiente quebrado** — situação comum
  depois de mover a pasta ou reinstalar o Python. Agora detecta, recria e
  reinstala as dependências quando o `requirements.txt` muda.
* Repositório: `uploads/`, `outputs/` e `__pycache__/` estavam versionados,
  incluindo mais de 1 GB de vídeo. Agora há um `.gitignore` de verdade.

## 1.0.0

Primeira versão: servidor FastAPI com fila de uma tarefa, transcrição com
faster-whisper `large-v3-turbo`, texto ao vivo por SSE, player sincronizado,
legendas SRT/VTT com quebra equilibrada, exportação em TXT, DOCX, Markdown, CSV
e JSON, e histórico em disco.
