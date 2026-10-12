"""Esquema relacional do SmartGrid. DDL e dados iniciais."""

TABELAS = (
    """
    CREATE TABLE IF NOT EXISTS dispositivos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag TEXT NOT NULL UNIQUE,
        tipo TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ABERTO', 'FECHADO')),
        ultima_atualizacao TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS regras_limites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        grandeza TEXT NOT NULL CHECK(grandeza IN ('CORRENTE', 'TENSAO', 'POTENCIA')),
        limite_maximo REAL NOT NULL CHECK(limite_maximo > 0),
        ativa INTEGER NOT NULL CHECK(ativa IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_hora TEXT NOT NULL,
        tipo_evento TEXT NOT NULL CHECK(tipo_evento IN ('Comando', 'Alerta', 'Status')),
        valor_medido TEXT,
        descricao TEXT NOT NULL,
        dispositivo_id INTEGER REFERENCES dispositivos(id) ON DELETE SET NULL
    )
    """,
)

DISPOSITIVOS_INICIAIS = (
    ("DISJ-01", "Disjuntor principal", "FECHADO"),
    ("RELE-01", "Relé de emergência", "FECHADO"),
)

# (nome, grandeza, limite_maximo, ativa)
REGRAS_INICIAIS = (
    ("Alerta Sobrecorrente", "CORRENTE", 15.0, 1),
    ("Alerta Sobretensão", "TENSAO", 230.0, 1),
    ("Pico de Potência", "POTENCIA", 3500.0, 0),
)
