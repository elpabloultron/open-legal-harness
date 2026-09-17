-- ============================================================================
-- Open Legal CRM - Esquema de dominio (portable SQLite / PostgreSQL)
-- Un solo modelo para los dos modos:
--   modo 'solo'     -> SQLite local, un estudio implicito, un usuario
--   modo 'oficina'  -> PostgreSQL, varios usuarios, roles y asignacion de causas
-- Todos los registros operativos cuelgan de `estudios` (aislamiento por estudio).
-- ============================================================================

CREATE TABLE IF NOT EXISTS estudios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre       TEXT NOT NULL,
    rut          TEXT,
    modo         TEXT NOT NULL DEFAULT 'solo',     -- 'solo' | 'oficina'
    creado_en    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usuarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    estudio_id     INTEGER NOT NULL REFERENCES estudios(id) ON DELETE CASCADE,
    nombre         TEXT NOT NULL,
    email          TEXT NOT NULL,
    rol            TEXT NOT NULL,                  -- socio|abogado|paralegal|administrativo|cliente
    password_hash  TEXT,
    activo         INTEGER NOT NULL DEFAULT 1,
    creado_en      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (estudio_id, email)
);

CREATE TABLE IF NOT EXISTS sesiones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    token       TEXT NOT NULL UNIQUE,
    creada_en   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expira_en   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clientes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    estudio_id    INTEGER NOT NULL REFERENCES estudios(id) ON DELETE CASCADE,
    rut           TEXT,
    nombre        TEXT NOT NULL,
    tipo_persona  TEXT NOT NULL DEFAULT 'natural', -- natural | juridica
    email         TEXT,
    telefono      TEXT,
    direccion     TEXT,
    creado_en     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS causas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    estudio_id       INTEGER NOT NULL REFERENCES estudios(id) ON DELETE CASCADE,
    cliente_id       INTEGER REFERENCES clientes(id) ON DELETE SET NULL,
    rol_rit          TEXT,                          -- Rol PJUD / RIT
    caratula         TEXT NOT NULL,
    tribunal         TEXT,
    materia          TEXT,                          -- laboral|civil|penal|familia|...
    estado_procesal  TEXT NOT NULL DEFAULT 'tramitacion',
    contraparte      TEXT,
    cuantia_clp      INTEGER,
    observaciones    TEXT,
    creado_en        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Quien trabaja cada causa. Sin fila aqui, el usuario no tiene acceso a ella.
CREATE TABLE IF NOT EXISTS causa_equipo (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    causa_id     INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,
    usuario_id   INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    rol_en_causa TEXT NOT NULL DEFAULT 'responsable', -- responsable|colaborador|apoyo
    desde        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hasta        TEXT,
    UNIQUE (causa_id, usuario_id)
);

CREATE TABLE IF NOT EXISTS plazos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    causa_id             INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,
    descripcion          TEXT NOT NULL,
    tipo                 TEXT NOT NULL DEFAULT 'judicial', -- judicial|administrativo|interno
    fecha_notificacion   TEXT,                             -- YYYY-MM-DD
    dias                 INTEGER,                          -- dias habiles (Art. 66 CPC)
    fecha_vencimiento    TEXT,                             -- YYYY-MM-DD
    es_fatal             INTEGER NOT NULL DEFAULT 0,
    responsable_id       INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    estado               TEXT NOT NULL DEFAULT 'pendiente', -- pendiente|cumplido|vencido|suspendido
    creado_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audiencias (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    causa_id    INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL,
    fecha       TEXT NOT NULL,                      -- YYYY-MM-DD
    hora        TEXT,
    modalidad   TEXT NOT NULL DEFAULT 'presencial', -- presencial|remota|hibrida
    lugar_o_url TEXT,
    estado      TEXT NOT NULL DEFAULT 'programada',
    minuta      TEXT,
    creado_en   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- visibilidad separa lo interno del estudio de lo publicable al cliente.
CREATE TABLE IF NOT EXISTS documentos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    causa_id    INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,
    nombre      TEXT NOT NULL,
    ruta        TEXT,
    tipo        TEXT,
    visibilidad TEXT NOT NULL DEFAULT 'interno',    -- interno | cliente
    subido_por  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    creado_en   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS honorarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    causa_id       INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,
    modalidad      TEXT NOT NULL DEFAULT 'fijo',    -- fijo|hora|cuota_litis|mixto
    monto_pactado  INTEGER,
    monto_bruto    INTEGER,
    retencion_sii  INTEGER,
    monto_liquido  INTEGER,
    monto_pagado   INTEGER NOT NULL DEFAULT 0,
    estado_pago    TEXT NOT NULL DEFAULT 'pendiente',
    creado_en      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gastos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    causa_id             INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,
    concepto             TEXT NOT NULL,
    monto                INTEGER NOT NULL DEFAULT 0,
    pagado_por_estudio   INTEGER NOT NULL DEFAULT 1,
    reembolsado          INTEGER NOT NULL DEFAULT 0,
    fecha                TEXT,
    creado_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Bitacora: quien hizo que y cuando. Requisito de cualquier oficina.
CREATE TABLE IF NOT EXISTS auditoria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    estudio_id  INTEGER,
    usuario_id  INTEGER,
    accion      TEXT NOT NULL,
    entidad     TEXT,
    entidad_id  INTEGER,
    detalle     TEXT,
    creado_en   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_causas_estudio    ON causas (estudio_id, estado_procesal);
CREATE INDEX IF NOT EXISTS idx_equipo_usuario    ON causa_equipo (usuario_id);
CREATE INDEX IF NOT EXISTS idx_plazos_vencimiento ON plazos (fecha_vencimiento, estado);
CREATE INDEX IF NOT EXISTS idx_audiencias_fecha  ON audiencias (fecha);
CREATE INDEX IF NOT EXISTS idx_auditoria_estudio ON auditoria (estudio_id, creado_en);
