# Bancada SmartGrid — entrega A1/5, issue #66

Responsável: Arthur De Marco Faggion. Revisão de software: 19/11/2026.
Base: `main` em `b01860e`; firmware em `firmware/smartgrid_placa/smartgrid_placa.ino`.
Este documento prepara a montagem e o ensaio. **Nenhum teste com Arduino real foi executado nesta entrega de software.**

## Materiais

| Quantidade | Material | Observação |
|---|---|---|
| 1 | Arduino UNO R3 / compatível com ATmega328P, 5 V | Alvo de compilação `arduino:avr:uno`; conferir a placa disponível |
| 1 | Cabo USB de dados compatível com a placa | Separar um cabo reserva; cabo somente de carga não serve |
| 2 | Potenciômetros lineares de 10 kΩ | Simulam tensão e corrente, sem sensores ligados a uma instalação |
| 1 | LED e resistor de 330 Ω | Opção mais simples para representar a carga em D7 |
| opcional | Módulo de relé com entrada compatível com lógica de 5 V | Substitui o LED; identificar polaridade de acionamento e consumo |
| 1 | Botão momentâneo normalmente aberto | Entre D2 e GND |
| 1 | Protoboard e conjunto de jumpers | Conferir continuidade das barras de alimentação |
| 1 | Computador com Arduino IDE e ambiente Python do projeto | Deixar dependências instaladas antes da apresentação |
| opcional | Multímetro | Conferência dos 0–5 V dos potenciômetros; não mede os valores simulados de 250 V/20 A |

O alvo é o UNO R3 clássico descrito na [documentação da placa](https://docs.arduino.cc/hardware/uno-rev3), não o UNO R4 ou o UNO Q.

## Montagem em baixa tensão

Monte com o USB desconectado. Use exclusivamente os 5 V do USB para a versão com LED e potenciômetros. **Não conectar a bancada à rede elétrica.** Os valores em volts e amperes exibidos pelo SmartGrid representam a simulação dos potenciômetros.

| Componente | Pino Arduino | Ligação |
|---|---|---|
| Potenciômetro de tensão | A0 | Cursor no A0; extremos em 5 V e GND |
| Potenciômetro de corrente | A1 | Cursor no A1; extremos em 5 V e GND |
| Botão de disparo | D2 | Um contato no D2 e o outro no GND; `INPUT_PULLUP` |
| LED ou entrada do módulo de relé | D7 | LED: D7 → resistor 330 Ω → ânodo; cátodo no GND |
| LED de diagnóstico | D13 / `LED_BUILTIN` | LED já existente no UNO; não exige montagem adicional |
| Referência comum | GND | Mesma barra de GND para UNO, potenciômetros, botão e circuito de indicação |

```mermaid
flowchart LR
    PC[Computador] -->|USB de dados e alimentação| UNO[Arduino UNO R3]
    V5[5 V do UNO] --> P1[Extremo do potenciômetro de tensão]
    V5 --> P2[Extremo do potenciômetro de corrente]
    P1 -->|Cursor| A0[A0]
    P2 -->|Cursor| A1[A1]
    P1 -->|Outro extremo| GND[GND compartilhado]
    P2 -->|Outro extremo| GND
    D2[D2 com INPUT_PULLUP] --> BTN[Botão normalmente aberto]
    BTN --> GND
    D7[D7] --> R[Resistor 330 ohms]
    R --> LED[Ânodo LED / cátodo em GND]
    LED --> GND
    UNO --- DIAG[D13: LED integrado]
```

- As entradas A0/A1 devem permanecer entre GND e 5 V. Não deixar o cursor flutuando. Girar os potenciômetros altera o ADC, não aplica tensão de rede ao UNO.
- No botão de quatro terminais, os dois pinos do mesmo contato já podem estar unidos. Identificar os contatos opostos antes de ligar; pressionar deve unir D2 ao GND.
- Com `INPUT_PULLUP`, solto = `HIGH` e pressionado = `LOW`. O resistor interno mantém a entrada definida; não se adiciona um resistor de pull-down nesta montagem. A ligação segue o [exemplo oficial InputPullupSerial](https://docs.arduino.cc/built-in-examples/digital/InputPullupSerial?queryID=24603a7d2a6b6b4669ef9e3e501e9d92).
- Não ligar uma bobina de relé diretamente ao D7. Se usar módulo, conectar D7 à entrada `IN`, GND à referência e alimentar o módulo conforme sua especificação, com estágio de acionamento/proteção próprio. Um módulo que exceda a alimentação disponível exige fonte adequada; conferir o esquema do fabricante antes de compartilhar alimentação. Manter os contatos de potência sem carga ou com carga de baixa tensão isolada.
- O padrão `RELE_ATIVO_EM_LOW = false` serve ao LED: `HIGH` representa carga fechada. Se o módulo for ativo em LOW, alterar somente essa constante para `true`, recompilar e validar os dois estados antes da demonstração.

## O que o firmware faz

| Item | Comportamento atual |
|---|---|
| Inicialização | `MODO_RMS = 0`, serial a **9600 bps**, estado `AGUARDANDO_LINK`, sem telemetria |
| Velocidades | Exatamente **9600, 19200, 38400 e 115200 bps**; não inclui 57600 |
| Handshake | Recebe `HS,<baud>,<intervalo_ms>` a 9600; responde `ACK,HS,...` na velocidade antiga; depois troca |
| Sincronização | Aguarda `SYNC` na velocidade escolhida por até 2000 ms; responde `ACK,SYNC` e inicia a telemetria |
| Telemetria | `TLM,<seq>,<tensao>,<corrente>,<disjuntor>` a cada 100–5000 ms; padrão solicitado pelo app: 1000 ms |
| Comandos | `RELE,0` abre; `RELE,1` fecha; responde `ACK,RELE,0|1`; `BYE` volta à espera em 9600 |
| Proteção local | Botão em LOW abre a carga; guarda simples de 50 ms; mantê-lo pressionado pode provocar nova abertura após um religamento |
| Valores com carga aberta | Corrente reportada = zero; tensão continua sendo lida; potência é calculada no desktop por `V × I` |
| Diagnóstico | D13 pisca a cada aproximadamente 500 ms na espera; fica aceso em telemetria |
| Watchdog do link | Mais de 5000 ms sem mensagem do host volta à espera a 9600; é temporização por software, não o watchdog de reset do AVR |

O desktop envia `PING` a cada 1 s. Ao perder pacotes, o limite de silêncio do desktop é `3 × intervalo de telemetria + timeout configurado` (4 s com os padrões), salvo erro de porta detectado antes disso. Após falha de `SYNC`, tenta velocidades menores: 115200 → 38400 → 19200 → 9600. Ao abrir a porta, o UNO pode reiniciar pelo DTR; o primeiro handshake permite até 6 s para o boot.

**Estado da carga:** `setup()` chama `aplicarRele(true)`. Ao conectar, o aplicativo realinha a placa ao estado persistido do disjuntor. O watchdog de comunicação e `BYE` preservam o estado do relé; não implementam abertura automática por perda de link. Desconectar USB na montagem alimentada só por USB desliga a placa e o LED, portanto não comprova o watchdog da placa. Esses comportamentos devem ser explicados na banca; a bancada representa uma carga didática.

Na revisão desta issue, a leitura de `agora = millis()` foi movida para depois de `lerSerial()` e `verificarBotao()`. A serial pode renovar `ultimoHost` durante o ciclo; usar um instante anterior causava subtração sem sinal com resultado muito grande e um timeout falso. Os pinos, os pacotes e as constantes de calibração foram preservados.

## Calibração e escala

Os valores atuais são os existentes no firmware, **não resultados de calibração experimental**:

| Constante | Valor atual | Uso |
|---|---|---|
| `MODO_RMS` | `0` | Demonstração com potenciômetros |
| `TENSAO_FUNDO_ESCALA` | `250.0` | Escala simulada em V para ADC = 1023 |
| `CORRENTE_FUNDO_ESCALA` | `20.0` | Escala simulada em A para ADC = 1023 |
| `FATOR_TENSAO_RMS` | `0.55` | Conversão de contagens RMS; valor inicial a validar antes de usar sensores |
| `SENSIBILIDADE_ACS712` | `0.185` V/A | Valor declarado para a variante de 5 A; conferir o modelo efetivo |
| `AMOSTRAS_RMS` | `160` | Amostragem sequencial; a janela não é sincronizada à fase da rede |

Para os potenciômetros, a conversão é `ADC × fundo_de_escala / 1023`. Com aproximadamente 2,5 V no cursor, espera-se aproximadamente 125 V ou 10 A simulados (corrente somente com carga fechada). A tensão real medida no cursor continua sendo aproximadamente 2,5 V.

Procedimento pendente de bancada:

1. Registrar placa, alimentação, versão do firmware, instrumento de referência e constantes iniciais.
2. Em `MODO_RMS = 0`, conferir extremos e ponto central dos dois potenciômetros com multímetro. Registrar tensão do cursor, valor reportado e repetibilidade; verificar que A0/A1 nunca excedem a alimentação.
3. Escolher e documentar a escala didática desejada. Manter 250 V e 20 A se não houver motivo para mudá-la. Ajustar fundo de escala muda a simulação, não calibra um sensor físico.
4. O modo RMS é opcional e não faz parte desta montagem. Para avaliá-lo, usar somente uma fonte de teste de baixa tensão isolada e sensores compatíveis, com referência independente e montagem aprovada para esses componentes. Não conectar os sensores à rede elétrica neste roteiro.
5. Com referência não nula e sinal estável, calcular a correção candidata `fator_novo = fator_atual × valor_referencia / valor_exibido`. Não aplicar se o ADC saturar, o valor for zero ou o ruído dominar. Conferir a sensibilidade declarada do ACS712 antes de alterar qualquer constante.
6. Recompilar, gravar e repetir medições em pelo menos três pontos; registrar erro e dispersão antes/depois. Só então chamar os fatores de calibrados. Não preencher resultados sem medição.

| Data / operador | Modo e canal | Referência | Valor antes | Constante antes/depois | Valor depois / erro | Evidência |
|---|---|---|---|---|---|---|
| Pendente | — | — | — | — | — | — |

No modelo resistivo da demonstração, `P = V × I`. A aplicação não mede fase nem fator de potência; não apresentar essa conta como medição exata de potência ativa de cargas reativas.

## Gravação e primeira conexão

1. Instalar a Arduino IDE para o sistema do computador a partir da [página oficial de software](https://www.arduino.cc/en/software). Instalar o pacote **Arduino AVR Boards** pelo gerenciador de placas, se necessário.
2. Conectar o UNO por USB e selecionar **Arduino Uno** no seletor de placa ou em **Tools/Ferramentas → Board/Placa**.
3. Selecionar a porta que aparece ao conectar a placa: por exemplo `COM3` no Windows, `/dev/ttyACM0` no Linux ou `/dev/cu.usbmodem...` no macOS. Confirmar a porta real, sem assumir esses exemplos.
4. Abrir `firmware/smartgrid_placa/smartgrid_placa.ino`. Conferir `MODO_RMS = 0`, polaridade do relé e pinagem desta página.
5. Usar **Verify/Verificar** para compilar. Corrigir erros antes de continuar; guardar saída e versão do pacote AVR.
6. Fechar SmartGrid e qualquer monitor serial que ocupe a porta; usar **Upload/Carregar**. O procedimento de placa, porta e upload está no [guia oficial da IDE 2](https://docs.arduino.cc/software/ide-v2/tutorials/getting-started/ide-v2-uploading-a-sketch/).
7. Fechar o monitor serial da IDE. Abrir o SmartGrid na raiz do projeto: Windows, `.\.venv\Scripts\python.exe main.py`; Linux/macOS, `.venv/bin/python main.py`. As dependências estão em `requirements.txt` e `requirements-dev.txt`.
8. Em **Comunicação Serial**, atualizar as portas, selecionar a porta real, escolher uma das quatro velocidades e um timeout válido (padrão 1000 ms). O intervalo vem de `data/config.json`, campo `taxa_amostragem_ms` (padrão 1000). Editar esse campo com o app fechado, se necessário.
9. Clicar **Conectar** e observar as fases: abertura → handshake a 9600 → negociação → telemetria. Escolher 115200 na tela não muda a velocidade inicial do handshake.
10. No Dashboard, girar um potenciômetro por vez: tensão, corrente e potência devem variar; o gráfico deve receber pontos. Conferir D13 e o evento de conexão no Histórico. Não esperar texto espontâneo no monitor serial antes do handshake.

Compilação opcional por CLI, sem upload:

```text
arduino-cli core update-index
arduino-cli core install arduino:avr
arduino-cli compile --fqbn arduino:avr:uno --warnings all firmware/smartgrid_placa
```

A [documentação do processo de build](https://docs.arduino.cc/arduino-cli/sketch-build-process/) diferencia compilar o `.ino` de gravá-lo na placa. A compilação não valida ligação, calibração nem comunicação elétrica.

## Checklist de aceite com Arduino real

**Todos os itens abaixo estão pendentes.** Para mudar o status, preencher data, operador, configuração, resultado e evidência (foto/vídeo, captura e trecho de log). Uma execução virtual não aprova esta tabela.

| ID | Ensaio e ação | Critério observável | Status / evidência |
|---|---|---|---|
| F01 | Conferir montagem desenergizada e depois ligar USB | Sem curto; A0/A1 em 0–5 V; pinos e polaridade corretos | **Pendente** |
| F02 | Verificar e gravar o firmware no UNO | IDE confirma upload; D13 pisca aguardando link | **Pendente** |
| F03 | Conectar escolhendo 9600 bps | Handshake começa em 9600 e termina em telemetria a 9600 | **Pendente** |
| F04 | Desconectar e conectar escolhendo 19200 bps | Handshake começa em 9600; negociação confirma 19200 | **Pendente** |
| F05 | Repetir escolhendo 38400 bps | Handshake começa em 9600; negociação confirma 38400 | **Pendente** |
| F06 | Repetir escolhendo 115200 bps | Handshake começa em 9600; negociação confirma 115200 | **Pendente** |
| F07 | Girar somente o potenciômetro A0 | Indicador de tensão varia conforme a escala; corrente permanece aproximadamente estável | **Pendente** |
| F08 | Girar somente o potenciômetro A1, carga fechada | Corrente e potência variam; tensão permanece aproximadamente estável | **Pendente** |
| F09 | Observar pelo menos 30 s de telemetria | D13 aceso, indicadores e gráfico atualizando, navegação responde | **Pendente** |
| F10 | Confirmar CORTE DE EMERGÊNCIA | D7/relé abre, badge ABERTO, corrente zero, evento persistido com dispositivo | **Pendente** |
| F11 | Soltar botão e confirmar RELIGAR CARGA | D7/relé fecha, corrente retorna, novo evento persistido | **Pendente** |
| F12 | Pressionar e soltar botão D2 | Abertura sem comando, aviso de proteção e evento de disparo; depois religar com confirmação | **Pendente** |
| F13 | Remover USB durante telemetria | App sinaliza Link perdido, indicadores param, comandos indisponíveis e GUI permanece navegável | **Pendente** |
| F14 | Recolocar USB, atualizar portas e reconectar manualmente | Novo handshake a 9600, telemetria retomada, estado alinhado ao banco | **Pendente** |
| F15 | Cliente de ensaio mantém a porta aberta após SYNC e deixa de enviar mensagens por mais de 5 s | Placa alimentada volta à espera a 9600; D13 pisca; não confundir com placa desligada | **Pendente** |
| F16 | Filtrar Histórico e reiniciar o app | Eventos confirmados permanecem; filtros tipo/data/dispositivo corretos após integrar #63 | **Pendente** |
| F17 | Fechar o app com telemetria ativa | Workers encerram, porta liberada, sem aviso de QThread destruída em execução | **Pendente** |

Após queda, aguardar a thread serial anterior encerrar, clicar Atualizar portas e Conectar. A aplicação cria outro `SerialWorker`; **não há reconexão automática**. Se a porta mudou, selecionar o nome novo.

Se o upload falhar, conferir placa, cabo de dados, porta e aplicações que a ocupam. O [guia de falhas de upload da Arduino](https://support.arduino.cc/hc/en-us/articles/4403365313810-If-your-sketch-doesn-t-upload) detalha a investigação; não trocar firmware ou fatores de calibração para tentar corrigir uma porta ocupada.

## Plano B — placa virtual

`tools/simulador_placa.py` usa `os.openpty()`, `tty` e descritores POSIX. Funciona em Linux/macOS; **não roda diretamente no Python nativo do Windows**, onde o import de `tty` falha por ausência de `termios`. Essa restrição é compatível com a [disponibilidade Unix de pseudoterminais no Python](https://docs.python.org/3/library/pty.html).

Alternativas para a apresentação:

1. Notebook Linux ou macOS com o app e o simulador na mesma máquina: opção direta.
2. Windows com uma distribuição Linux no **WSL 2 + WSLg**, previamente preparada. Executar tanto o SmartGrid quanto o simulador dentro da mesma distribuição; não colar `/dev/pts/N` no app Windows. O suporte gráfico depende dos requisitos de versão e drivers descritos no [guia oficial do WSLg](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps).
3. Máquina virtual Linux com ambiente gráfico. App e simulador devem compartilhar o mesmo ambiente e a mesma pty. Não é necessário encaminhar USB para simular.

Preparar um clone Linux separado, por exemplo `~/SmartGrid`; não reutilizar o `.venv` criado pelo Windows. Em uma distribuição Ubuntu já instalada, confirmar Python, `python3-venv` e as bibliotecas de execução do Qt. Dependências de X11/`xcb` variam entre distribuições; consultar os [requisitos oficiais do Qt](https://doc.qt.io/qt-6/linux-requirements.html) em caso de erro de plugin. `QT_QPA_PLATFORM=offscreen` serve aos testes, não à apresentação visual.

No terminal Linux/macOS, a partir da raiz do clone:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/simulador_placa.py
```

Manter esse terminal aberto. Em outro terminal, no mesmo clone e ambiente:

```bash
.venv/bin/python main.py
```

Copiar o caminho realmente impresso pelo simulador (`/dev/pts/N` em Linux, normalmente `/dev/ttys...` em macOS) para o campo editável de porta serial; escolher a velocidade e conectar. No terminal do simulador, digitar a letra **e Enter**:

| Entrada | Efeito |
|---|---|
| `d` | Disparo virtual: abre o disjuntor; a próxima telemetria informa o estado |
| `x` | Fecha o mestre da pty e termina o simulador; o app deve detectar Link perdido |
| `q` ou Ctrl+C | Encerra o simulador |

Depois de `x`, executar o simulador novamente, copiar a nova porta e reconectar o app manualmente. Não reutilizar um caminho antigo sem conferir.

Para demonstrar fallback, começar uma instância nova:

```bash
.venv/bin/python tools/simulador_placa.py --falhar-sync 115200
```

Escolher 115200 no app. O simulador ignora esse SYNC; depois dos prazos, a negociação deve concluir em 38400. A pty não impõe baud rate elétrico: isso valida a sequência lógica e os estados, não a qualidade do enlace do UNO a 115200.

Recursos: handshake, SYNC, quatro velocidades lógicas, telemetria sintética variável, RELE/ACK, disparo `d`, BYE, watchdog de link e fechamento `x`. Limitações: não lê potenciômetros, não aciona LED/relé, não emula reset por DTR, ruído elétrico, conversor USB ou calibração; os valores são sintéticos e não há comando de console para fixar tensão/corrente. Não declarar os ensaios F01–F17 concluídos a partir do simulador.

Testes automatizados existentes para preparar o Plano B, no Linux/macOS:

```bash
.venv/bin/python -m pytest -q tests/test_protocolo.py tests/test_serial_worker.py tests/test_aplicacao_controller.py
```

Não editar ou suprimir esses testes para simular aprovação no Windows.

| ID | Ensaio virtual/manual | Status / evidência |
|---|---|---|
| V01 | App gráfico + simulador, handshake nas quatro opções e gráfico | **Pendente de execução em Linux/macOS ou WSLg** |
| V02 | Corte, religamento e comando `d` | **Pendente** |
| V03 | Comando `x`, GUI responsiva e reconexão com nova pty | **Pendente** |
| V04 | `--falhar-sync 115200` resulta em 38400 | **Pendente** |
| V05 | Ensaio completo de 18 minutos usando Plano B | **Pendente** |

## Evidências disponíveis nesta revisão

- Compilação do firmware para `arduino:avr:uno` com Arduino CLI 1.6.0 e Arduino AVR Boards 1.8.8: aprovada em `MODO_RMS = 0` (8668 bytes de programa, 412 bytes globais) e em uma cópia temporária com `MODO_RMS = 1` (9354 bytes de programa, 412 bytes globais). O arquivo versionado continua no modo de potenciômetros. Avisos de parâmetros não utilizados vieram do core Arduino, não do sketch. Não houve upload.
- Revisão do simulador e de seus comandos: realizada sobre a `main` citada. Execução POSIX e apresentação gráfica do Plano B: **pendentes neste ambiente Windows**.
- Upload, medições, calibração, acionamento físico, quatro velocidades elétricas e remoção/reconexão do cabo: **não executados**.
- A issue #66 só satisfaz o aceite físico após preencher os checklists e ensaiar com os quatro integrantes. O histórico final e o diálogo de limites assíncrono exigem integrar as entregas #63 e #62, respectivamente; esta branch de documentação continua independente delas.

## Preparação para transportar

- UNO montado e identificado; dois potenciômetros e botão acessíveis; LED/relé visível; USB principal e reserva.
- Firmware correto já gravado; captura do upload e registros F01–F17 quando executados.
- Versão final integrada do app, dependências, banco demonstrativo e backup de `data/config.json` conforme o [roteiro](roteiro-demonstracao.md).
- Notebook/ambiente do Plano B já ensaiado; terminais separados; carregador; capturas locais e vídeo curto de contingência, identificados como gravação.
- Cronômetro, roteiro e tabela de evidências com data/operador, sem marcar testes pendentes como aprovados.
