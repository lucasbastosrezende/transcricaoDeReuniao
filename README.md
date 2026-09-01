# Transcritor pt-BR

Transcrição de vídeos e áudios em português do Brasil. Roda inteiramente na sua
máquina: nada é enviado para a internet, não há chave de API, cota nem custo.

```
iniciar.bat
```

Abre em <http://localhost:8000>. Na primeira execução o script cria o ambiente
Python, instala as dependências e, se necessário, o FFmpeg.

---

## O que ele entrega

| | |
|---|---|
| **Formatos de entrada** | MP4, MKV, MOV, AVI, WEBM, MP3, WAV, M4A, FLAC, OPUS e outros |
| **Formatos de saída** | TXT (com parágrafos), TXT com tempos, SRT, VTT, DOCX, Markdown, CSV, JSON — ou tudo em um ZIP |
| **Durante o processo** | texto aparecendo ao vivo, barra de progresso, tempo restante estimado, cancelamento |
| **Depois** | player sincronizado, clique no trecho para ouvir, busca, edição do texto, histórico |

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

**Barreiras contra alucinação.** Limiares de probabilidade, razão de compressão,
penalidade de repetição e um filtro dos bordões que o modelo costuma inserir
sobre silêncio (*"Legendas pela comunidade Amara.org"*, *"Inscreva-se no
canal"*). Laços de repetição são detectados e removidos.

**Vocabulário do seu contexto.** O campo na tela alimenta o parâmetro `hotwords`
do modelo. Nomes de pessoas, siglas internas e jargão técnico — `SEMEC`,
`SISCOMEX`, `Dra. Marcela Bittencourt` — passam a ser esperados em vez de
adivinhados foneticamente. É o ajuste que mais melhora transcrições de reuniões.

**Pós-processamento em português.** Espaço antes de vírgula, maiúscula depois de
ponto, reticências, repetições — tudo normalizado, preservando `1,5` e `10:30`.
O texto é dividido em parágrafos nas pausas reais da fala.

**Legendas com quebra profissional.** Os tempos por palavra permitem montar
blocos de no máximo 2 linhas de 42 colunas, com duração mínima de 1 s, sem
sobreposição e cortando de preferência depois de uma pontuação. As linhas são
equilibradas para evitar a "escadinha".

---

## Perfis

| Perfil | Quando usar |
|---|---|
| **Rápido** | Rascunho, quando você só precisa saber o que foi dito |
| **Equilibrado** *(padrão)* | Uso normal — melhor relação entre tempo e precisão |
| **Precisão máxima** | Áudio ruim, muitos interlocutores, sotaque carregado |

---

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

## Arquivos do projeto

| Arquivo | Responsabilidade |
|---|---|
| `app.py` | Servidor web, fila de tarefas, eventos em tempo real, downloads |
| `transcriber.py` | Motor de transcrição sobre o faster-whisper |
| `ptbr.py` | Limpeza do texto em português e montagem das legendas |
| `exporters.py` | Geração de TXT, SRT, VTT, DOCX, MD, CSV e JSON |
| `media.py` | FFmpeg e FFprobe: metadados e extração de áudio |
| `config.py` | Todos os ajustes, sobrescritíveis por variável de ambiente |
| `test_setup.py` | Diagnóstico do ambiente |

---

## Ajustes

Tudo em `config.py` aceita variável de ambiente. Alguns exemplos:

```bat
set TRANSCRITOR_PORT=9000
set TRANSCRITOR_MODELO=large-v3
set TRANSCRITOR_LEGENDA_COLUNAS=38
set TRANSCRITOR_RETENTION_HORAS=168
iniciar.bat
```

---

## Notas de funcionamento

* **Uma tarefa por vez.** A transcrição satura todos os núcleos; duas em
  paralelo terminariam depois do que em sequência. Novos envios entram numa
  fila e a tela mostra a posição.
* **A pasta `uploads/` é temporária.** O vídeo enviado é apagado assim que a
  transcrição termina; o que sobrar de execuções interrompidas é limpo na
  inicialização. Guarde os originais em outro lugar.
* **Resultados ficam 72 horas** em `outputs/`, e reaparecem no histórico mesmo
  depois de reiniciar o programa. Ajuste com `TRANSCRITOR_RETENTION_HORAS`.
* **Atalhos:** `espaço` reproduz/pausa, `←` `→` avançam 5 s, `/` foca a busca,
  `T` alterna o tema.

---

## Se algo der errado

| Sintoma | Causa provável |
|---|---|
| "FFmpeg não foi encontrado" | `winget install Gyan.FFmpeg`, depois reabra |
| "Este arquivo não possui faixa de áudio" | O vídeo é mudo ou a faixa está corrompida |
| Primeira transcrição demora demais | O modelo está sendo baixado; acontece uma vez só |
| Porta 8000 ocupada | Outra instância está aberta, ou use `set TRANSCRITOR_PORT=8001` |
| Texto com palavras erradas | Preencha o campo de vocabulário e use o perfil de precisão máxima |
