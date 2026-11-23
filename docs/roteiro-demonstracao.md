# Seminário SmartGrid — integração final com Arduino UNO

Revisão: 23/11/2026. Tempo alvo: **18 minutos**, com 2 minutos de margem. Cada integrante tem 4 min 30 s de fala/execução. A arguição ocorre depois.

| Integrante | Responsabilidade |
|---|---|
| Samuel De Marco | Objetivo, arquitetura, Dashboard e comandos manuais |
| Davy Josias Scheuermann | Protocolo, conexão, perda de link e contingência |
| Lucas Zamoner Locatelli | Limites de alerta, configuração e testes |
| Arthur De Marco Faggion | Histórico, bancada, botão e integridade transacional |

## Preparação

1. Montar a bancada conforme [bancada.md](bancada.md): A0 tensão, A1 corrente, D11 Active HIGH/pull-down externo, D12 relé Active LOW e D13 no osciloscópio.
2. Carregar o firmware obrigatório sem alterações e fechar Monitor Serial. Testar previamente as quatro velocidades e usar 115200 no seminário.
3. Usar um checkout identificado, dependências instaladas e backup do SQLite/JSON com o app fechado. Registrar separadamente a alteração do firmware já existente na área de trabalho. Não fazer commit/push sem revisão da equipe.
4. Deixar o modo de falha simulada desligado inicialmente; `Ctrl+Shift+F` alterna e mostra o selo. Preparar um banco descartável para falhas reais de escrita.
5. Preparar eventos de tipos/datas/dispositivos diferentes, identificando dados sintéticos. Não limpar o histórico usado como evidência.
6. Executar testes automatizados e preencher os ensaios físicos apenas depois de realizados. Os testes em memória e os de GUI offscreen não substituem Arduino e osciloscópio.
7. Preparar o Plano B em Linux/macOS ou WSL com app e simulador no mesmo ambiente; ter uma gravação de contingência identificada.

## Ordem e divisão do tempo

| Tempo | Quem | Demonstração e resultado esperado |
|---|---|---|
| 00:00–01:00 | Samuel | Supervisão didática com potenciômetros e auditoria persistente |
| 01:00–02:30 | Samuel | MVC e três threads; GUI não faz serial/SQLite |
| 02:30–04:30 | Davy | !S, ACK textual e DAT; boot/reset e velocidade confirmada |
| 04:30–05:30 | Samuel | Médias, P=V×I e gráfico; Timer1 200 Hz e D13 100 Hz |
| 05:30–07:30 | Arthur | Histórico por tipo/data/dispositivo, consulta de medições e atualização após commit |
| 07:30–10:00 | Lucas | Limites, validação, alertas de picos, persistência e setpoint |
| 10:00–11:00 | Samuel | Corte e religamento manuais, commit e confirmação DAT |
| 11:00–12:00 | Arthur | Botão D11, prioridade do corte, liberação sem religamento automático |
| 12:00–13:30 | Davy | Falha USB, estado desconhecido e reconexão manual |
| 13:30–15:00 | Lucas | Recriação de JSON e padrões, com restauração do backup |
| 15:00–16:00 | Arthur | Falha simulada: rollback sem comando enviado |
| 16:00–17:00 | Davy | Plano B com !S/DAT; limites da simulação |
| 17:00–17:30 | Lucas | Testes aprovados e ensaios físicos pendentes |
| 17:30–18:00 | Arthur | Carga em estado combinado, pausa e liberação dos recursos |

## Falas e passos observáveis

### Arquitetura

“A View desenha e publica ações. Os controllers coordenam. Models guardam protocolo, regras e repositórios. A GUI recebe sinais; SerialWorker atende a porta e o botão, e DatabaseWorker grava SQLite/JSON e executa consultas. São três threads na aplicação. Somente o simulador externo possui seu próprio laço de emulação.”

### Conexão e telemetria

1. Atualizar portas e selecionar a porta real. Escolher 115200, timeout de 1000 ms e agregação de 1000 ms.
2. Narrar: “Abrimos em 9600, tolerando o boot do UNO. Enviamos `!S115200` com LF. O ACK textual sai na velocidade anterior; DAT válido confirma a nova velocidade. Se o ACK se perder, ainda testamos DAT.”
3. Mostrar a velocidade negociada e o relé inicialmente aberto, conforme o firmware. Não há religamento automático ao conectar.
4. Explicar a recuperação: “Sem DAT, enviamos `!P` nas possíveis velocidades e reiniciamos em 9600. Não há SYNC, PING ou watchdog no firmware.” Mostrar registros das quatro opções; não gastar a apresentação repetindo todas.
5. Religar manualmente com o botão solto. Variar potenciômetros: A0 × 250/1023 V, A1 × 20/1023 A, P=V×I. Relé aberto implica corrente/potência zero no desktop.
6. No osciloscópio: Timer1 interrompe a 200 Hz (5 ms) e D13 alterna a 100 Hz (10 ms por ciclo). O campo de agregação muda só as médias do desktop. Outras velocidades podem perder amostras por falta de vazão serial.

### Histórico e limites

Consultar eventos por tipo, data e dispositivo e medições por últimas N/intervalo. Comparar os pontos agregados do gráfico com o SQLite. O histórico recebe eventos persistidos após commit.

Alterar limites de tensão/corrente/potência. Testar entrada vazia/inválida, cancelar e confirmar. Mostrar que o setpoint de potência acompanha a regra ativa. Ultrapassar um limite: alerta visual e uma ocorrência por transição, sem corte automático. Máximos recebidos na janela evitam que a média esconda um pico nos alertas; amostras perdidas pelo UNO não podem ser reconstruídas.

### Comandos e botão

1. Confirmar corte manual. O SQLite grava solicitação e estados na mesma transação; depois sai `!0`. O badge muda após RELE=0 em DAT, não pelo ACK ou commit. Religar com nova confirmação.
2. Com carga ligada, pressionar D11. Mostrar corte, ocorrência e bloqueio de religamento enquanto retido. Soltar e demonstrar que a carga permanece aberta; então pedir novo religamento manual.
3. Explicar: “O primeiro HIGH solicita corte imediatamente na thread serial. A liberação usa debounce de 50 ms. Pressão invalida um religamento que estava esperando o banco. O corte físico não depende do commit.”
4. Mostrar evidências preparadas para botão pressionado antes da conexão e durante religamento. Em banco descartável, testar falha real de escrita: !0 continua saindo, o DAT confirma e a falha de registro é informada.
5. Declarar: **o botão depende do desktop e da serial; não é proteção elétrica independente.**

### Falha USB e recuperação

Remover o USB: Link perdido e estado **SEM CONFIRMAÇÃO**, preservando o último conhecido. Não afirmar carga desligada. Se USB era a única alimentação, também houve desligamento da placa; isso não simula perda de comunicação com placa alimentada.

Reconectar cabo, esperar enumeração e término do worker anterior, atualizar portas e conectar manualmente. O estado vem do DAT. O desktop não religa automaticamente. Para ensaiar placa ainda transmitindo, usar alimentação separada e o procedimento da bancada; não esperar retorno espontâneo a 9600.

### JSON e falha simulada

Com app fechado e backup pronto, renomear `data/config.json` para um nome de backup não existente, iniciar e mostrar padrões: COM1, 115200, timeout 1000 ms, agregação 1000 ms, limites iniciais 15 A/230 V/3500 W e falha simulada false. Selecionar a porta real, encerrar e restaurar o arquivo original. Não apagar o banco: regras existentes continuam vindo do SQLite.

Ligar `Ctrl+Shift+F` e solicitar um corte manual. Conferir rollback por SQL: os dois estados e a quantidade de eventos do comando permanecem anteriores; nenhum !0 manual é enviado. Desligar o modo e repetir. A falha simulada aplica-se à operação manual, nunca impede o botão D11.

### Plano B e encerramento

```bash
python tools/simulador_placa.py
python main.py
```

Usar a porta impressa. `d` pressiona, `l` libera, `x` remove cabo e `q` encerra. `--falhar-inicio 115200` ensaia alvo alternativo. A simulação não comprova vazão física, DTR, ADC, D13 ou acionamento elétrico.

Ao terminar, solicitar corte e aguardar DAT se o combinado for deixar a carga desligada. Desconectar envia `!P`, que apenas pausa e retorna a 9600. Fechar o app deve liberar porta e drenar a fila SQLite sem destruir threads ativas.

## Arguição — respostas curtas

| Pergunta | Resposta |
|---|---|
| Por que três threads? | GUI processa interação; serial espera bytes; banco espera disco/locks. Sinais devolvem resultados à GUI. |
| O intervalo muda Timer1? | Não. Aquisição fixa 200 Hz/5 ms; D13 100 Hz; intervalo só agrega no desktop. |
| O ACK confirma desligamento? | Não. Somente RELE=0 em DAT confirma o estado reportado pela placa. |
| Banco falhou com botão pressionado? | !0 já foi solicitado pela serial. O erro de persistência é informado; o estado depende de DAT. |
| Rollback desfaz efeito físico? | Não. Nas operações manuais, falha antes do commit impede envio. Depois do envio, só telemetria confirma o efeito. |
| Limite ultrapassado desliga? | Requisitos locais pedem alerta e auditoria; não há corte automático por limite. |
| Soltar D11 religa? | Não. Exige LOW estável e novo pedido manual. |
| Perder o cabo desliga? | Não é garantido. O firmware não tem watchdog; com alimentação própria, o relé pode permanecer ligado. |
| O checksum garante tudo? | XOR detecta parte das corrupções; não autentica e não fornece segurança elétrica. |
| O histórico guarda 200 amostras/s? | Guarda médias por janela e P=média(V)×média(I); eventos de limite incluem o máximo recebido. |
| Há filas ilimitadas de medições? | Não. Agregados para GUI e leituras para SQLite têm limites e avisos de descarte. Tarefas de controle continuam preservadas. |

## Checklist da apresentação

- [ ] Versão e resultados automatizados identificados no relatório final.
- [ ] Ligações D11/D12 e D13 confirmadas por ensaio físico.
- [ ] 200 Hz, 5 ms e 100 Hz explicados corretamente.
- [ ] Botão inicial, durante religamento e falha SQLite ensaiados.
- [ ] Relé confirmado por DAT, sem promessa de proteção independente.
- [ ] Rollback manual/falha simulada demonstrados e modo restaurado.
- [ ] Backup preservado, banco demonstrativo identificado e porta correta selecionada.
- [ ] Ensaio físico distinguido de testes em memória e GUI offscreen.
- [ ] Estado final da carga conferido antes de pausar e liberar a porta.
