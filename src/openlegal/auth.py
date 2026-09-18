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

from . import seguridad
from .db import DB

ROLES = ("socio", "administrador", "abogado", "paralegal", "administrativo", "cliente")

# Permisos por rol. `todas` significa "todas las causas del estudio";
# sin `todas`, solo las causas en las que el usuario esta en causa_equipo.
PERMISOS: dict[str, set[str]] = {
    "socio": {
        "causa.leer.todas", "causa.crear", "causa.editar", "causa.asignar",
        "cliente.leer", "cliente.editar", "plazo.leer", "plazo.crear", "plazo.cerrar", "plazo.editar",
        "audiencia.leer", "audiencia.crear", "audiencia.editar", "documento.leer", "documento.crear",
        "documento.publicar", "honorario.leer", "honorario.editar", "gasto.leer",
        "gasto.editar", "usuario.gestionar", "auditoria.leer", "reporte.panel",
        "honorario.leer.todos", "ia.autorizar", "ia.enviar", "ia.leer",
        "titular.gestionar",
    },
    # Administrador del sistema: gestiona usuarios y ve todo el estudio para
    # operar el CRM (agenda, plazos, clientes), pero no toca la redacción ni las
    # finanzas. La secretaria que además factura es rol `administrativo`.
    "administrador": {
        "causa.leer", "causa.leer.todas", "causa.crear", "causa.asignar", "cliente.leer",
        "cliente.editar", "plazo.leer", "plazo.crear", "plazo.editar", "audiencia.leer", "audiencia.crear",
        "audiencia.editar", "documento.leer", "usuario.gestionar", "auditoria.leer", "reporte.panel",
    },
    "abogado": {
        "causa.leer", "causa.crear", "causa.editar", "cliente.leer", "cliente.editar",
        "plazo.leer", "plazo.crear", "plazo.cerrar", "plazo.editar", "audiencia.leer", "audiencia.crear",
        "audiencia.editar", "documento.leer", "documento.crear", "documento.publicar", "gasto.leer",
        "honorario.leer", "reporte.panel", "ia.autorizar", "ia.enviar", "ia.leer",
        "titular.gestionar",
    },
    "paralegal": {
        "causa.leer", "cliente.leer", "plazo.leer", "plazo.crear", "plazo.editar", "audiencia.leer",
        "audiencia.crear", "audiencia.editar", "documento.leer", "documento.crear", "gasto.leer",
    },
    "administrativo": {
        # Ve todas las causas del estudio para poder facturar, pero sin documentos
        # ni redaccion: el acceso sustantivo sigue siendo por asignacion.
        "causa.leer", "causa.leer.todas", "cliente.leer", "cliente.editar", "plazo.leer",
        "audiencia.leer", "audiencia.crear", "audiencia.editar", "honorario.leer", "honorario.editar", "gasto.leer",
        "gasto.editar", "reporte.panel",
    },
    # El cliente solo ve su causa, su estado, sus audiencias y lo publicado.
    "cliente": {"causa.leer.propia", "plazo.leer", "audiencia.leer", "documento.leer.cliente"},
}


class ErrorPermiso(PermissionError):
    pass


class ErrorSegundoFactor(ValueError):
    """La contraseña está bien, pero falta el código o no sirve.

    Se distingue de un fallo de credenciales a propósito: no es lo mismo "esta persona no
    probó bien su contraseña" que "esta persona sabe la contraseña y no tiene el segundo
    factor". Lo segundo, en un CRM con expedientes de terceros, es una alarma.
    """


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


def autenticar(db: DB, email: str, password: str, codigo: str | None = None) -> dict | None:
    """Verifica credenciales y, si el usuario tiene segundo factor, también el código.

    Falla cerrado: si el segundo factor está activo y no llega un código válido, no hay
    sesión — ni siquiera con la contraseña correcta.
    """
    usuario = db.uno("SELECT * FROM usuarios WHERE email = ? AND activo = 1", (email.lower(),))
    if not usuario or not usuario["password_hash"]:
        # El intento fallido deja rastro aunque la cuenta no exista: es la mitad de la
        # alerta de accesos anómalos (nadie debería poder probar contraseñas en silencio).
        auditar(db, None, None, "login.fallido", "usuarios", None, f"{email.lower()} · cuenta inexistente o inactiva")
        return None
    if not verificar_password(password, usuario["password_hash"]):
        auditar(
            db, usuario["estudio_id"], usuario["id"], "login.fallido", "usuarios", usuario["id"],
            f"{email.lower()} · contraseña incorrecta",
        )
        return None
    if usuario.get("totp_activo"):
        if not seguridad.codigo_valido(usuario.get("totp_secret"), codigo):
            auditar(
                db, usuario["estudio_id"], usuario["id"], "login.2fa.fallido", "usuarios",
                usuario["id"], f"{email.lower()} · contraseña correcta, código ausente o inválido",
            )
            raise ErrorSegundoFactor("código de verificación incorrecto o vencido")
    token = secrets.token_urlsafe(32)
    expira = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat()
    db.insertar("sesiones", {"usuario_id": usuario["id"], "token": token, "expira_en": expira})
    usuario.pop("password_hash", None)
    usuario["token"] = token
    return usuario


# ------------------------------------------------------------- segundo factor
def activar_segundo_factor(db: DB, actor: dict, email: str) -> dict:
    """Enrola el segundo factor de un usuario y devuelve su secreto, una sola vez.

    El secreto se entrega acá para cargarlo en la app de autenticación. No vuelve a
    mostrarse en ninguna consulta: si se pierde el teléfono, se enrola de nuevo.
    """
    exigir(db, actor, "usuario.gestionar")
    fila = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (actor["estudio_id"], email.lower())
    )
    if not fila:
        raise ValueError(f"no existe el usuario {email} en el estudio {actor['estudio_id']}")
    secreto = seguridad.nuevo_secreto()
    db.ejecutar("UPDATE usuarios SET totp_secret = ?, totp_activo = 1 WHERE id = ?", (secreto, fila["id"]))
    auditar(
        db, actor["estudio_id"], actor["id"], "usuario.2fa.activar", "usuarios", fila["id"],
        f"{email.lower()} por {actor['email']}",
    )
    return {
        "email": email.lower(),
        "secreto": secreto,
        "uri": seguridad.uri_otpauth(secreto, email.lower()),
        "digitos": seguridad.DIGITOS,
        "periodo_segundos": seguridad.PERIODO,
    }


def desactivar_segundo_factor(db: DB, actor: dict, email: str, motivo: str) -> None:
    """Apaga el segundo factor de un usuario. Exige motivo: queda en la bitácora."""
    exigir(db, actor, "usuario.gestionar")
    if not motivo or not motivo.strip():
        raise ValueError("falta el motivo: apagar el segundo factor tiene que ser explicable")
    fila = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (actor["estudio_id"], email.lower())
    )
    if not fila:
        raise ValueError(f"no existe el usuario {email} en el estudio {actor['estudio_id']}")
    db.ejecutar("UPDATE usuarios SET totp_secret = NULL, totp_activo = 0 WHERE id = ?", (fila["id"],))
    auditar(
        db, actor["estudio_id"], actor["id"], "usuario.2fa.desactivar", "usuarios", fila["id"],
        f"{email.lower()} · motivo: {motivo.strip()}",
    )


def estado_segundo_factor(db: DB, actor: dict) -> list[dict]:
    """Quién tiene segundo factor en el estudio. Para el panel de seguridad."""
    exigir(db, actor, "usuario.gestionar")
    filas = db.todos(
        "SELECT u.email, u.nombre, u.rol, u.activo, u.totp_activo FROM usuarios u "
        "WHERE u.estudio_id = ? ORDER BY u.rol, u.email",
        (actor["estudio_id"],),
    )
    return [
        {**dict(fila), "esperado": fila["rol"] in ("socio", "administrador")}
        for fila in filas
    ]


def usuario_por_token(db: DB, token: str) -> dict | None:
    fila = db.uno(
        "SELECT u.* FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id "
        "WHERE s.token = ? AND u.activo = 1",
        (token,),
    )
    if fila:
        fila.pop("password_hash", None)
    return fila


def cerrar_sesion(db: DB, token: str) -> None:
    """Invalida el token: lo borra de la tabla de sesiones."""
    db.ejecutar("DELETE FROM sesiones WHERE token = ?", (token,))


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
