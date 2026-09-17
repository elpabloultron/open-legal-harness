"""Usuarios, roles, sesiones y control de acceso por causa.

Reglas de diseño:
- Todo cuelga de un estudio: nada se lee ni se escribe sin `estudio_id`.
- Un usuario no-socio solo ve las causas donde figura en `causa_equipo`.
- El rol `cliente` es de solo lectura y nunca ve lo interno (notas, honorarios,
  documentos marcados como internos).
- Cada accion relevante deja fila en `auditoria`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets

from .db import DB

ROLES = ("socio", "abogado", "paralegal", "administrativo", "cliente")

# Permisos por rol. `todas` significa "todas las causas del estudio";
# sin `todas`, solo las causas en las que el usuario esta en causa_equipo.
PERMISOS: dict[str, set[str]] = {
    "socio": {
        "causa.leer.todas", "causa.crear", "causa.editar", "causa.asignar",
        "cliente.leer", "cliente.editar", "plazo.leer", "plazo.crear", "plazo.cerrar",
        "audiencia.leer", "audiencia.crear", "documento.leer", "documento.crear",
        "documento.publicar", "honorario.leer", "honorario.editar", "gasto.leer",
        "gasto.editar", "usuario.gestionar", "auditoria.leer", "reporte.panel",
        "honorario.leer.todos",
    },
    "abogado": {
        "causa.leer", "causa.crear", "causa.editar", "cliente.leer", "cliente.editar",
        "plazo.leer", "plazo.crear", "plazo.cerrar", "audiencia.leer", "audiencia.crear",
        "documento.leer", "documento.crear", "documento.publicar", "gasto.leer",
        "honorario.leer", "reporte.panel",
    },
    "paralegal": {
        "causa.leer", "cliente.leer", "plazo.leer", "plazo.crear", "audiencia.leer",
        "audiencia.crear", "documento.leer", "documento.crear", "gasto.leer",
    },
    "administrativo": {
        # Ve todas las causas del estudio para poder facturar, pero sin documentos
        # ni redaccion: el acceso sustantivo sigue siendo por asignacion.
        "causa.leer", "causa.leer.todas", "cliente.leer", "cliente.editar", "plazo.leer",
        "audiencia.leer", "audiencia.crear", "honorario.leer", "honorario.editar", "gasto.leer",
        "gasto.editar", "reporte.panel",
    },
    # El cliente solo ve su causa, su estado, sus audiencias y lo publicado.
    "cliente": {"causa.leer.propia", "plazo.leer", "audiencia.leer", "documento.leer.cliente"},
}


class ErrorPermiso(PermissionError):
    pass


# --------------------------------------------------------------------- claves
def hash_password(password: str) -> str:
    sal = os.urandom(16)
    derivada = hashlib.scrypt(password.encode(), salt=sal, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${sal.hex()}${derivada.hex()}"


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        algoritmo, n, r, p, sal_hex, hash_hex = almacenado.split("$")
        if algoritmo != "scrypt":
            return False
        derivada = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(sal_hex), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(derivada.hex(), hash_hex)


# ------------------------------------------------------------------- estudios
def crear_estudio(db: DB, nombre: str, rut: str | None = None, modo: str = "solo") -> int:
    if modo not in ("solo", "oficina"):
        raise ValueError("modo debe ser 'solo' u 'oficina'")
    return db.insertar("estudios", {"nombre": nombre, "rut": rut, "modo": modo})


def crear_usuario(
    db: DB,
    estudio_id: int,
    nombre: str,
    email: str,
    rol: str,
    password: str | None = None,
    actor_id: int | None = None,
) -> int:
    if rol not in ROLES:
        raise ValueError(f"rol invalido: {rol} (validos: {', '.join(ROLES)})")
    usuario_id = db.insertar(
        "usuarios",
        {
            "estudio_id": estudio_id,
            "nombre": nombre,
            "email": email.lower(),
            "rol": rol,
            "password_hash": hash_password(password) if password else None,
        },
    )
    # `actor_id` es quien ejecuta la accion (None cuando lo hace el sistema).
    auditar(db, estudio_id, actor_id, "usuario.crear", "usuarios", usuario_id, f"rol={rol} email={email.lower()}")
    return usuario_id


def autenticar(db: DB, email: str, password: str) -> dict | None:
    usuario = db.uno("SELECT * FROM usuarios WHERE email = ? AND activo = 1", (email.lower(),))
    if not usuario or not usuario["password_hash"]:
        return None
    if not verificar_password(password, usuario["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    expira = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat()
    db.insertar("sesiones", {"usuario_id": usuario["id"], "token": token, "expira_en": expira})
    usuario.pop("password_hash", None)
    usuario["token"] = token
    return usuario


def usuario_por_token(db: DB, token: str) -> dict | None:
    fila = db.uno(
        "SELECT u.* FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id "
        "WHERE s.token = ? AND u.activo = 1",
        (token,),
    )
    if fila:
        fila.pop("password_hash", None)
    return fila


# ------------------------------------------------------------------ permisos
def es_miembro(db: DB, usuario: dict, causa_id: int) -> bool:
    fila = db.uno(
        "SELECT 1 AS ok FROM causa_equipo WHERE causa_id = ? AND usuario_id = ? AND hasta IS NULL",
        (causa_id, usuario["id"]),
    )
    return bool(fila)


def causa_del_estudio(db: DB, usuario: dict, causa_id: int) -> bool:
    fila = db.uno(
        "SELECT 1 AS ok FROM causas WHERE id = ? AND estudio_id = ?",
        (causa_id, usuario["estudio_id"]),
    )
    return bool(fila)


def puede(db: DB, usuario: dict, permiso: str, causa_id: int | None = None) -> bool:
    """Permiso efectivo = rol + pertenencia al estudio + asignacion a la causa."""
    rol = usuario["rol"]
    concedidos = PERMISOS.get(rol, set())
    if rol == "cliente":
        return permiso in concedidos or permiso == "causa.leer.propia"
    if permiso not in concedidos and f"{permiso}.todas" not in concedidos:
        return False
    if causa_id is None:
        return True
    if not causa_del_estudio(db, usuario, causa_id):
        return False
    if f"{permiso}.todas" in concedidos or "causa.leer.todas" in concedidos:
        return True
    return es_miembro(db, usuario, causa_id)


def exigir(db: DB, usuario: dict, permiso: str, causa_id: int | None = None) -> None:
    if not puede(db, usuario, permiso, causa_id):
        auditar(db, usuario["estudio_id"], usuario["id"], "permiso.denegado", permiso, causa_id)
        raise ErrorPermiso(f"{usuario['rol']} no tiene '{permiso}' sobre la causa {causa_id}")


def causas_visibles(db: DB, usuario: dict) -> list[dict]:
    if "causa.leer.todas" in PERMISOS.get(usuario["rol"], set()):
        return db.todos(
            "SELECT * FROM causas WHERE estudio_id = ? ORDER BY id DESC", (usuario["estudio_id"],)
        )
    return db.todos(
        "SELECT c.* FROM causas c JOIN causa_equipo e ON e.causa_id = c.id "
        "WHERE c.estudio_id = ? AND e.usuario_id = ? AND e.hasta IS NULL ORDER BY c.id DESC",
        (usuario["estudio_id"], usuario["id"]),
    )


# ----------------------------------------------------------------- auditoria
def auditar(
    db: DB,
    estudio_id: int | None,
    usuario_id: int | None,
    accion: str,
    entidad: str | None = None,
    entidad_id: int | None = None,
    detalle: str | None = None,
) -> int:
    return db.insertar(
        "auditoria",
        {
            "estudio_id": estudio_id,
            "usuario_id": usuario_id,
            "accion": accion,
            "entidad": entidad,
            "entidad_id": entidad_id,
            "detalle": detalle,
        },
    )
