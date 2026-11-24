# Bancada SmartGrid — firmware obrigatório do Arduino UNO

Revisão: 23/11/2026. Responsável pelo ensaio: Arthur De Marco Faggion.
Os testes automatizados usam transporte em memória. **Os testes físicos abaixo permanecem pendentes.** Não modificar o firmware em `firmware/smartgrid_placa/smartgrid_placa.ino`.

## Materiais e ligações

Usar Arduino UNO R3/ATmega328P, cabo USB de dados, dois potenciômetros de 10 kΩ, botão normalmente aberto, resistor de pull-down externo (por exemplo 10 kΩ), módulo de relé Active LOW compatível com lógica de 5 V, protoboard, jumpers e osciloscópio. Usar carga didática de baixa tensão; os 250 V e 20 A exibidos são valores simulados.

| Pino | Função | Ligação |
|---|---|---|
| A0 | Potenciômetro de tensão | Cursor em A0; extremos em 5 V e GND |
| A1 | Potenciômetro de corrente | Cursor em A1; extremos em 5 V e GND |
| D11 | Botão Active HIGH | Botão entre D11 e 5 V; resistor externo de D11 a GND; `INPUT`, sem pull-up interno |
| D12 | Relé Active LOW | Entrada IN do módulo; LOW liga, HIGH desliga; GND comum |
| D13 | Sinal para osciloscópio | Ponta em D13 e referência em GND: 100 Hz enquanto a aquisição está ativa |

Montar com a alimentação desconectada. Não ligar bobina diretamente ao pino. Conferir a alimentação e o estágio de acionamento do módulo. Se usar um LED como indicação Active LOW, ligar 5 V → resistor de 330 Ω → ânodo do LED, cátodo → D12. Não usar D7/D2: são ligações do firmware anterior.

## Aquisição e conversão

Timer1: `OCR1A = 1249`, prescaler 64, clock de 16 MHz: **200 interrupções/s**, período **5 ms**. D13 alterna a cada interrupção habilitada: período completo de 10 ms, **100 Hz**, não 200 Hz. Ao pausar, D13 fica LOW.

O desktop converte A0/A1 de 0–1023:

```python
tensao = valor_a0 * 250.0 / 1023.0
corrente = valor_a1 * 20.0 / 1023.0
potencia = tensao * corrente
```

Com RELE=0, o desktop apresenta corrente e potência iguais a zero, mesmo que o potenciômetro A1 esteja elevado. Não há RMS: os potenciômetros representam grandezas contínuas simuladas.

`taxa_amostragem_ms` no JSON e o campo **Intervalo de agregação** representam janelas de médias no desktop (100–5000 ms). Não reprogramam Timer1. Há somas, contagem e máximos em memória constante. Mudança do relé fecha a janela anterior para não misturar carga ligada e desligada. Indicadores, gráficos e SQLite usam as mesmas médias e P = média(V) × média(I). Alertas avaliam também os máximos de V, I e da potência instantânea da janela; o histórico de eventos registra o máximo violado. As amostras brutas e os máximos de todas as janelas não são armazenados na tabela `leituras`.

O firmware só guarda a amostra mais recente em variáveis e em `novosDados`. Em velocidades baixas, pode sobrescrever amostras antes de transmiti-las. Um DAT com cerca de 25 bytes a 200 Hz excede a capacidade de 9600/19200/38400 com UART 8N1. **115200 bps é recomendado**; as outras opções continuam disponíveis, mas não garantem a entrega de 200 DAT/s. Picos que o próprio UNO não transmitiu não podem ser recuperados pelo desktop.

## Protocolo e conexão

| Comando/pacote | Significado |
|---|---|
| `!S9600`, `!S19200`, `!S38400`, `!S115200` + LF | Inicia aquisição na velocidade escolhida |
| `>>> ACK: INICIANDO EM 115200 BPS <<<` | ACK textual na velocidade anterior, antes da troca |
| `$DAT,A0,A1,RELE,BOTAO*CS` + CRLF | Dados brutos, estados 0/1 e checksum XOR dos bytes de `DAT,...` |
| `!1` + LF | Liga a carga |
| `!0` + LF | Desliga a carga |
| `!P` + LF | Pausa aquisição e retorna a 9600; preserva o relé |
| `>>> ACK: RELE LIGADO <<<` / `>>> ACK: RELE DESLIGADO <<<` | Recepção do comando; a posição é confirmada somente pelo próximo DAT |
| `>>> ERR: ... <<<` | Erro textual; não substitui DAT |

DAT aceita LF ou CRLF, ADC inteiro na faixa 0–1023, RELE e BOTAO estritamente 0/1. Pacotes inválidos são descartados e contados. O protocolo anterior com `$HS`, `$SYNC`, `$TLM`, `$PING` e `$BYE` não é utilizado.

O desktop abre em 9600 e tolera até 6 s para o boot/reset por DTR. Envia `!S<alvo>`, aguarda o ACK e testa DAT na velocidade alvo mesmo se o ACK se perder. Fragmentos da velocidade anterior são descartados. Se não houver DAT, envia `!P` nas quatro velocidades possíveis para recuperar uma placa que continuou transmitindo; então tenta novamente a partir de 9600 e pode tentar alvos menores. Não depende de retorno automático a 9600. A recuperação pode levar vários segundos e pode ser cancelada pela interface.

O firmware não tem watchdog. `!S9600` durante transmissão em outra velocidade também não força a troca para 9600; a recuperação usa `!P`. Falha de confirmação da atuação, com DAT ativo, mantém a conexão para nova ação manual, sem retransmissão automática. Silêncio de DAT por `max(0,5 s, timeout configurado)` ou erro de porta encerra o worker e mostra **Link perdido**. ACK/ERR não mantêm artificialmente o link ativo. Reconexão é manual, com novo worker e sem religar a carga automaticamente. Se a intenção persistida era ABERTO e DAT reporta FECHADO ao conectar, é mantida uma tentativa segura de corte por conexão; falha dessa tentativa fica visível e não é repetida a cada DAT.

## Botão de proteção e banco

O botão D11 apenas informa seu estado. O `SerialWorker` envia `!0` imediatamente no primeiro HIGH válido, substitui qualquer `!1` pendente e invalida religamentos que aguardavam SQLite. O corte não espera a GUI nem um commit. A liberação exige 50 ms de LOW estável; bounce não gera várias ocorrências. Ao conectar com D11 HIGH, o mesmo tratamento se aplica, inclusive se o relé já estiver aberto.

O desktop tenta auditar a solicitação no SQLite e reconcilia os dois dispositivos depois do DAT. Uma falha real de gravação é informada sem impedir o comando. Só há aviso de **carga desligada** depois de RELE=0 no DAT. Sem telemetria válida, o modelo informa estado desconhecido (`None`) e o badge exibe DESCONHECIDO. A intenção persistida continua separada do estado reportado. RELE em DAT representa a leitura lógica de D12; não comprova o contato mecânico do relé nem a ausência de energia na carga real. Soltar o botão não religa: é necessário solicitar manualmente depois da liberação.

**D11 não é uma proteção elétrica independente.** O corte depende de o UNO transmitir o pacote, da comunicação serial e de o desktop executar `!0`. Perder o cabo com a placa alimentada pode deixar a carga ligada.

Corte e religamento manuais preservam a transação original: UPDATE dos dois dispositivos + INSERT do comando, commit e envio serial. Falha antes do commit faz rollback e nenhum comando manual é enviado. `Ctrl+Shift+F` continua simulando a falha entre UPDATE e INSERT. Esse modo não bloqueia o botão físico. O estado gravado por uma solicitação manual pode divergir temporariamente do estado físico; DAT é a referência da interface.

Limites de tensão, corrente e potência geram alertas visuais e eventos por transição. Os requisitos locais não exigem corte automático por limite. Não apresentar esses alertas como proteção elétrica de hardware.

## Threads e encerramento

São preservadas três threads: GUI (widgets/controladores), SerialWorker (porta, validação, botão e agregação) e DatabaseWorker (SQLite/JSON e consultas). A GUI recebe sinais. A entrega de leituras à GUI mantém até 1000 agregados, com uma notificação pendente. O banco mantém até 10000 leituras na fila, lotes de até 512 e tarefas de controle separadas das perdas de leituras. Saturação gera aviso e contador de descarte; as filas de tarefas sob solicitação do usuário não têm limite fixo. Evitar consultas históricas repetidas durante lentidão persistente de disco.

Na desconexão normal, a serial envia `!P` e fecha a porta; isso preserva o relé, conforme o comportamento documentado e sem requisito local de corte ao sair. Para deixar a carga desligada: solicitar corte manual, aguardar RELE=0 no DAT, depois desconectar. Ao fechar o programa, aguardar a serial, entregar os últimos agregados e drenar o banco antes da destruição das threads. Locks do SQLite podem prolongar o encerramento; não destruir uma QThread ainda em execução.

## Ensaio físico — registrar evidências

1. Preparar backup do SQLite/JSON com o app fechado. Montar a bancada e conferir D11 LOW solto/HIGH pressionado. Conferir D12 Active LOW.
2. Carregar o firmware obrigatório sem modificações. Fechar Monitor Serial e qualquer programa que ocupe a porta.
3. Executar `python main.py`, selecionar a porta real, 115200, timeout de 1000 ms e agregação de 1000 ms. Conectar. Conferir DAT ativo e estado inicial aberto do firmware, sem envio automático de `!1`.
4. Observar D13 no osciloscópio: 100 Hz; confirmar aquisição de 200 Hz/5 ms. Alterar a agregação para 100 ms e 5000 ms em novas conexões: D13 deve continuar em 100 Hz.
5. Variar A0 de mínimo a máximo: aproximadamente 0–250 V. Com carga ligada manualmente e botão solto, variar A1: aproximadamente 0–20 A; potência até 5000 W. Com carga aberta, corrente/potência devem ser zero. Comparar indicadores, gráfico e histórico.
6. Solicitar corte manual e observar commit/evento, comando e RELE=0 antes da confirmação visual. Religar manualmente. Repetir com modo de falha simulada: rollback, estados SQL anteriores preservados, sem comando enviado.
7. Com carga ligada, pressionar D11: corte confirmado por DAT, uma ocorrência por pressão, religamento bloqueado enquanto retido. Soltar: não deve religar. Pedir novo religamento manual.
8. Repetir com D11 já pressionado antes de conectar; testar com relé aberto e, mantendo alimentação separada, com estado ligado anterior. Não deve haver religamento automático.
9. Pressionar D11 durante uma solicitação de religamento e durante uma operação SQLite lenta. Conferir prioridade de `!0` e cancelamento do religamento antigo, inclusive depois de soltar D11.
10. Em banco descartável, induzir falha real de escrita. Pressionar D11: comando e DAT devem funcionar, aviso de persistência deve aparecer. Não corromper o banco usado na apresentação.
11. Gerar picos curtos de potenciômetro: média pode suavizar o pico, mas evento deve mostrar o máximo recebido. Observar que limitação de transmissão do UNO pode perder picos antes do desktop.
12. Desconectar manualmente com relé ligado: `!P` preserva carga. Reconectar e conferir o estado real. Repetir deixando a carga desligada antes da desconexão.
13. Remover USB durante telemetria e durante atuação. Deve haver Link perdido, nenhum aviso falso de corte e liberação da porta. Se USB for a única alimentação, isso também desliga o UNO e não prova comportamento com placa alimentada.
14. Recolocar cabo, atualizar portas, conectar manualmente. Com alimentação separada e cliente de ensaio, deixar a placa transmitindo em cada velocidade, fechar o cliente e reconectar: validar recuperação por `!P` sem watchdog.
15. Ensaiar 9600, 19200 e 38400, declarando a menor vazão efetiva. Voltar a 115200. Fechar o app, confirmar porta liberada, fila gravada e ausência de QThread destruída em execução.

| Evidência | Resultado | Data/operador/arquivo |
|---|---|---|
| Ligações D11/D12 e D13 a 100 Hz | Pendente | — |
| Calibração e corrente zero com relé aberto | Pendente | — |
| Conexão nas quatro velocidades e boot por DTR | Pendente | — |
| Botão inicial, retido, bounce e durante religamento | Pendente | — |
| Falha real no SQLite e rollback manual | Pendente | — |
| Cabo, perda de DAT e recuperação sem watchdog | Pendente | — |
| Encerramento e liberação da porta | Pendente | — |

## Plano B — placa virtual

O simulador de pseudoterminal requer Linux/macOS (ou app e simulador juntos no WSL com GUI preparada). Os testes em memória também funcionam no Windows.

```bash
python tools/simulador_placa.py
python main.py
```

Digitar a porta impressa pelo simulador no campo editável do app. No terminal do simulador: `d` pressiona D11, `l` libera, `x` remove o cabo e `q` encerra. A placa virtual não corta sozinha ao pressionar `d`: quem envia `!0` é o desktop.

```bash
python tools/simulador_placa.py --falhar-inicio 115200
```

Esse modo recusa o início em 115200 para exercitar alvos menores. PTY não impõe baud elétrico; simulação não valida USB, reset por DTR, ADC, osciloscópio, alimentação ou relé físico. Não preencher a tabela de ensaio físico com resultados de simulação.

## Integração da main — estados de confirmação (PR #80)

Mantidos `SituacaoDisjuntor`, `ConfirmacaoDisjuntor`, avisos de operação, status de boot e distinção de simulação do PR #79. A tela distingue solicitação salva, comando PENDENTE, DAT CONFIRMADO e FALHA de confirmação. ACK correto seguido de DAT contraditório falha a operação; ACK ausente com DAT correto pode confirmar. Uma amostra capturada pelo ISR antes do comando, transmitida depois do ACK, não é usada para decidir a atuação.

O corte físico é a exceção à precedência de persistência: o worker envia !0 primeiro, cancela !1 pendente e a auditoria é tentada depois. Testar também HIGH com religamento ainda no SQLite e pressão/liberação enquanto a confirmação modal está aberta. Não reaproveitar o aceite antigo para religar.

O simulador preserva `b` (pulso D11), `--falhar-em` (sem DAT em um baud) e os acréscimos da branch: `d` (manter D11), `l` (liberar), `--falhar-inicio` (recusar início). Há parser de comandos e banner do firmware, modos de perda de comando/ACK e ACK sem atuação para os testes. Nenhum desses modos altera o firmware obrigatório.
