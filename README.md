# SmartGrid — Supervisório de Energia (IHM Desktop)

Software desktop que atua como uma **Interface Homem-Máquina (IHM) de supervisão energética**, responsável por processar os sinais analógicos e digitais recebidos de um microcontrolador e permitir a intervenção do operador em tempo real.

Projeto da disciplina **Desenvolvimento de Aplicações Computacionais** — Unoesc.

---

## Sumário

- [Equipe](#equipe)
- [Visão Geral](#visão-geral)
- [Escopo das Entregas](#escopo-das-entregas)
- [Entrega A1/1 — Requisitos Obrigatórios](#entrega-a11--requisitos-obrigatórios)
- [Entrega Final (Dezembro) — Requisitos Funcionais](#entrega-final-dezembro--requisitos-funcionais)
  - [1. Processamento e Apresentação de Telemetria](#1-processamento-e-apresentação-de-telemetria-hardware--desktop)
  - [2. Monitoramento de Estado Binário e Segurança](#2-monitoramento-de-estado-binário-e-segurança-hardware--desktop)
  - [3. Atuação e Parametrização do Sistema](#3-atuação-e-parametrização-do-sistema-desktop--hardware)
  - [4. Gestão de Histórico e Auditoria](#4-gestão-de-histórico-e-auditoria-logs-em-tabela)
- [Arquitetura e Organização do Código](#arquitetura-e-organização-do-código)
- [Organização do Trabalho em Equipe](#organização-do-trabalho-em-equipe)
- [Tecnologias](#tecnologias)
- [Como Executar](#como-executar)
- [Checklist da Entrega A1/1](#checklist-da-entrega-a11)
- [Forma de Entrega e Apresentação](#forma-de-entrega-e-apresentação-a11)
- [Regras Obrigatórias do Git](#regras-obrigatórias-do-git)
- [Critérios Avaliativos](#critérios-avaliativos)
- [Opção de Tema Livre](#-opção-de-tema-livre-prototipagem-de-microcontroladores)

---

## Equipe

| Integrante | Usuário GitHub | Função |
|---|---|---|
| Samuel De Marco | [@SamuelDeMarco-Dev](https://github.com/SamuelDeMarco-Dev) | Arquitetura MVC, janela principal e Dashboard de Monitoramento |
| Davy Josias Scheuermann | [@Davyjosias01](https://github.com/Davyjosias01) | Painel de Configuração da Comunicação Serial |
| Lucas Zamoner Locatelli | [@lucaszlocatelli](https://github.com/lucaszlocatelli) | Diálogo Modal de Limites e Parâmetros |
| Arthur De Marco Faggion | [@ArthurDeMarcoF](https://github.com/ArthurDeMarcoF) | Histórico de Eventos e Registros |

> ⚠️ Todos os integrantes devem possuir commits registrados em seu próprio usuário do GitHub. Ver [Regras Obrigatórias do Git](#regras-obrigatórias-do-git).

**Repositório:** https://github.com/SamuelDeMarco-Dev/SmartGrid

---

## Visão Geral

O software supervisor recebe continuamente as amostras brutas dos sensores do microcontrolador — via comunicação serial no futuro, ou **simuladas nesta entrega** — e:

1. Processa e apresenta a telemetria elétrica (tensão, corrente e potência);
2. Monitora o estado binário do disjuntor/chave de proteção;
3. Permite atuação sobre a carga (relé) e parametrização de limites de alerta;
4. Centraliza todo o histórico de eventos em uma tabela de auditoria.

```
┌──────────────────┐        Serial (Unidade 4)        ┌──────────────────────┐
│  Microcontrolador│  ──────  V_RMS, I_RMS, estado ──▶ │                      │
│  (ACS712 /       │                                   │  SmartGrid Desktop   │
│   ZMPT101B /     │  ◀──────  RELAY_OFF, setpoints ── │  (IHM Supervisória)  │
│   Relé / Disj.)  │                                   │                      │
└──────────────────┘                                   └──────────────────────┘
```

---

## Escopo das Entregas

| Entrega | Data | Escopo |
|---|---|---|
| **A1/1** | 17/08/2026 | Arquitetura MVC, navegação entre janelas, layout responsivo e interface gráfica completos. **Sem conexão física com o hardware.** |
| **Final** | Dezembro | Integração serial real, processamento de telemetria, atuação sobre o relé, proteção por setpoint e auditoria completa. |

> Na entrega A1/1 a aplicação **não precisa estar fisicamente conectada ao hardware** — a integração serial ocorrerá na Unidade 4. No entanto, toda a arquitetura de código em camadas (MVC), navegação entre janelas, layout responsivo e interface gráfica devem estar prontos.

---

## Entrega A1/1 — Requisitos Obrigatórios

### 1. Dashboard Principal de Monitoramento (Smart Grid)

- **Leituras de Telemetria:** indicadores visuais para Tensão (V), Corrente (A) e Potência Calculada (W).
- **Indicador de Estado Binário:** sinalizador visual (LED virtual ou *badge*) indicando o status do Disjuntor/Chave de Proteção (**Aberto / Fechado**).
- **Comandos de Acionamento:** botão de **"Corte Emergencial de Carga"** (relé), confirmação por `QMessageBox` e registro do comando no histórico.
- **Configuração de Limites:** acesso ao diálogo modal de limites, com regras de alerta aplicadas ao dashboard por meio do barramento.
- **Gráfico de Tendência (com carga inicial de dados):** área de plotagem (`pyqtgraph`) exibindo a curva de demanda das últimas 24 horas, **preenchida com dados amostrais no momento em que a tela abre**.

### 2. Painel de Configuração da Comunicação Serial

- Seleção de **Porta COM** (`QComboBox`);
- **Baud Rate** (9600, 115200);
- **Timeout**;
- Botões de **Conectar / Desconectar**.
- Atualização do status visual da conexão e publicação do estado no barramento.

> Nesta etapa A1/1, a tela valida e simula a abertura da porta para integração visual. A comunicação real com o microcontrolador fica para a entrega final.

### 3. Janela de Configuração de Limites / Parâmetros (`QDialog` Modal)

- Formulário secundário para cadastro de **regras de alerta** (ex.: limite máximo de corrente, limite máximo de tensão);
- **No mínimo duas regras**;
- Envio e resgate correto de parâmetros para a tela principal;
- Validações com `QMessageBox` e persistência em memória durante a execução.

### 4. Histórico de Eventos e Registros (`QTableWidget`)

- Tabela para listagem de eventos do sistema, incluindo:
  - registros de cortes de emergência;
  - ultrapassagem de limite de corrente;
  - trocas de estado do disjuntor.
- Filtros por tipo de evento e data inicial;
- Limpeza do histórico com confirmação;
- Eventos recebidos em tempo real pelo barramento.

---

## Entrega Final (Dezembro) — Requisitos Funcionais

### 1. Processamento e Apresentação de Telemetria (Hardware → Desktop)

O software recebe continuamente as amostras brutas dos sensores do microcontrolador.

**Leitura e Tratamento de Tensão (V) e Corrente (A)**

- Ler a **tensão eficaz (V<sub>RMS</sub>)** e a **corrente (I<sub>RMS</sub>)** enviadas pelo sensor (ex.: **ACS712** para corrente / **ZMPT101B** para tensão).

**Cálculo da Potência Ativa (P)**

- Calcular a potência instantânea aproximada em **Watts (W)** ou **Quilowatts (kW)** através da relação:

```
P = V × I
```

**Renderização no Dashboard**

- Exibir os valores instantâneos de **V**, **I** e **P** em indicadores numéricos de alta visibilidade (`QLCDNumber` ou `QLabel` customizadas com destaque de cor);
- Atualizar o **Gráfico de Tendência Temporal** (`pyqtgraph`) plotando a curva de consumo de potência ao longo do tempo. O gráfico deve ser inicializado com **histórico pré-carregado** no momento da abertura da tela.

### 2. Monitoramento de Estado Binário e Segurança (Hardware → Desktop)

O software monitora o estado físico do **disjuntor/chave de proteção geral** da instalação.

**Sinalização Visual de Estado (Aberto / Fechado)**

| Estado | Significado | Sinalização |
|---|---|---|
| **Fechado** | Energizado / Normal | Indicador **verde** — *"Disjuntor: FECHADO / NORMAL"* |
| **Aberto** | Desenergizado / Trip / Falha | Indicador **vermelho** — *"Disjuntor: ABERTO / PROTEÇÃO ATIVADA"* |

**Detecção e Registro de Eventos de Disparo**

- Sempre que o estado mudar **espontaneamente** no hardware (ex.: disjuntor caiu por sobrecarga), o software deve:
  - disparar um aviso visual (`QMessageBox.warning`);
  - gerar um **registro automático** na tabela de histórico.

### 3. Atuação e Parametrização do Sistema (Desktop → Hardware)

**Corte Emergencial da Carga (Relé de Saída)**

- Botão de grande destaque: **"CORTE DE EMERGÊNCIA"** / **"DESLIGAR CARGA"**;
- Ao ser acionado, exibe uma **caixa de confirmação modal** (`QMessageBox.question`);
- Se confirmado, envia o comando binário (ex.: `RELAY_OFF`) para o microcontrolador abrir o relé.

**Ajuste de Limite de Alerta de Consumo (Setpoints)**

- O operador define um **limite máximo tolerável** de corrente ou potência através de campo numérico (`QDoubleSpinBox`) no diálogo de limites.

**Lógica Local de Proteção de Software**

- Se a potência calculada ultrapassar o **setpoint** configurado pelo usuário, o software deve:
  - alterar a cor do dashboard para **amarelo/vermelho**;
  - emitir um **alerta visual**;
  - **registrar a ocorrência de sobrecorrente**.

### 4. Gestão de Histórico e Auditoria (Logs em Tabela)

Toda a atividade do sistema é centralizada na `QTableWidget` de registros:

| Coluna | Descrição do Dado | Exemplo de Preenchimento |
|---|---|---|
| **Data / Hora** | Carimbo de data/hora exato do evento (*timestamp*) | `10/08/2026 10:30:15` |
| **Tipo de Evento** | Categoria do evento registrado | `Comando`, `Alerta` ou `Status` |
| **Descrição** | Detalhes do evento ou valor medido na ocorrência | `Corte de emergência acionado via software` |
| **Valor Medido** | Potência/Corrente no momento do evento | `12.5 A / 2750 W` |

---

## Arquitetura e Organização do Código

### Padrão MVC — Separação Estrita de Pastas

```
SmartGrid/
├── main.py                        # Inicializa QApplication, estilo, aquisição e telas
├── requirements.txt
├── controllers/                   # Lógica das janelas e eventos (Signals & Slots)
│   ├── __init__.py
│   ├── dashboard_controller.py
│   ├── historico_controller.py
│   ├── limites_dialog_controller.py
│   ├── main_window_controller.py
│   └── serial_config_controller.py
├── models/                        # Regras de negócio, entidades e dados
│   ├── __init__.py
│   ├── aquisicao.py               # Serviço que publica telemetria simulada
│   ├── barramento.py              # Canal de sinais compartilhado entre telas
│   ├── estado_disjuntor.py
│   ├── evento.py
│   ├── leitura.py
│   ├── porta_serial.py            # Validação e simulação da porta serial
│   ├── regra_alerta.py
│   ├── repositorio_eventos.py
│   ├── repositorio_regras.py
│   └── simulador_telemetria.py
├── ui/                            # Arquivos .ui e versões geradas pelo pyuic6
│   ├── __init__.py
│   ├── dashboard.ui
│   ├── dashboard_ui.py
│   ├── estilo.qss                 # Folha de estilo global
│   ├── historico.ui
│   ├── historico_ui.py
│   ├── limites_dialog.ui
│   ├── limites_dialog_ui.py
│   ├── main_window.ui
│   ├── main_window_ui.py
│   ├── serial_config.ui
│   └── serial_config_ui.py
└── README.md
```

### Regras de Encapsulamento

- **Nenhuma regra de negócio ou evento** deve ser escrita diretamente dentro dos arquivos gerados pelo Qt Designer;
- Os arquivos `_ui.py` em `/ui` permanecem **completamente isolados** — são descrições de layout geradas pelo `pyuic6`;
- Os arquivos `.ui` são mantidos como fonte editável do Qt Designer, e a folha `ui/estilo.qss` centraliza a aparência global;
- Toda lógica de interação, validação visual e conexão de sinais vive em `/controllers`;
- As regras de negócio, entidades, repositórios em memória, simulação de telemetria e serial ficam em `/models`;
- `main.py` permanece como ponto de entrada e composição: cria a aplicação, aplica o estilo, inicia o serviço de aquisição, monta a janela principal e registra as telas no shell.

### Comunicação entre as Telas — Barramento de Eventos

Nenhum controller importa outro controller. As telas conversam por sinais publicados em `models/barramento.py`, o que as mantém independentes e permite o desenvolvimento em paralelo:

| Sinal | Emitido por | Consumido por |
|---|---|---|
| `leitura_recebida(Leitura)` | Serviço de aquisição | Dashboard (indicadores e gráfico) |
| `evento_registrado(Evento)` | Qualquer tela | Histórico de Eventos |
| `estado_disjuntor_alterado(EstadoDisjuntor)` | Dashboard / aquisição | Dashboard (badge e comandos) |
| `regras_alteradas(list[RegraAlerta])` | Diálogo de Limites | Dashboard (proteção por setpoint) |
| `status_conexao_alterado(bool, str)` | Painel Serial | Janela principal, Dashboard e serviço de aquisição |

```python
from models.barramento import barramento
from models.evento import TipoEvento

barramento.evento_registrado.connect(self._adicionar_linha)
barramento.registrar_evento(TipoEvento.COMANDO, "Corte de emergência acionado", "12.50 A / 2750 W")
```

A janela principal expõe `registrar_pagina(nome, widget)` e `ir_para(nome)`. O `main.py` usa esses métodos para encaixar `DashboardController`, `SerialConfigController` e `HistoricoController` no `QStackedWidget` do shell.

### Compilação das Telas do Qt Designer

Após editar um `.ui`, recompile e **versione os dois arquivos** (`.ui` e `_ui.py`):

```bash
pyuic6 -o ui/nome_da_tela_ui.py ui/nome_da_tela.ui
```

---

## Organização do Trabalho em Equipe

O trabalho está dividido em **milestones e issues no GitHub**, com propriedade exclusiva de arquivos: cada tela é dona do seu `.ui`, do seu controller e dos seus models específicos. Assim ninguém edita o arquivo de ninguém e não há conflito de merge.

| Milestone | Conteúdo |
|---|---|
| **M0 — Fundação MVC & Contratos** ✅ | Estrutura de pastas, `main.py`, shell da janela principal e entidades compartilhadas em `/models` |
| **M1 — Entrega A1/1: Telas** | As 4 telas obrigatórias, desenvolvidas em paralelo — uma por integrante |
| **M2 — Integração & Entrega** | Fiação das telas no shell, padronização visual, checklist de entrega |

Os contratos de `/models` entregues no M0 estão **congelados**: qualquer campo novo passa por comentário na issue correspondente e PR específico.

### Fluxo de trabalho

```bash
git switch main && git pull            # sempre partir do main atualizado
git switch -c feat/minha-tela          # branch própria
# ... commits pequenos e frequentes ...
git pull --rebase origin main          # antes de abrir o PR
```

- Ninguém commita direto em `main` — toda entrega passa por **Pull Request**.
- Mensagens no padrão `tipo(escopo): descrição`, ex.: `feat(dashboard): pré-carrega gráfico com histórico de 24h`.
- Commits distribuídos ao longo do período. Ver [Regras Obrigatórias do Git](#regras-obrigatórias-do-git).

---

## Tecnologias

| Item | Uso |
|---|---|
| **Python 3** | Linguagem base |
| **PyQt6** | Framework da interface gráfica |
| **Qt Designer** | Construção visual das telas (`.ui`) com uso de *Layouts* |
| **pyqtgraph** | Gráfico de tendência de consumo |
| **numpy** | Apoio ao processamento e manipulação de dados numéricos |
| **pyserial** | Comunicação serial com o microcontrolador *(Unidade 4)* |

### Componentes Qt Utilizados

`QLCDNumber` · `QLabel` · `QComboBox` · `QSpinBox` / `QDoubleSpinBox` · `QPushButton` · `QTableWidget` · `QDateEdit` · `QDialog` (modal) · `QMessageBox` (`question` / `warning`)

---

## Como Executar

```powershell
git clone https://github.com/SamuelDeMarco-Dev/SmartGrid.git
cd SmartGrid
```

No Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Com o ambiente virtual ativo:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Se a política de execução bloquear a ativação do ambiente virtual, use diretamente o Python da `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

No Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

> Execute sempre a partir da raiz do projeto (`SmartGrid/`) — é de lá que os pacotes `ui`, `controllers` e `models` são importados.

**Requisitos:** Python 3.10 ou superior. As dependências documentadas são exatamente as de `requirements.txt`: `PyQt6`, `pyqtgraph`, `numpy` e `pyserial`.

---

## Checklist da Entrega A1/1

- [x] Critério 1 — Arquitetura MVC e separação de responsabilidades
- [x] Critério 2 — Interface gráfica, layouts e navegação
- [x] Critério 3 — Telemetria, disjuntor e gráfico pré-carregado
- [x] Critério 4 — QDialog, QTableWidget, QComboBox, QDateEdit e QMessageBox
- [x] Critério 5 — README, Git e organização do trabalho

---

## Forma de Entrega e Apresentação (A1/1)

- **Envio do Projeto:** submissão do link do repositório GitHub no Google Forms até **17/08/2026**.
- **Apresentação em Sala:** no dia **17/08/2026**, no laboratório, as equipes farão uma **demonstração prática executando o código clonado do próprio GitHub**.

---

## Regras Obrigatórias do Git

> **Leia com atenção.**

- **Evolução do Código:** os commits devem demonstrar a **evolução progressiva** do projeto ao longo do período de desenvolvimento. **Enviar todo o código em um único commit no dia da entrega não será aceito.**
- **Avaliação Individual:** a participação de cada membro é **auditada pelo histórico do Git**. O aluno que **NÃO** possuir commits registrados em seu usuário do GitHub receberá **nota ZERO** na entrega.
- **Padrão de Mensagens:** manter um padrão coerente nas mensagens de commit.

---

## Critérios Avaliativos

| # | Categoria | Critério Detalhado | Nota Máx. |
|---|---|---|---|
| **1** | **Arquitetura de Software & Padrão MVC** | • Separação estrita de pastas (`/ui`, `/controllers`, `/models`, `main.py`)<br>• Isolação completa dos arquivos compilados pelo Qt Designer (nenhuma regra de negócio dentro da pasta `/ui`)<br>• `main.py` enxuto, funcionando apenas como ponto de partida da aplicação<br>• Organização de classes, boas práticas de POO e legibilidade do código em Python | **2,5 pts** |
| **2** | **Interface Gráfica (UI/UX) & Layouts Responsivos** | • Utilização de *Layouts* no Qt Designer para alinhamento dos componentes (evitando elementos sobrepostos ou desalinhados)<br>• Organização visual clara, boa distribuição dos elementos e hierarquia das informações (títulos, campos e botões bem definidos)<br>• Padronização estética (cores, fontes) e facilidade na navegação entre as janelas | **2,0 pts** |
| **3** | **Módulo de Telemetria & Gráfico Pré-carregado (Smart Grid)** | • Presença e funcionamento dos indicadores visuais para Tensão (V), Corrente (A) e Potência Calculada (P = V × I)<br>• Indicador gráfico de status do disjuntor (Aberto / Fechado) com mudança visual de cor/estado<br>• Área de gráfico (PyQtGraph ou Matplotlib) inicializada com dados pré-carregados simulando a curva de consumo | **2,0 pts** |
| **4** | **Componentes Avançados & Múltiplas Janelas** | • Janela secundária de configuração (`QDialog` modal) com envio e resgate correto de parâmetros para a tela principal<br>• Uso funcional e preenchimento correto de `QTableWidget` (histórico de eventos), `QComboBox`, `QDateEdit` e validações com `QMessageBox` | **2,0 pts** |
| **5** | **Governança, Versionamento & Auditoria Git** | • Repositório ativo e bem estruturado no GitHub/GitLab com arquivo `README.md` identificando a equipe e o projeto<br>• Distribuição equitativa de commits entre todos os integrantes da equipe (auditado pelo histórico do Git)<br>• Padrão coerente nas mensagens de commit | **1,5 pt** |
| | **NOTA TOTAL** | | **10,0 pts** |

---

## 💡 Opção de Tema Livre (Prototipagem de Microcontroladores)

As equipes que preferirem podem aplicar o software supervisor a um **protótipo físico/projeto desenvolvido em disciplinas como Microcontroladores, Microprocessadores ou Sistemas Embarcados**, desde que o projeto atenda aos **mesmos requisitos** de leitura analógica/digital e acionamento de atuadores.
