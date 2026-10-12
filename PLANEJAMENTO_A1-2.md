# Planejamento A1/2 — Persistência, Logs e Robustez

> Documento de planejamento (não versionar na entrega final). Datas dos milestones são **sugestões**: ajustar conforme o prazo real da A1/2.

---

## 1. Diagnóstico do ponto de partida (A1/1)

| Hoje (em memória) | Vira (A1/2) |
|---|---|
| `models/repositorio_eventos.py` — lista em memória com eventos amostrais | SQLite, tabela `eventos`, `listar_todos()` |
| `models/repositorio_regras.py` — regras em memória (`repositorio_regras_compartilhado`) | SQLite, tabela `regras_limites` |
| Estado do disjuntor só em `Dashboard` / `barramento.estado_disjuntor_alterado` | SQLite, tabela `dispositivos` |
| Porta/baud/timeout só nos widgets da tela serial | `data/config.json` |
| Nenhum log | `logs/smartgrid.log` (módulo `logging`) |
| `HistoricoController` insere no repositório ao ouvir o barramento | Gravação centralizada e transacional; tabela só reflete o que já foi persistido |
| Sem pasta `database/`, `data/`, `logs/`; `.gitignore` sem `*.db*` | Estrutura MVC exigida + `.gitignore` completo |

Pontos do código que serão tocados: `controllers/dashboard_controller.py:332` (`alternar_carga` — corte de emergência), `:200` (`_avaliar_protecao` — alertas), `controllers/historico_controller.py:32/66/85`, `controllers/limites_dialog_controller.py:22/142`, `controllers/serial_config_controller.py:60` (`conectar`).

---

## 2. Decisões de arquitetura (valem para todos)

1. **Esquema único** definido pela M0 em `database/schema.py` (DDL em constante). Ninguém altera tabela de outro sem comentar na issue de contratos.
2. **`Database`** (`database/connection.py`):
   - `Database.init_db()` cria `data/smartgrid.db` e as tabelas (`CREATE TABLE IF NOT EXISTS`) e insere as regras padrão se a tabela estiver vazia.
   - `Database.conectar()` abre conexão com `timeout=`, `PRAGMA foreign_keys = ON;`, `row_factory = sqlite3.Row`.
   - `Database.transacao()` — *context manager*: `BEGIN` → `yield conn` → `COMMIT`; em exceção faz `ROLLBACK`, loga `[ERROR] ... Rollback executado: <motivo>` e relança.
3. **Repositórios participam de transação**: todo método que escreve aceita `conn: sqlite3.Connection | None = None`. Se vier `conn`, usa-o (e não faz commit); se não vier, abre a própria transação. É isso que permite o corte de emergência ser atômico (UPDATE do dispositivo + INSERT do evento na **mesma** transação).
4. **Fluxo de eventos**: o `barramento` ganha o sinal `evento_persistido(Evento)`. Um `GravadorEventos` (em `models/`) escuta `evento_registrado`, grava no SQLite e então emite `evento_persistido`. O corte emergencial grava por conta própria dentro da transação e emite `evento_persistido` só após o `COMMIT`. O `HistoricoController` passa a escutar **apenas** `evento_persistido` (evita gravação duplicada e linha na tabela que não existe no banco).
5. **SQL somente com `?`**. Nada de f-string/concatenação em comando SQL (nem em `LIMIT`/`ORDER BY` dinâmicos — usar lista branca fixa).
6. **Log**: `logging.getLogger("smartgrid")`, nunca `print`. Formato: `2026-10-20 14:03:11 [INFO] mensagem`. Níveis: `INFO` operação normal, `WARNING` limite violado/entrada inválida/config corrompida, `ERROR` exceção + rollback.
7. **Validação preventiva antes de tocar no banco**; `sqlite3.Error` sempre capturado no controller → `QMessageBox.critical` + log `ERROR`. A UI nunca deve quebrar com traceback.
8. **Tabelas** (resumo, DDL completa na issue M0):

```sql
dispositivos(id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL,
             status TEXT NOT NULL CHECK(status IN ('ABERTO','FECHADO')), ultima_atualizacao TEXT NOT NULL)
regras_limites(id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
               grandeza TEXT NOT NULL CHECK(grandeza IN ('CORRENTE','TENSAO','POTENCIA')),
               limite_maximo REAL NOT NULL CHECK(limite_maximo > 0), ativa INTEGER NOT NULL CHECK(ativa IN (0,1)))
eventos(id INTEGER PRIMARY KEY AUTOINCREMENT, data_hora TEXT NOT NULL,
        tipo_evento TEXT NOT NULL CHECK(tipo_evento IN ('Comando','Alerta','Status')),
        valor_medido TEXT, descricao TEXT NOT NULL,
        dispositivo_id INTEGER REFERENCES dispositivos(id) ON DELETE SET NULL)
```

   A FK `eventos.dispositivo_id → dispositivos.id` garante o uso real de chaves estrangeiras.

9. **Árvore alvo**:

```
SmartGrid/
├── database/      connection.py · schema.py · __init__.py
├── models/        repositorio_*.py (SQLite) · gravador_eventos.py · config_manager.py · log_config.py
├── controllers/   (já existente)
├── ui/            (já existente)
├── logs/          .gitkeep   (smartgrid.log ignorado)
├── data/          .gitkeep   (smartgrid.db e config.json ignorados)
└── main.py        init logging → init config → Database.init_db() → monta telas
```

---

## 3. Passo a passo de desenvolvimento

**Passo 0 — Preparação (todos, 10 min).** `git switch main && git pull`. Cada pessoa cria sua branch a partir da `main` somente depois do M0 mergeado.

**Passo 1 — M0 Fundação (bloqueante).** Samuel entrega `database/`, esquema, `transacao()`, sinal `evento_persistido`, `.gitignore`, pastas `data/` e `logs/`. Davy entrega o logger em paralelo (módulo isolado). Os dois PRs precisam estar na `main` antes do M1.

**Passo 2 — M1 Persistência por módulo (paralelo, propriedade exclusiva de arquivos).**
- Arthur: eventos (repositório SQLite + tela de histórico lendo do banco).
- Lucas: regras (repositório SQLite + diálogo robusto) e repositório de dispositivos.
- Davy: `config.json` + tela serial lendo/gravando a configuração.
- Samuel: corte emergencial transacional + log de alertas + `GravadorEventos` + `main.py`.

**Passo 3 — M2 Integração e robustez.** Fiação final (dashboard usa regras e dispositivos do banco; histórico atualiza ao vivo), mecanismo de **falha simulada** para a demonstração, testes automatizados de integridade/rollback/SQL parametrizado, auditoria do log.

**Passo 4 — M3 Entrega.** README atualizado (ER, config, log), roteiro de demonstração ensaiado (3 cenários da arguição), verificação do `.gitignore` e da distribuição de commits por integrante.

**Passo 5 — Ensaio da arguição (todos).** Seguir o roteiro da issue 17 do começo ao fim, abrindo `logs/smartgrid.log` ao final.

### Regras de convivência (iguais à A1/1)
- Branch por issue: `feat/<escopo>`; PR para `main`; ninguém commita direto em `main`.
- `git pull --rebase origin main` antes do PR.
- Commits pequenos e semânticos (`tipo(escopo): descrição`) — a participação individual é auditada.
- Só editar os arquivos listados na sua issue.

---

## 4. Labels

Reaproveitar: `arquitetura`, `bloqueante`, `integração`, `documentação`. Criar:

| Label | Cor | Descrição |
|---|---|---|
| `banco` | `#0E8A16` | SQLite, esquema, repositórios e transações |
| `config-log` | `#1D76DB` | config.json e smartgrid.log |
| `robustez` | `#D93F0B` | Validações, tolerância a falhas, rollback |
| `testes` | `#FBCA04` | Testes e roteiro de demonstração |

## 5. Milestones

| # | Título | Prazo sugerido | Descrição |
|---|---|---|---|
| M0 | M0 — A1/2: Fundação de Persistência | 18/10/2026 | `database/`, esquema, transação, logger, `.gitignore`. Bloqueia o resto. |
| M1 | M1 — A1/2: Persistência por Módulo | 01/11/2026 | Eventos, regras, dispositivos, config.json e corte transacional, em paralelo. |
| M2 | M2 — A1/2: Integração & Robustez | 08/11/2026 | Fiação final, falha simulada, testes de integridade, auditoria de log. |
| M3 | M3 — A1/2: Documentação & Entrega | 15/11/2026 | README, roteiro de demonstração, checklist Git. |

## 6. Distribuição por integrante

| Integrante | Issues | Foco |
|---|---|---|
| **Samuel** | 1, 3, 11, 12, 17 | `database/`, transação, corte emergencial atômico, `main.py`, fechamento |
| **Davy** | 2, 8, 9, 13, 16 | logger, `config.json`, tela serial, modo de falha simulada, README |
| **Lucas** | 6, 7, 10, 14 | regras no SQLite, validações do diálogo, dispositivos, auditoria de SQL/integridade |
| **Arthur** | 4, 5, 15, 18 | eventos no SQLite, histórico, teste de rollback, roteiro de demonstração |

Mapa de dependência: `1 → (3,4,6,10,11)`, `2 → (todos que logam)`, `3 → (4,11,12)`, `10 → 11`, `8 → (9,13)`, `11 + 13 → 15, 18`.

---

# 7. Issues

> Formato: **Título** · Milestone · Label(s) · Responsável. Corpo pronto para colar.

---

## M0 — A1/2: Fundação de Persistência

### Issue 1 — M0 — Pacote `database/`, esquema SQLite e transações
**Milestone:** M0 · **Labels:** `arquitetura`, `banco`, `bloqueante` · **Responsável:** @SamuelDeMarco-Dev

```markdown
## Objetivo
Criar a base de persistência. **Bloqueia** as issues de repositório (4, 6, 10, 11).

## Escopo
- `database/__init__.py`, `database/schema.py` (DDL das 3 tabelas: `dispositivos`, `regras_limites`, `eventos` — ver decisões de arquitetura no planejamento).
- `database/connection.py` com a classe `Database`:
  - `init_db()` — cria `data/smartgrid.db` (e a pasta `data/` se faltar), executa o DDL (`CREATE TABLE IF NOT EXISTS`), insere regras padrão (corrente 15 A, tensão 230 V, potência 3500 W) e os dois dispositivos (`DISJ-01` disjuntor principal, `RELE-01` relé de emergência, ambos `FECHADO`) **somente se vazios**.
  - `conectar()` — `sqlite3.connect(..., timeout=...)`, `PRAGMA foreign_keys = ON;`, `row_factory = sqlite3.Row`.
  - `transacao()` — context manager: commit no sucesso; em exceção `ROLLBACK`, `logger.error("Rollback executado: <motivo>")` e relança.
- Restrições obrigatórias: `PRIMARY KEY`, `NOT NULL`, `CHECK(status IN ('ABERTO','FECHADO'))`, `FOREIGN KEY eventos.dispositivo_id → dispositivos.id`.
- Pastas `data/.gitkeep` e `logs/.gitkeep`.
- `.gitignore`: acrescentar `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`, `data/config.json`, `logs/*.log`, `!*/.gitkeep`.

## Arquivos sob sua responsabilidade
`database/*`, `.gitignore`, `data/.gitkeep`, `logs/.gitkeep`

## Definição de pronto
- [ ] `Database.init_db()` chamado duas vezes não duplica dados nem falha
- [ ] Inserir `status='QUEIMADO'` em `dispositivos` levanta `IntegrityError`
- [ ] Inserir evento com `dispositivo_id` inexistente levanta `IntegrityError` (FK ativa)
- [ ] `git status` não mostra `smartgrid.db`, `*.db-wal`, `smartgrid.log`
- [ ] Todo SQL usa `?`
- [ ] PR aberto para `main`

## Commits sugeridos
`feat(database): cria conexão com pragmas e timeout` · `feat(database): define esquema relacional` · `feat(database): adiciona context manager de transação` · `chore(gitignore): ignora arquivos do SQLite, logs e config`
```

### Issue 2 — M0 — Módulo de log (`logging`) para auditoria
**Milestone:** M0 · **Labels:** `arquitetura`, `config-log`, `bloqueante` · **Responsável:** @Davyjosias01

```markdown
## Objetivo
Subsistema de log disponível para todos antes do M1.

## Escopo
- `models/log_config.py` com `configurar_log()` que:
  - cria `logs/` se não existir;
  - chama `logging.basicConfig(filename="logs/smartgrid.log", level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S", encoding="utf-8")` (ou handler equivalente);
  - é idempotente (chamar duas vezes não duplica handlers).
- `obter_logger()` devolvendo `logging.getLogger("smartgrid")`.
- Exemplos de mensagem que o restante da equipe vai usar:
  - `[INFO] Conexão com SQLite estabelecida e esquema validado.`
  - `[WARNING] Limite de corrente violado: 28.5A (Limite configurado: 25.0A).`
  - `[ERROR] Falha na operação de corte emergencial. Rollback executado: <motivo>.`
- Escrita com *flush* a cada registro (para abrir o arquivo logo após a demonstração).

## Arquivos sob sua responsabilidade
`models/log_config.py`

## Definição de pronto
- [ ] Arquivo `logs/smartgrid.log` criado automaticamente
- [ ] Linhas com timestamp, nível entre colchetes e descrição
- [ ] Acentos corretos (UTF-8)
- [ ] Docstring com exemplo de uso para os outros módulos
- [ ] PR aberto para `main`

## Commits sugeridos
`feat(log): configura logging com formato de auditoria` · `docs(log): documenta uso do logger`
```

### Issue 3 — M0 — Contrato: `evento_persistido` e `GravadorEventos`
**Milestone:** M0 · **Labels:** `arquitetura`, `banco`, `bloqueante` · **Responsável:** @SamuelDeMarco-Dev

```markdown
## Objetivo
Garantir um único ponto de gravação de eventos e que a tabela da UI só mostre o que já está no banco.

## Escopo
- `models/barramento.py`: novo sinal `evento_persistido = pyqtSignal(Evento)` (contrato descongelado só para esta mudança).
- `models/gravador_eventos.py`: `GravadorEventos(repositorio)` escuta `barramento.evento_registrado`, chama `repositorio.adicionar(evento)` e emite `evento_persistido`. Captura `sqlite3.Error`, loga `ERROR` e **não derruba** a aplicação.
- Documentar no docstring do barramento a diferença entre `evento_registrado` (pedido de gravação) e `evento_persistido` (gravado).
- Depende da issue 1 para a assinatura final de `RepositorioEventos.adicionar(evento, conn=None)`; começar com stub.

## Arquivos sob sua responsabilidade
`models/barramento.py`, `models/gravador_eventos.py`

## Definição de pronto
- [ ] Evento publicado no barramento é gravado exatamente uma vez
- [ ] Falha de banco gera log `[ERROR]` e a UI continua viva
- [ ] Comentário na issue 4 avisando Arthur sobre o novo sinal
- [ ] PR aberto para `main`
```

---

## M1 — A1/2: Persistência por Módulo

### Issue 4 — Histórico — `RepositorioEventos` em SQLite e tabela carregada do banco
**Milestone:** M1 · **Labels:** `banco`, `tela` · **Responsável:** @ArthurDeMarcoF · **Depende de:** 1, 3

```markdown
## Objetivo
A `QTableWidget` deixa de usar dados estáticos e passa a ler do SQLite.

## Escopo
- Reescrever `models/repositorio_eventos.py`:
  - `listar_todos() -> list[Evento]` (mais recente primeiro, `ORDER BY data_hora DESC`);
  - `adicionar(evento, conn=None)` — `INSERT ... VALUES (?, ?, ?, ?)`; aceita `conn` para participar de transação externa;
  - `filtrar(tipo=None, data_inicio=None)` com SQL parametrizado;
  - `limpar(conn=None)`.
  - Remover `_criar_eventos_amostrais()`.
- `controllers/historico_controller.py`:
  - carregar a tabela em `showEvent` (e na inicialização) via `Repository.listar_todos()`;
  - escutar `barramento.evento_persistido` (não mais `evento_registrado`) e inserir a linha no topo.
- Converter `data_hora` ↔ texto ISO (`%Y-%m-%d %H:%M:%S`) na fronteira repositório/entidade; a UI continua exibindo `dd/mm/aaaa HH:MM:SS`.

## Arquivos sob sua responsabilidade
`models/repositorio_eventos.py`, `controllers/historico_controller.py`, `ui/historico.ui` + `_ui.py`

## Somente leitura
`database/*`, `models/barramento.py`, `models/gravador_eventos.py`

## Definição de pronto
- [ ] Fechar e reabrir o app mantém os eventos
- [ ] Nenhum evento amostral hardcoded
- [ ] Novo evento (ex.: corte emergencial) aparece na tabela sem recarregar a janela
- [ ] SQL 100% com `?`
- [ ] Erro de banco → `QMessageBox.critical` + log, sem traceback
- [ ] PR aberto para `main`

## Commits sugeridos
`feat(historico): persiste eventos em SQLite` · `feat(historico): carrega tabela no showEvent via listar_todos` · `refactor(historico): escuta evento_persistido`
```

### Issue 5 — Histórico — Filtros SQL, limpeza atômica e tolerância a falhas
**Milestone:** M1 · **Labels:** `banco`, `robustez` · **Responsável:** @ArthurDeMarcoF · **Depende de:** 4

```markdown
## Escopo
- Filtros por tipo e data inicial executados **no SQL** (`WHERE tipo_evento = ? AND data_hora >= ?`).
- "Limpar histórico" (`QMessageBox.question`) dentro de `Database.transacao()`; registrar evento/Log `[INFO]` "Histórico limpo (N registros)".
- Data inicial inválida ou intervalo incoerente → `QMessageBox.warning` e `[WARNING]` no log, sem consultar o banco.
- Banco indisponível/bloqueado (`sqlite3.OperationalError`) → mensagem amigável e `[ERROR]`.

## Definição de pronto
- [ ] Filtros funcionam com grande volume (inserir ~1000 eventos para testar)
- [ ] Limpeza é tudo-ou-nada (se falhar, nada some)
- [ ] Nenhuma exceção sem tratamento chega à UI
- [ ] PR aberto para `main`
```

### Issue 6 — Limites — `RepositorioRegras` em SQLite
**Milestone:** M1 · **Labels:** `banco` · **Responsável:** @lucaszlocatelli · **Depende de:** 1

```markdown
## Objetivo
Regras de alerta (limites de corrente/tensão/potência) persistidas na tabela `regras_limites`.

## Escopo
- Reescrever `models/repositorio_regras.py` mantendo a API usada hoje: `obter_regras()` e `salvar_regras(lista)`.
- `salvar_regras` troca o conjunto inteiro **em uma transação** (`DELETE` + `INSERT`s) — se uma regra falhar, nenhuma é alterada.
- Remover `repositorio_regras_compartilhado` global criado no import (cria conexão antes do `init_db`); substituir por função/instância criada depois do `init_db()` e injetada em `main.py`/controllers (combinar assinatura com Samuel).
- Regras padrão vêm do `Database.init_db()` (issue 1), não do repositório.

## Arquivos sob sua responsabilidade
`models/repositorio_regras.py`, `controllers/limites_dialog_controller.py` (somente a troca do repositório)

## Definição de pronto
- [ ] Reiniciar o app mantém os limites salvos
- [ ] Salvar com regra inválida não deixa estado parcial no banco
- [ ] `barramento.regras_alteradas` continua sendo emitido após salvar
- [ ] SQL com `?`
- [ ] PR aberto para `main`
```

### Issue 7 — Limites — Validação preventiva do diálogo
**Milestone:** M1 · **Labels:** `robustez`, `tela` · **Responsável:** @lucaszlocatelli · **Depende de:** 6

```markdown
## Escopo (cenário "Falha de Validação" da arguição)
- Bloquear com `QMessageBox.warning` (sem fechar o diálogo): nome vazio/duplicado, limite vazio, **texto/letras no campo numérico**, valor negativo ou zero.
- Se o campo numérico continuar sendo `QDoubleSpinBox`, garantir que o caso "letras" seja tratado onde houver `QLineEdit`/entrada textual; caso contrário trocar por `QLineEdit` + `QDoubleValidator` e converter com `try/except ValueError`.
- Cada rejeição gera `[WARNING] Entrada inválida em limites: <campo>=<valor>`.
- Exceção do banco ao salvar → `QMessageBox.critical` + `[ERROR]`.

## Definição de pronto
- [ ] Os 4 casos (vazio, letras, negativo, zero) bloqueados com alerta visual
- [ ] Cada caso aparece no log
- [ ] Nada é gravado no banco em caso de rejeição
- [ ] PR aberto para `main`
```

### Issue 8 — Config — `ConfigManager` (`data/config.json`)
**Milestone:** M1 · **Labels:** `config-log` · **Responsável:** @Davyjosias01 · **Depende de:** 2

```markdown
## Escopo
- `models/config_manager.py`:
  - `carregar()` — usa `os.path.exists`; se o arquivo não existir, cria `data/config.json` com valores default e loga `[INFO]`;
  - `salvar(config)` — escrita atômica (grava em `.tmp` e `os.replace`);
  - JSON corrompido ou campo com tipo errado → recai nos defaults daquele campo, loga `[WARNING]`, não quebra.
- Chaves: `porta_com`, `baud_rate` (padrão 9600), `timeout` , `limites_iniciais` (`corrente`, `tensao`, `potencia`), `simular_falha_corte` (bool, padrão `false` — usada na issue 13).
- Validar faixas (baud ∈ {9600, 115200}, timeout > 0, limites > 0).

## Arquivos sob sua responsabilidade
`models/config_manager.py`

## Definição de pronto
- [ ] Apagar `config.json` → app recria com defaults
- [ ] Corromper o JSON → app abre normalmente e loga `[WARNING]`
- [ ] PR aberto para `main`
```

### Issue 9 — Serial — Tela de comunicação lê e grava `config.json`
**Milestone:** M1 · **Labels:** `config-log`, `tela` · **Responsável:** @Davyjosias01 · **Depende de:** 8

```markdown
## Escopo
- `controllers/serial_config_controller.py`: preencher porta, baud e timeout a partir do `ConfigManager` ao abrir; salvar ao conectar com sucesso.
- Porta salva que não existe mais → selecionar a primeira disponível e `[WARNING]`.
- Validar timeout (vazio, letras, negativo) com `QMessageBox.warning`.
- Logar `[INFO]` em conectar/desconectar (porta, baud) e `[ERROR]` em falha de abertura.

## Definição de pronto
- [ ] Fechar/abrir o app preserva a última configuração
- [ ] Validações com alerta visual
- [ ] Log de conectar/desconectar
- [ ] PR aberto para `main`
```

### Issue 10 — Dispositivos — `RepositorioDispositivos` (estado do disjuntor e relé)
**Milestone:** M1 · **Labels:** `banco` · **Responsável:** @lucaszlocatelli · **Depende de:** 1

```markdown
## Escopo
- `models/repositorio_dispositivos.py`:
  - `obter_por_tag(tag)` / `listar()`;
  - `atualizar_status(tag, status, conn=None)` — `UPDATE dispositivos SET status = ?, ultima_atualizacao = ? WHERE tag = ?`; aceita `conn` externo; levanta erro claro se a tag não existir (`rowcount == 0`).
- Mapear `EstadoDisjuntor` (`models/estado_disjuntor.py`) ↔ `'ABERTO'/'FECHADO'`.

## Definição de pronto
- [ ] Tentativa de gravar status fora de `ABERTO/FECHADO` falha pelo `CHECK`
- [ ] `conn` externo permite que o UPDATE seja desfeito por um rollback do chamador
- [ ] SQL com `?`
- [ ] PR aberto para `main`
```

### Issue 11 — Dashboard — Corte de emergência transacional
**Milestone:** M1 · **Labels:** `banco`, `robustez`, `tela` · **Responsável:** @SamuelDeMarco-Dev · **Depende de:** 1, 3, 10

```markdown
## Objetivo
O coração do cenário "Falha Transacional com Rollback".

## Escopo
- `models/servico_corte.py` (`ServicoCorteEmergencial`):
  ```
  with Database.transacao() as conn:
      repo_dispositivos.atualizar_status("DISJ-01", ABERTO, conn)
      repo_dispositivos.atualizar_status("RELE-01", ABERTO, conn)
      # ponto de injeção de falha (issue 13)
      repo_eventos.adicionar(Evento.agora(COMANDO, "Corte de emergência acionado", ...), conn)
  ```
  Após o `COMMIT`: emitir `evento_persistido` e `estado_disjuntor_alterado`.
- Em exceção: rollback automático (da `transacao()`), `[ERROR] Falha na operação de corte emergencial. Rollback executado: <motivo>.`, `QMessageBox.critical` e a UI **não** muda de estado.
- `controllers/dashboard_controller.py::alternar_carga` (linha ~332) chama o serviço; religamento segue a mesma lógica.
- Ao abrir o dashboard, restaurar o estado do disjuntor a partir de `dispositivos`.

## Arquivos sob sua responsabilidade
`models/servico_corte.py`, `controllers/dashboard_controller.py`, `ui/dashboard.ui` + `_ui.py`

## Definição de pronto
- [ ] Sucesso: banco, tabela do histórico e badge consistentes
- [ ] Falha entre UPDATE e INSERT: dispositivos continuam `FECHADO`, nenhum evento gravado
- [ ] Log `[INFO]` no sucesso e `[ERROR]` com "Rollback executado" na falha
- [ ] Fechar/abrir o app preserva o estado do disjuntor
- [ ] PR aberto para `main`
```

### Issue 12 — Dashboard/Main — Alertas persistidos, log de limite e `main.py` de inicialização
**Milestone:** M1 · **Labels:** `banco`, `config-log`, `arquitetura` · **Responsável:** @SamuelDeMarco-Dev · **Depende de:** 2, 3, 6

```markdown
## Escopo
- `main.py` enxuto, nesta ordem: `configurar_log()` → `ConfigManager.carregar()` → `Database.init_db()` (log `[INFO] Conexão com SQLite estabelecida e esquema validado.`) → instanciar `GravadorEventos` → montar telas. Falha na inicialização do banco → `QMessageBox.critical`, log `[ERROR]` e encerramento limpo.
- `_avaliar_protecao` (dashboard, ~linha 200): ao violar limite, `[WARNING] Limite de corrente violado: 28.5A (Limite configurado: 25.0A).` e evento `Alerta` pelo barramento (persistido pelo `GravadorEventos`). Evitar disparar um evento por amostra: um por transição de "dentro" para "fora" do limite.
- Disparo de alarme visual também gera evento persistido.
- Disparo espontâneo do disjuntor (`_tratar_disparo_espontaneo`) atualiza `dispositivos` e registra evento.

## Definição de pronto
- [ ] `main.py` sem regra de negócio
- [ ] Log mostra: banco inicializado, alerta de limite, disparo espontâneo
- [ ] Sem flood de eventos duplicados
- [ ] PR aberto para `main`
```

---

## M2 — A1/2: Integração & Robustez

### Issue 13 — Modo de falha simulada para a demonstração
**Milestone:** M2 · **Labels:** `robustez`, `testes` · **Responsável:** @Davyjosias01 · **Depende de:** 8, 11

```markdown
## Objetivo
Permitir provocar, ao vivo, uma interrupção entre o UPDATE do corte e o INSERT do histórico.

## Escopo
- Ler `simular_falha_corte` do `config.json` **e** um atalho (ex.: `Ctrl+Shift+F`) na janela principal que alterna a flag em memória (combinar com Samuel o ponto de gancho em `ServicoCorteEmergencial`).
- Quando ativa, o serviço levanta `RuntimeError("Falha simulada entre corte e histórico")` após o UPDATE e antes do INSERT.
- Indicador visível na barra de status quando o modo está ativo ("MODO DE TESTE: falha simulada").
- Log `[WARNING] Modo de falha simulada ativado/desativado`.

## Definição de pronto
- [ ] Com a flag ativa, o corte falha, o banco volta ao estado anterior (comprovado por consulta SQL)
- [ ] Desativando, o corte volta a funcionar
- [ ] Documentado no README (issue 16)
- [ ] PR aberto para `main`
```

### Issue 14 — Auditoria de SQL parametrizado e testes de integridade
**Milestone:** M2 · **Labels:** `testes`, `banco` · **Responsável:** @lucaszlocatelli · **Depende de:** 4, 6, 10

```markdown
## Escopo
- Script/`pytest` em `tests/` usando banco temporário (`tmp_path`):
  - `CHECK` de status rejeita valor inválido;
  - FK rejeita `dispositivo_id` inexistente (com `PRAGMA foreign_keys = ON`);
  - `limite_maximo <= 0` rejeitado;
  - `init_db()` idempotente.
- Verificação automática de interpolação em SQL: `grep -rnE "(execute|executemany)\(\s*f[\"']" --include=*.py .` e `\.format\(`/`%` em SQL — deve retornar vazio. Documentar o comando no README.
- Corrigir (via comentário/PR na issue do dono) qualquer ocorrência encontrada.

## Definição de pronto
- [ ] `pytest` verde
- [ ] Nenhuma interpolação de SQL no repositório
- [ ] `pytest` adicionado ao `requirements.txt` (ou `requirements-dev.txt`)
- [ ] PR aberto para `main`
```

### Issue 15 — Teste de rollback e auditoria do log
**Milestone:** M2 · **Labels:** `testes`, `robustez` · **Responsável:** @ArthurDeMarcoF · **Depende de:** 11, 13

```markdown
## Escopo
- Teste automatizado: forçar exceção entre `atualizar_status` e `adicionar` → após o erro, `SELECT status FROM dispositivos` continua `FECHADO` e `SELECT COUNT(*) FROM eventos` não aumentou.
- Teste do sucesso: ambos persistidos juntos.
- Teste do log: após a falha, `logs/smartgrid.log` contém `[ERROR]` e "Rollback executado".
- Teste da limpeza do histórico (atomicidade).

## Definição de pronto
- [ ] Testes verdes
- [ ] Evidência (print/trecho de log) anexada ao PR
- [ ] PR aberto para `main`
```

---

## M3 — A1/2: Documentação & Entrega

### Issue 16 — README da A1/2
**Milestone:** M3 · **Labels:** `documentação` · **Responsável:** @Davyjosias01

```markdown
## Escopo
- Seções novas: persistência (diagrama ER das 3 tabelas), `config.json` (chaves e defaults), `smartgrid.log` (formato e exemplos), como ativar a falha simulada, como rodar os testes, nova árvore de pastas.
- Atualizar tabela de milestones e equipe.
- Checklist de entrega A1/2.

## Definição de pronto
- [ ] README coerente com o código final
- [ ] Nenhum link quebrado
- [ ] PR aberto para `main`
```

### Issue 17 — Integração final, `.gitignore` e auditoria de commits
**Milestone:** M3 · **Labels:** `integração`, `documentação` · **Responsável:** @SamuelDeMarco-Dev

```markdown
## Escopo
- Rodar a aplicação do zero (apagar `data/` e `logs/`) e confirmar criação automática de DB, config e log.
- `git ls-files | grep -E "\.db|\.log|__pycache__|\.venv|\.vscode"` deve retornar vazio.
- `git shortlog -sn` — cada integrante com commits próprios e semânticos; redistribuir tarefas pequenas se algum estiver baixo.
- Revisar PRs pendentes e resolver conflitos.
- Tag `a1-2-entrega`.

## Definição de pronto
- [ ] Execução limpa em máquina sem `data/` e `logs/`
- [ ] `.gitignore` validado
- [ ] Todos os integrantes com histórico visível
- [ ] Todas as issues M0–M3 fechadas
```

### Issue 18 — Roteiro de demonstração (arguição técnica)
**Milestone:** M3 · **Labels:** `testes`, `documentação` · **Responsável:** @ArthurDeMarcoF · **Depende de:** 7, 13, 15

```markdown
## Escopo
Roteiro ensaiado, com quem faz cada passo:

1. **Falha de validação** — no diálogo de limites: campo vazio, letras, valor negativo → `QMessageBox`; na tela serial: timeout inválido.
2. **Falha transacional** — ativar o modo de falha simulada (`Ctrl+Shift+F`) → clicar em Corte Emergencial → mostrar erro; abrir o banco (`sqlite3 data/smartgrid.db "SELECT tag,status FROM dispositivos; SELECT COUNT(*) FROM eventos;"`) e provar que nada mudou; desativar a falha e repetir com sucesso.
3. **Auditoria** — abrir `logs/smartgrid.log` e apontar `[WARNING]` das validações, `[ERROR] ... Rollback executado: ...` e `[INFO]` do corte bem-sucedido.

## Definição de pronto
- [ ] Roteiro em `docs/roteiro-demonstracao.md` (ou seção do README)
- [ ] Ensaio completo realizado por todos, cronometrado
- [ ] Banco reiniciado antes do ensaio final para dados limpos
- [ ] PR aberto para `main`
```

---

## 8. Checklist de aderência ao enunciado

| Requisito | Onde é atendido |
|---|---|
| `smartgrid.db` criado por `Database.init_db()` | 1 |
| Tabelas dispositivos / eventos / regras | 1, 4, 6, 10 |
| PK, NOT NULL, CHECK, `PRAGMA foreign_keys` | 1, 14 |
| `QTableWidget` via `Repository.listar_todos()` no `showEvent` | 4 |
| Eventos novos gravados e refletidos na tabela | 3, 11, 12 |
| `config.json` + `os.path.exists` + defaults | 8, 9 |
| `smartgrid.log` com timestamp, `[INFO]/[WARNING]/[ERROR]` | 2, 11, 12 |
| `.gitignore` completo | 1, 17 |
| Estrutura de pastas MVC | 1, 12 |
| SQL só com `?` | 14 |
| Demo: validação / rollback / log | 7, 11, 13, 15, 18 |
