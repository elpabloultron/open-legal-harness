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

    # ---------------------------------------------------------------- conexion
    def _conectar(self):
        if self.dialecto == "sqlite":
            ruta = self.url.replace("sqlite:///", "", 1)
            if ruta not in (":memory:", ""):
                pathlib.Path(ruta).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(ruta)
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
    def columnas(self, tabla: str) -> set[str]:
        if self.dialecto == "sqlite":
            return {f["name"] for f in self.todos(f"PRAGMA table_info({tabla})")}
        filas = self.todos(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (tabla,)
        )
        return {f["column_name"] for f in filas}

    def migrar(self) -> list[str]:
        """Crea tablas e indices y agrega las columnas nuevas. Idempotente."""
        ddl = SCHEMA.read_text(encoding="utf-8")
        sentencias = [s.strip() for s in ddl.split(";") if s.strip() and not set(s.strip()) <= {"-", "\n"}]
        aplicadas = []
        for sentencia in sentencias:
            if sentencia.startswith("--"):
                sentencia = "\n".join(
                    linea for linea in sentencia.splitlines() if not linea.strip().startswith("--")
                ).strip()
            if not sentencia:
                continue
            self.ejecutar(sentencia)
            encabezado = " ".join(sentencia.split()[:3])
            aplicadas.append(encabezado)

        for tabla, columnas in COLUMNAS_NUEVAS.items():
            existentes = self.columnas(tabla)
            for nombre, definicion in columnas.items():
                if nombre not in existentes:
                    self.ejecutar(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
                    aplicadas.append(f"ALTER {tabla}.{nombre}")
        return aplicadas

    def cerrar(self) -> None:
        self.conn.close()
