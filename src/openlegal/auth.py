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
        "titular.gestionar", "aviso.gestionar",
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
        "titular.gestionar", "aviso.gestionar",
    },
    "paralegal": {
        "causa.leer", "cliente.leer", "plazo.leer", "plazo.crear", "plazo.editar", "audiencia.leer",
        "audiencia.crear", "audiencia.editar", "documento.leer", "documento.crear", "gasto.leer",
        "aviso.gestionar",
    },
    "administrativo": {
        # Ve todas las causas del estudio para poder facturar, pero sin documentos
        # ni redaccion: el acceso sustantivo sigue siendo por asignacion.
        "causa.leer", "causa.leer.todas", "cliente.leer", "cliente.editar", "plazo.leer",
        "audiencia.leer", "audiencia.crear", "audiencia.editar", "honorario.leer", "honorario.editar", "gasto.leer",
        "gasto.editar", "reporte.panel", "aviso.gestionar",
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


class ErrorBloqueado(PermissionError):
    """La cuenta está bloqueada por intentos fallidos: no hay sesión, ni con la clave buena.

    Se distingue de `ErrorPermiso` porque no es falta de permisos sino un bloqueo temporal
    que se destraba solo: quien lo lee tiene que saber que el problema es el tiempo, no su
    rol en el estudio.
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


def actualizar_usuario(
    db: DB,
    actor: dict,
    email: str,
    *,
    telefono: str | None = None,
    activo: bool | None = None,
    rol: str | None = None,
    password: str | None = None,
) -> dict:
    """Cambia datos de un usuario del estudio: teléfono, rol, contraseña o si está activo.

    Los datos del personal son datos personales igual que los del cliente: cada cambio va a
    la bitácora con quién lo hizo. Dos cosas no se permiten, porque dejarían al estudio sin
    quien pueda administrarlo: cambiarle el rol al **último socio activo** y desactivarlo.
    """
    exigir(db, actor, "usuario.gestionar")
    usuario = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (actor["estudio_id"], email.lower())
    )
    if not usuario:
        raise ValueError(f"no existe el usuario {email} en el estudio")

    if rol is not None and rol not in ROLES:
        raise ValueError(f"rol invalido: {rol} (validos: {', '.join(ROLES)})")
    pierde_socio = (rol is not None and rol != "socio") or activo is False
    if usuario["rol"] == "socio" and pierde_socio:
        socios = db.todos(
            "SELECT id FROM usuarios WHERE estudio_id = ? AND rol = 'socio' AND activo = 1",
            (actor["estudio_id"],),
        )
        if len(socios) <= 1:
            raise ValueError(
                "no se puede: es el último socio activo del estudio y nadie quedaría con "
                "usuarios.gestionar. Crea otro socio primero."
            )

    cambios: dict = {}
    if telefono is not None:
        cambios["telefono"] = telefono
    if activo is not None:
        cambios["activo"] = 1 if activo else 0
    if rol is not None:
        cambios["rol"] = rol
    if password is not None:
        cambios["password_hash"] = hash_password(password)
    if not cambios:
        raise ValueError("no hay nada que cambiar: indica teléfono, rol, activo o contraseña")

    asignaciones = ", ".join(f"{campo} = ?" for campo in cambios)
    db.ejecutar(
        f"UPDATE usuarios SET {asignaciones} WHERE id = ?", (*cambios.values(), usuario["id"])
    )
    resumen = ", ".join(
        campo if campo != "password_hash" else "contraseña" for campo in cambios
    )
    auditar(
        db, actor["estudio_id"], actor["id"], "usuario.actualizar", "usuarios", usuario["id"],
        f"{email.lower()}: {resumen}",
    )
    fila = db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario["id"],))
    assert fila is not None
    fila.pop("password_hash", None)
    return fila


def preparar_segundo_factor(db: DB, actor: dict, email: str) -> dict:
    """Genera el secreto y lo deja **pendiente**: todavía no exige código para entrar.

    Es la primera mitad del alta desde el panel. Se deja pendiente a propósito: si se activara
    con el secreto recién generado y la persona copió mal la clave en el teléfono, quedaría
    afuera de su propia cuenta. Se activa recién cuando manda un código que sirve.
    """
    exigir(db, actor, "usuario.gestionar")
    fila = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (actor["estudio_id"], email.lower())
    )
    if not fila:
        raise ValueError(f"no existe el usuario {email} en el estudio {actor['estudio_id']}")
    secreto = seguridad.nuevo_secreto()
    db.ejecutar("UPDATE usuarios SET totp_secret = ?, totp_activo = 0 WHERE id = ?", (secreto, fila["id"]))
    auditar(
        db, actor["estudio_id"], actor["id"], "usuario.2fa.preparar", "usuarios", fila["id"],
        f"{email.lower()} por {actor['email']}",
    )
    return {
        "email": email.lower(),
        "secreto": secreto,
        "uri": seguridad.uri_otpauth(secreto, email.lower()),
        "activo": False,
        "aviso": "carga el secreto en la app de autenticación y confirma con un código de 6 dígitos",
    }


def confirmar_segundo_factor(db: DB, actor: dict, email: str, codigo: str | None) -> dict:
    """Activa el segundo factor sólo si el código corresponde al secreto ya cargado."""
    exigir(db, actor, "usuario.gestionar")
    fila = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (actor["estudio_id"], email.lower())
    )
    if not fila:
        raise ValueError(f"no existe el usuario {email} en el estudio {actor['estudio_id']}")
    if not fila.get("totp_secret"):
        raise ValueError("ese usuario no tiene un alta de segundo factor empezada")
    if not seguridad.codigo_valido(fila["totp_secret"], codigo):
        raise ErrorSegundoFactor(
            "el código no coincide con el secreto: revisa que el teléfono tenga la hora automática "
            "y vuelve a probar (el alta queda pendiente, no se activó nada)"
        )
    db.ejecutar("UPDATE usuarios SET totp_activo = 1 WHERE id = ?", (fila["id"],))
    auditar(
        db, actor["estudio_id"], actor["id"], "usuario.2fa.activar", "usuarios", fila["id"],
        f"{email.lower()} por {actor['email']} · confirmado con código",
    )
    return {"email": email.lower(), "activo": True}


def autenticar(db: DB, email: str, password: str, codigo: str | None = None) -> dict | None:
    """Verifica credenciales y, si el usuario tiene segundo factor, también el código.

    Falla cerrado: si el segundo factor está activo y no llega un código válido, no hay
    sesión — ni siquiera con la contraseña correcta. Lo mismo si la cuenta está bloqueada
    por intentos fallidos: el bloqueo vale también para quien acierta la clave, que es lo
    único que lo hace útil contra alguien que prueba contraseñas.
    """
    estado = seguridad.bloqueo(db, email)
    if estado["bloqueada"]:
        # Se anota, pero con una acción distinta: un intento bloqueado no puede sumar al
        # conteo que decide el bloqueo, o la cuenta no se destrabaría nunca sola.
        auditar(
            db, None, None, "login.bloqueado", "usuarios", None,
            f"{email.lower()} · faltan {estado['faltan_minutos']} minuto(s) de bloqueo",
        )
        raise ErrorBloqueado(
            f"cuenta bloqueada por intentos fallidos: vuelve a intentarlo en "
            f"{estado['faltan_minutos']} minuto(s) o pide que la destraben"
        )
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


def desbloquear(db: DB, actor: dict, email: str) -> dict:
    """Destraba una cuenta a mano: la secretaria que se equivocó ocho veces, por ejemplo.

    Queda en la bitácora como `login.desbloqueado` y desde ahí se vuelven a contar los
    fallos — no se borra nada, se marca el corte.
    """
    exigir(db, actor, "usuario.gestionar")
    cuenta = (email or "").strip().lower()
    if not cuenta:
        raise ValueError("indica el correo de la cuenta a destrabar")
    usuario = db.uno("SELECT id FROM usuarios WHERE email = ?", (cuenta,))
    auditar(
        db, actor["estudio_id"], actor["id"], "login.desbloqueado", "usuarios",
        usuario["id"] if usuario else None, f"{cuenta} · destrabada a mano",
    )
    return seguridad.bloqueo(db, cuenta)


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
