# 🎙️ ESP32 Audio Implant Monitor (M5Snoopy Receiver)

Um aplicativo Python (Cliente) desenvolvido para conectar-se a um "Audio Implant" via Wi-Fi (como um ESP32-S3). Ele recebe um fluxo de áudio PCM bruto (Raw PCM) via protocolo HTTP, reproduz o áudio em tempo real e realiza a transcrição de fala para texto (Speech-to-Text - STT) de forma **totalmente offline** utilizando o [Vosk](https://alphacephei.com/vosk/). Todas as transcrições são salvas automaticamente em um arquivo de log local com data e hora.

## ✨ Funcionalidades
- **Recepção de Áudio via Wi-Fi:** Conecta-se diretamente ao servidor HTTP do dispositivo (ex: ESP32).
- **Reprodução em Tempo Real:** Toca o áudio recebido simultaneamente usando a biblioteca `sounddevice`.
- **Transcrição Offline (STT):** Processa o áudio localmente usando modelos da biblioteca Vosk, sem necessidade de conexão com a internet ou APIs pagas.
- **Interface Gráfica (Dark Mode):** Interface amigável e moderna construída com `customtkinter`.
- **Modo Console de Segurança:** Caso a interface gráfica falhe ou as dependências visuais não sejam encontradas, o sistema funciona perfeitamente via linha de comando (Console).
- **Logs de Transcrição:** Salva silenciosamente todo o texto transcrito em arquivos `.txt`.
- **VU Meter:** Um indicador visual simples de volume/atividade de rede na própria interface.

---

## ⚙️ Instalação e Configuração

### 1. Instalando Dependências do Python
Para rodar este projeto através do código-fonte (Python), você vai precisar do Python 3.8 ou superior instalado.
Abra seu terminal (ou prompt de comando) na pasta do projeto e instale as dependências executando:

```bash
pip install -r requirements.txt
```
*(Isso instalará: `vosk`, `sounddevice`, `numpy`, e `customtkinter`)*

### 2. Baixando o Modelo de Reconhecimento de Voz (Vosk)
O aplicativo precisa de um modelo de linguagem pré-treinado para conseguir ouvir e transformar a voz em texto. **Sem esse passo, a funcionalidade de transcrição não funcionará.**

1. Acesse a página oficial de modelos do Vosk: [https://alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)
2. Procure pelo modelo do idioma que você deseja transcrever. Por exemplo, para português, procure por **Portuguese** e baixe o arquivo `vosk-model-small-pt-X.X` (mais leve) ou a versão maior (mais precisa, exige mais do computador).
3. **Extraia o arquivo `.zip` baixado.**
4. Renomeie a pasta extraída para **`model`** (tudo em minúsculo).
5. Coloque a pasta `model` **exatamente dentro da pasta principal** deste projeto, junto ao arquivo `implant_monitor.py`.

A estrutura final das suas pastas deve ficar parecida com isso:
```text
/m5snoopy receiver/
├── implant_monitor.py
├── requirements.txt
├── README.md
└── model/               <-- (O modelo do Vosk extraído deve ficar aqui)
    ├── am/
    ├── conf/
    ├── graph/
    ├── ivector/
    └── README ...
```

---

## ▶️ Como Usar e Executar

### Rodando o script Python
Abra seu terminal na pasta do projeto e use o comando mágico:
```bash
python implant_monitor.py
```
A interface gráfica deverá se abrir. Nela, preencha os dados:
- **IP:** Digite o IP do seu implante de áudio (Padrão: `192.168.4.1`).
- **Port:** A porta de fluxo usada pelo implante (Padrão: `81`).
- **Token:** Token de segurança HTTP (Padrão: `root`).
- **Vosk Model:** Deixe apenas como `model` (que é a pasta que você criou no passo anterior).
- **Gain:** Multiplicador de volume (1.0 = normal, 2.0 = o dobro do volume, etc).

Clique no botão verde **▶ Connect** e pronto! O áudio começará a tocar no seu computador e a transcrição ao vivo aparecerá na tela.

---

### Executando via versão compilada (Arquivo `.exe`)
Caso tenha recebido a versão executável autossuficiente (sem necessidade do Python instalado), o uso é ainda mais simples:
1. Siga o **Passo 2** descrito acima (baixar o Vosk e extrair a pasta `model` exatamente no mesmo diretório em que está o arquivo `.exe`).
2. Dê um duplo-clique no arquivo executável (ex: `Audio Implant Monitor.exe`).
3. O aplicativo abrirá imediatamente.

---

## ⚠️ Resolução de Problemas (Troubleshooting)

- **O áudio está falhando ou atrasado:** Isso ocorre por má conexão Wi-Fi entre o PC e o ESP32. Verifique o sinal ou fique mais perto do dispositivo transmissor.
- **Não estou ouvindo nenhum aúdio:** Verifique se você está conectado na mesma rede que o equipamento de envio de áudio (ou no Ponto de Acesso criado por ele).
- **A interface não abre/Erro "Failed to load Vosk model":** O aplicativo não encontrou os arquivos do Vosk. Tem certeza que a pasta extraída foi renomeada para `model` e colocada no mesmo local que o executável ou o arquivo Python?
- **Mensagem de "Vosk not available":** Caso não tenha baixado o vosk pelo PIP, ou a instalação tenha falhado na sua máquina.

### Informações Técnicas para o Dispositivo Transmissor
Este script espera um endpoint HTTP (Ex: `GET /stream?token=root`) que emita áudio nestas especificações rígidas:
- **Formato:** Raw PCM (apenas dados, sem cabeçalho WAV)
- **Tipo de Dados:** 16-bit inteiro assinado (Signed 16-bit Little-Endian)
- **Taxa de Amostragem (Sample Rate):** 16.000 Hz (16 kHz)
- **Canais:** 1 (Mono)
