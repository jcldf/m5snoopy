# Audio Implant Monitor - Distribution Instructions

Esta pasta contém o programa executável pré-compilado "Audio Implant Monitor.exe" e todas as suas dependências (Numpy, Vosk, CustomTkinter, SoundDevice, etc.).

## Como rodar o programa

1. **Baixe ou coloque o modelo do Vosk (Português) nesta pasta.**
   Você precisa garantir que exista uma pasta chamada "model" examente no mesmo diretório em que está o arquivo "Audio Implant Monitor.exe".
   A estrutura deve ficar assim:
   
   /Audio Implant Monitor/
   ├── Audio Implant Monitor.exe
   ├── (vários arquivos .dll e .pyd)
   ├── _internal/
   └── model/
       ├── am/
       ├── conf/
       ├── graph/
       ├── ivector/
       ├── rescore_bak/ (caso tenha renomeado o rescore)
       └── README etc.

2. **Execute com um duplo clique!**
   Basta dar um duplo clique em "Audio Implant Monitor.exe".
   O aplicativo abrirá a interface gráfica sem a tela preta de console atrás.

## Resolução de Problemas
- Se a interface não abrir ou der um erro de "Failed to load model", verifique se a pasta `model` está no mesmo diretório do `.exe` e se não há nada corrompido dentro dela.
- Você pode passar o caminho manual para o modelo na interface do aplicativo usando o campo "Vosk Model".
