"""Capa de almacenamiento portable.

Modo 'solo'    -> SQLite local (por defecto en ~/.openlegal/openlegal.db)
Modo 'oficina' -> PostgreSQL via LEGALCRM_DB_URL=postgresql://usuario:clave@host/db

El SQL de la aplicacion usa `?` como marcador; para Postgres se traduce a `%s`.
El esquema en schema.sql esta escrito en dialecto SQLite y se traduce al vuelo.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
from collections.abc import Callable

RAIZ = pathlib.Path(__file__).resolve().parent
SCHEMA = RAIZ / "schema.sql"

# Columnas agregadas después de la primera versión. `CREATE TABLE IF NOT EXISTS`
# no toca una tabla que ya existe, así que las bases viejas se actualizan con
# ALTER TABLE en `migrar()` — que se corre en cada `openlegal init`.
COLUMNAS_NUEVAS: dict[str, dict[str, str]] = {
    "clientes": {"representante_legal": "TEXT"},
}


def url_por_defecto() -> str:
    base = pathlib.Path(os.environ.get("OPENLEGAL_HOME", pathlib.Path.home() / ".openlegal"))
    return f"sqlite:///{base / 'openlegal.db'}"


class DB:
    """Conexion unica con helpers de consulta y migracion."""

    def __init__(self, url: str | None = None):
        self.url = url or os.environ.get("LEGALCRM_DB_URL") or url_por_defecto()
        self.dialecto = "postgres" if self.url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self.conn = self._conectar()
        self._cerrada = False

    # ---------------------------------------------------------------- conexion
    def _conectar(self):
        if self.dialecto == "sqlite":
            ruta = self.url.replace("sqlite:///", "", 1)
            if ruta not in (":memory:", ""):
                pathlib.Path(ruta).parent.mkdir(parents=True, exist_ok=True)
            # `check_same_thread=False` porque el panel web abre la conexión en un hilo y la
            # cierra en otro: FastAPI atiende cada petición en un hilo del grupo, y la parte
            # final de una dependencia (`yield`) puede tocar otro. Sin esto, el cierre revienta
            # con «SQLite objects created in a thread can only be used in that same thread» y
            # el módulo del panel devuelve un error. Cada petición abre su propia conexión y la
            # usa de a una operación por vez, así que no hay dos hilos escribiendo a la vez.
            conn = sqlite3.connect(ruta, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            if ruta != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            return conn
        try:
            import psycopg
        except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "El modo oficina requiere PostgreSQL: instala el driver con "
                "`pip install 'open-legal-harness[postgres]'`."
            ) from exc
        return psycopg.connect(self.url, row_factory=psycopg.rows.dict_row)

    # ------------------------------------------------------------------- SQL
    def _sql(self, sql: str) -> str:
        if self.dialecto == "postgres":
            sql = sql.replace(" INTEGER PRIMARY KEY AUTOINCREMENT", " SERIAL PRIMARY KEY")
            sql = sql.replace("?", "%s")
        return sql

    def ejecutar(self, sql: str, params: tuple = ()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        if self.dialecto == "sqlite":
            self.conn.commit()
        else:
            self.conn.commit()
        return cur

    def uno(self, sql: str, params: tuple = ()) -> dict | None:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        fila = cur.fetchone()
        if fila is None:
            return None
        return dict(fila)

    def todos(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        return [dict(f) for f in cur.fetchall()]

    def insertar(self, tabla: str, datos: dict) -> int:
        columnas = ", ".join(datos)
        marcas = ", ".join("?" for _ in datos)
        sql = f"INSERT INTO {tabla} ({columnas}) VALUES ({marcas})"
        if self.dialecto == "postgres":
            sql += " RETURNING id"
        cur = self.ejecutar(sql, tuple(datos.values()))
        if self.dialecto == "postgres":
            # El cursor usa dict_row: el ID vuelve como {'id': N}, no como tupla.
            fila = cur.fetchone()
            return int(fila["id"] if isinstance(fila, dict) else fila[0])
        return int(cur.lastrowid)

    # --------------------------------------------------------------- migracion
    def tablas(self) -> set[str]:
        """Nombres de las tablas que existen hoy en la base."""
        if self.dialecto == "sqlite":
            return {f["name"] for f in self.todos("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return {
            f["table_name"]
            for f in self.todos(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }

    def columnas(self, tabla: str) -> set[str]:
        if self.dialecto == "sqlite":
            return {f["name"] for f in self.todos(f"PRAGMA table_info({tabla})")}
        filas = self.todos(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (tabla,)
        )
        return {f["column_name"] for f in filas}

    # ------------------------------------------- migraciones versionadas (F1)
    def migraciones_aplicadas(self) -> dict[int, dict]:
        """{version: fila} de lo que ya está aplicado en ESTA base.

        Es una lectura: si la base todavía no tiene el registro (es anterior a él),
        devuelve vacío y NO crea nada. Quien aplica y registra es `migrar()`.
        """
        if "migraciones" not in self.tablas():
            return {}
        return {int(f["version"]): f for f in self.todos("SELECT * FROM migraciones ORDER BY version")}

    def migraciones_pendientes(self) -> list[tuple[int, str]]:
        """Las que faltan, en orden. Vacío = la base está al día."""
        aplicadas = self.migraciones_aplicadas()
        return [(version, nombre) for version, nombre, _ in MIGRACIONES if version not in aplicadas]

    def migrar(self) -> list[str]:
        """Pone la base al día y devuelve qué hizo, en texto. Idempotente.

        Conviven dos mecanismos a propósito:

        - las MIGRACIONES versionadas, que quedan registradas en la tabla `migraciones`
          (así una base sabe en qué punto está y no hay que adivinar por las columnas);
        - la reconciliación de columnas sueltas (`COLUMNAS_NUEVAS`), que es la red que ya
          existía antes de que hubiera registro. Se mantiene porque es idempotente y
          porque hay bases instaladas que dependen de ella.
        """
        aplicadas = self._aplicar_migraciones()
        aplicadas += self._reconciliar_columnas_sueltas()
        return aplicadas

    # ------------------------------------------------------- interno de migrar
    def _asegurar_tabla_de_migraciones(self) -> None:
        self.ejecutar(
            "CREATE TABLE IF NOT EXISTS migraciones ("
            " version INTEGER PRIMARY KEY,"
            " nombre TEXT NOT NULL,"
            " aplicada_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    def _aplicar_migraciones(self) -> list[str]:
        """Aplica las pendientes, en orden, y las registra."""
        self._asegurar_tabla_de_migraciones()
        ya = {int(f["version"]) for f in self.todos("SELECT version FROM migraciones")}
        aplicadas: list[str] = []

        # Base anterior a este registro: tiene tablas pero no sabe en qué versión está.
        # Se la marca en la 1 SIN volver a correr el esquema base (volver a correrlo
        # sería inofensivo para el esquema, pero marcar y no adivinar es lo honesto).
        if not ya and "clientes" in self.tablas():
            self.ejecutar(
                "INSERT INTO migraciones (version, nombre) VALUES (?, ?)",
                (1, f"{MIGRACIONES[0][1]} (base anterior, marcada al adoptar el registro)"),
            )
            ya = {1}
            aplicadas.append("marcada 1 (la base ya existía)")

        for version, nombre, funcion in MIGRACIONES:
            if version in ya:
                continue
            aplicadas += funcion(self)
            self.ejecutar(
                "INSERT INTO migraciones (version, nombre) VALUES (?, ?)", (version, nombre)
            )
            aplicadas.append(f"migración {version}: {nombre}")
        return aplicadas

    def _reconciliar_columnas_sueltas(self) -> list[str]:
        aplicadas: list[str] = []
        for tabla, columnas in COLUMNAS_NUEVAS.items():
            existentes = self.columnas(tabla)
            for nombre, definicion in columnas.items():
                if nombre not in existentes:
                    self.ejecutar(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
                    aplicadas.append(f"ALTER {tabla}.{nombre}")
        return aplicadas

    def cerrar(self) -> None:
        """Cierra la conexion. Idempotente: se llama al salir del contexto y en tests.

        Que sea idempotente y que exista el gestor de contexto no es adorno: en Windows
        un archivo abierto NO se puede borrar, asi que una conexion que queda viva hace
        fallar la limpieza de la base temporal (y, en produccion, deja la base tomada).
        """
        if getattr(self, "_cerrada", False):
            return
        self.conn.close()
        self._cerrada = True

    def __enter__(self) -> DB:
        return self

    def __exit__(self, *_: object) -> None:
        self.cerrar()


# ==================================================================== migraciones
# El esquema base vive en schema.sql y ES la migración 1. Cada cambio posterior de
# estructura es una migración más, con su número, y queda registrado en la tabla
# `migraciones` de cada base: así una base sabe en qué punto está, en vez de deducirlo
# mirando columnas.
#
# Para agregar una:
#   1. escribe la función que aplica el cambio, con sus guardas (la base puede venir de
#      cualquier versión anterior: pregunte antes de tocar);
#   2. súmala al final de la lista con el número siguiente.
#
# Nunca se edita una migración ya publicada: las bases que ya la corrieron no la vuelven
# a correr, así que cambiarla sólo crearía dos historias distintas del mismo número.

def _migracion_1_esquema_base(db: DB) -> list[str]:
    """Migración 1: las tablas e índices del esquema, tal como están en schema.sql."""
    ddl = SCHEMA.read_text(encoding="utf-8")
    aplicadas = []
    for fragmento in ddl.split(";"):
        sentencia = "\n".join(
            linea for linea in fragmento.splitlines() if not linea.strip().startswith("--")
        ).strip()
        if not sentencia or set(sentencia) <= {"-", "\n"}:
            continue
        db.ejecutar(sentencia)
        aplicadas.append(" ".join(sentencia.split()[:3]))
    return aplicadas


def _migracion_2_documentos_integridad(db: DB) -> list[str]:
    """Migración 2: hash y tamaño de cada documento, para acreditar que no cambió.

    El hash se calcula al registrar el documento y se vuelve a calcular cuando alguien
    pide verificarlo: si no calza, el archivo cambió después de incorporarse al
    expediente — que es justo lo que hay que poder demostrar.
    """
    if "documentos" not in db.tablas():
        return []
    aplicadas = []
    for columna, tipo in (("hash_sha256", "TEXT"), ("bytes", "INTEGER")):
        if columna not in db.columnas("documentos"):
            db.ejecutar(f"ALTER TABLE documentos ADD COLUMN {columna} {tipo}")
            aplicadas.append(f"ALTER documentos.{columna}")
    return aplicadas


def _migracion_3_segundo_factor(db: DB) -> list[str]:
    """Migración 3: el segundo factor de cada usuario (secreto TOTP y si está activo).

    El secreto se guarda por usuario y no se expone nunca por herramienta: se entrega una
    vez, cuando se enrola, para cargarlo en la app de autenticación.
    """
    if "usuarios" not in db.tablas():
        return []
    aplicadas = []
    for columna, definicion in (
        ("totp_secret", "TEXT"),
        ("totp_activo", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if columna not in db.columnas("usuarios"):
            db.ejecutar(f"ALTER TABLE usuarios ADD COLUMN {columna} {definicion}")
            aplicadas.append(f"ALTER usuarios.{columna}")
    return aplicadas


def _migracion_4_retencion(db: DB) -> list[str]:
    """Migración 4: la política de retención que declara el estudio.

    Los plazos no se inventan en el código: viven en la base, con su motivo y su fecha,
    para que dentro de unos años se pueda saber con qué criterio se anonimizó algo.
    """
    db.ejecutar(
        "CREATE TABLE IF NOT EXISTS retencion_politica ("
        " tipo TEXT PRIMARY KEY,"
        " meses INTEGER NOT NULL,"
        " motivo TEXT,"
        " actualizado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    return ["CREATE retencion_politica"]


def _migracion_5_notificaciones(db: DB) -> list[str]:
    """Migración 5: la cola de avisos (correo y SMS) y el teléfono de cada usuario.

    Los avisos NO se mandan en el momento en que se crea un plazo: se encolan. Así el CRM
    nunca se queda esperando a un servidor de correo, un aviso que falla se puede
    reintentar sin perderlo, y queda registro de qué se avisó, a quién, cuándo y por qué
    canal — que es lo que después permite decir «sí, se le avisó el día 12».

    La columna `clave` es la que evita mandar el mismo recordatorio dos veces: es única, y
    el envío la usa como huella (por ejemplo `plazo:12:email:2026-09-20`).
    """
    aplicadas = []
    for tabla, columna, definicion in (
        ("usuarios", "telefono", "TEXT"),
        # Para avisar de una audiencia hay que saber de quién es: si no, el aviso va a
        # todos los abogados del estudio y el ruido termina tapando lo importante.
        ("audiencias", "responsable_id", "INTEGER REFERENCES usuarios(id)"),
    ):
        if tabla in db.tablas() and columna not in db.columnas(tabla):
            db.ejecutar(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
            aplicadas.append(f"ALTER {tabla}.{columna}")
    db.ejecutar(
        "CREATE TABLE IF NOT EXISTS notificaciones ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " estudio_id INTEGER NOT NULL REFERENCES estudios(id) ON DELETE CASCADE,"
        " usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,"
        " causa_id INTEGER REFERENCES causas(id) ON DELETE SET NULL,"
        " plazo_id INTEGER REFERENCES plazos(id) ON DELETE SET NULL,"
        " audiencia_id INTEGER REFERENCES audiencias(id) ON DELETE SET NULL,"
        " canal TEXT NOT NULL,"
        " destino TEXT NOT NULL,"
        " asunto TEXT,"
        " cuerpo TEXT NOT NULL,"
        " prioridad TEXT NOT NULL DEFAULT 'normal',"
        " programada_para TEXT,"
        " estado TEXT NOT NULL DEFAULT 'pendiente',"
        " intentos INTEGER NOT NULL DEFAULT 0,"
        " ultimo_error TEXT,"
        " creada_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " enviada_en TEXT,"
        " clave TEXT UNIQUE)"
    )
    db.ejecutar("CREATE INDEX IF NOT EXISTS idx_notificaciones_cola ON notificaciones (estado, programada_para)")
    aplicadas.append("CREATE notificaciones")
    return aplicadas


MIGRACIONES: list[tuple[int, str, Callable[[DB], list[str]]]] = [
    (1, "esquema_base", _migracion_1_esquema_base),
    (2, "documentos_integridad", _migracion_2_documentos_integridad),
    (3, "segundo_factor", _migracion_3_segundo_factor),
    (4, "retencion", _migracion_4_retencion),
    (5, "notificaciones", _migracion_5_notificaciones),
]
