"""Operaciones de negocio: clientes, causas, equipo, plazos, audiencias y panel.

Cada funcion que escribe pasa por `auth.exigir` y deja rastro en `auditoria`.
"""
from __future__ import annotations

import datetime as dt

from . import auth, plazos
from .db import DB


# ------------------------------------------------------------------- clientes
def crear_cliente(db: DB, usuario: dict, nombre: str, rut: str | None = None, **extra) -> int:
    auth.exigir(db, usuario, "cliente.editar")
    datos = {
        "estudio_id": usuario["estudio_id"],
        "nombre": nombre,
        "rut": rut,
        "tipo_persona": extra.get("tipo_persona", "natural"),
        "email": extra.get("email"),
        "telefono": extra.get("telefono"),
        "direccion": extra.get("direccion"),
    }
    cliente_id = db.insertar("clientes", datos)
    auth.auditar(db, usuario["estudio_id"], usuario["id"], "cliente.crear", "clientes", cliente_id, nombre)
    return cliente_id


# --------------------------------------------------------------------- causas
def crear_causa(
    db: DB,
    usuario: dict,
    caratula: str,
    cliente_id: int | None = None,
    rol_rit: str | None = None,
    tribunal: str | None = None,
    materia: str | None = None,
    **extra,
) -> int:
    auth.exigir(db, usuario, "causa.crear")
    causa_id = db.insertar(
        "causas",
        {
            "estudio_id": usuario["estudio_id"],
            "cliente_id": cliente_id,
            "rol_rit": rol_rit,
            "caratula": caratula,
            "tribunal": tribunal,
            "materia": materia,
            "estado_procesal": extra.get("estado_procesal", "tramitacion"),
            "contraparte": extra.get("contraparte"),
            "cuantia_clp": extra.get("cuantia_clp"),
            "observaciones": extra.get("observaciones"),
        },
    )
    auth.auditar(db, usuario["estudio_id"], usuario["id"], "causa.crear", "causas", causa_id, caratula)
    # Quien crea la causa queda en su equipo como responsable (no pasa por
    # `asignar`, que es facultad exclusiva del socio).
    if usuario["rol"] in ("socio", "abogado", "paralegal"):
        equipo_id = db.insertar(
            "causa_equipo",
            {"causa_id": causa_id, "usuario_id": usuario["id"], "rol_en_causa": "responsable"},
        )
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "causa.asignar", "causa_equipo", equipo_id,
            f"causa={causa_id} usuario={usuario['id']} rol=responsable (al crear)",
        )
    return causa_id


def asignar(db: DB, usuario: dict, causa_id: int, usuario_id: int, rol_en_causa: str = "colaborador") -> int:
    auth.exigir(db, usuario, "causa.asignar", causa_id)
    equipo_id = db.insertar(
        "causa_equipo",
        {"causa_id": causa_id, "usuario_id": usuario_id, "rol_en_causa": rol_en_causa},
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "causa.asignar", "causa_equipo", equipo_id,
        f"causa={causa_id} usuario={usuario_id} rol={rol_en_causa}",
    )
    return equipo_id


def equipo_de_causa(db: DB, usuario: dict, causa_id: int) -> list[dict]:
    auth.exigir(db, usuario, "causa.leer", causa_id)
    return db.todos(
        "SELECT e.*, u.nombre, u.rol FROM causa_equipo e JOIN usuarios u ON u.id = e.usuario_id "
        "WHERE e.causa_id = ? ORDER BY e.id",
        (causa_id,),
    )


# --------------------------------------------------------------------- plazos
def crear_plazo(
    db: DB,
    usuario: dict,
    causa_id: int,
    descripcion: str,
    dias: int | None = None,
    fecha_notificacion: str | None = None,
    tipo: str = "judicial",
    es_fatal: bool = True,
    responsable_id: int | None = None,
) -> dict:
    auth.exigir(db, usuario, "plazo.crear", causa_id)
    fecha_vencimiento = None
    calculo = None
    if dias and fecha_notificacion:
        notificacion = dt.date.fromisoformat(fecha_notificacion)
        calculo = plazos.vencimiento(notificacion, dias)
        fecha_vencimiento = calculo["fecha_vencimiento"]
    plazo_id = db.insertar(
        "plazos",
        {
            "causa_id": causa_id,
            "descripcion": descripcion,
            "tipo": tipo,
            "fecha_notificacion": fecha_notificacion,
            "dias": dias,
            "fecha_vencimiento": fecha_vencimiento,
            "es_fatal": 1 if es_fatal else 0,
            "responsable_id": responsable_id or usuario["id"],
        },
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "plazo.crear", "plazos", plazo_id,
        f"{descripcion} vence={fecha_vencimiento}",
    )
    return {"id": plazo_id, "fecha_vencimiento": fecha_vencimiento, "calculo": calculo}


def vencimientos(db: DB, usuario: dict, desde: str | None = None, dias: int = 30) -> list[dict]:
    """Proximos vencimientos de las causas visibles para el usuario."""
    desde = desde or dt.date.today().isoformat()
    hasta = (dt.date.fromisoformat(desde) + dt.timedelta(days=dias)).isoformat()
    visibles = {c["id"] for c in auth.causas_visibles(db, usuario)}
    if not visibles:
        return []
    marcadores = ", ".join("?" for _ in visibles)
    filas = db.todos(
        f"SELECT p.*, c.caratula, c.rol_rit FROM plazos p JOIN causas c ON c.id = p.causa_id "
        f"WHERE p.causa_id IN ({marcadores}) AND p.estado = 'pendiente' "
        f"AND p.fecha_vencimiento IS NOT NULL AND p.fecha_vencimiento BETWEEN ? AND ? "
        f"ORDER BY p.fecha_vencimiento",
        (*sorted(visibles), desde, hasta),
    )
    return filas


def marcar_cumplido(db: DB, usuario: dict, plazo_id: int) -> None:
    plazo = db.uno("SELECT * FROM plazos WHERE id = ?", (plazo_id,))
    if not plazo:
        raise ValueError(f"plazo {plazo_id} no existe")
    auth.exigir(db, usuario, "plazo.cerrar", plazo["causa_id"])
    db.ejecutar("UPDATE plazos SET estado = 'cumplido' WHERE id = ?", (plazo_id,))
    auth.auditar(db, usuario["estudio_id"], usuario["id"], "plazo.cumplido", "plazos", plazo_id)


# ----------------------------------------------------------------- audiencias
def crear_audiencia(
    db: DB, usuario: dict, causa_id: int, tipo: str, fecha: str, hora: str | None = None, **extra
) -> int:
    auth.exigir(db, usuario, "audiencia.crear", causa_id)
    audiencia_id = db.insertar(
        "audiencias",
        {
            "causa_id": causa_id,
            "tipo": tipo,
            "fecha": fecha,
            "hora": hora,
            "modalidad": extra.get("modalidad", "presencial"),
            "lugar_o_url": extra.get("lugar_o_url"),
            "minuta": extra.get("minuta"),
        },
    )
    auth.auditar(db, usuario["estudio_id"], usuario["id"], "audiencia.crear", "audiencias", audiencia_id)
    return audiencia_id


def agenda(db: DB, usuario: dict, desde: str | None = None, dias: int = 30) -> list[dict]:
    desde = desde or dt.date.today().isoformat()
    hasta = (dt.date.fromisoformat(desde) + dt.timedelta(days=dias)).isoformat()
    visibles = {c["id"] for c in auth.causas_visibles(db, usuario)}
    if not visibles:
        return []
    marcadores = ", ".join("?" for _ in visibles)
    return db.todos(
        f"SELECT a.*, c.caratula FROM audiencias a JOIN causas c ON c.id = a.causa_id "
        f"WHERE a.causa_id IN ({marcadores}) AND a.fecha BETWEEN ? AND ? ORDER BY a.fecha, a.hora",
        (*sorted(visibles), desde, hasta),
    )


# ---------------------------------------------------------------------- panel
def panel(db: DB, usuario: dict) -> dict:
    """KPIs para el socio: carga por abogado, causas por estado y vencimientos proximos."""
    auth.exigir(db, usuario, "reporte.panel")
    estudio = usuario["estudio_id"]
    causas_por_estado = db.todos(
        "SELECT estado_procesal, COUNT(*) AS total FROM causas WHERE estudio_id = ? "
        "GROUP BY estado_procesal ORDER BY total DESC",
        (estudio,),
    )
    carga_por_abogado = db.todos(
        "SELECT u.nombre, u.rol, COUNT(e.causa_id) AS causas FROM usuarios u "
        "LEFT JOIN causa_equipo e ON e.usuario_id = u.id AND e.hasta IS NULL "
        "WHERE u.estudio_id = ? AND u.activo = 1 AND u.rol IN ('abogado','socio','paralegal') "
        "GROUP BY u.id, u.nombre, u.rol ORDER BY causas DESC",
        (estudio,),
    )
    pendientes = db.uno(
        "SELECT COUNT(*) AS total FROM plazos p JOIN causas c ON c.id = p.causa_id "
        "WHERE c.estudio_id = ? AND p.estado = 'pendiente'",
        (estudio,),
    )
    return {
        "causas_por_estado": causas_por_estado,
        "carga_por_abogado": carga_por_abogado,
        "plazos_pendientes": pendientes["total"] if pendientes else 0,
    }
