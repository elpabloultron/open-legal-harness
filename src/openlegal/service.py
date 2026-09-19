"""Operaciones de negocio: clientes, causas, equipo, plazos, audiencias y panel.

Cada funcion que escribe pasa por `auth.exigir` y deja rastro en `auditoria`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import pathlib

from . import auth, ia, notificaciones, plazos
from .db import DB


# ------------------------------------------------------------------- clientes
def crear_cliente(db: DB, usuario: dict, nombre: str, rut: str | None = None, **extra) -> int:
    auth.exigir(db, usuario, "cliente.editar")
    datos = {
        "estudio_id": usuario["estudio_id"],
        "nombre": nombre,
        "rut": rut,
        "tipo_persona": extra.get("tipo_persona", "natural"),
        "representante_legal": extra.get("representante_legal"),
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
    sabado_habil: bool = True,
) -> dict:
    auth.exigir(db, usuario, "plazo.crear", causa_id)
    fecha_vencimiento = None
    calculo = None
    if dias and fecha_notificacion:
        notificacion = dt.date.fromisoformat(fecha_notificacion)
        calculo = plazos.vencimiento(notificacion, dias, sabado_habil=sabado_habil)
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
    notificaciones.avisar_asignacion(
        db, usuario, "plazo", plazo_id, descripcion,
        causa_id=causa_id, responsable_id=responsable_id or usuario["id"],
        cuando=f"Vence el {fecha_vencimiento}" if fecha_vencimiento else None,
        plazo_id=plazo_id,
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


def actualizar_plazo(
    db: DB,
    usuario: dict,
    plazo_id: int,
    descripcion: str | None = None,
    dias: int | None = None,
    fecha_notificacion: str | None = None,
    es_fatal: bool | None = None,
    motivo: str | None = None,
    sabado_habil: bool = True,
) -> dict:
    """Corrige un plazo y recalcula su vencimiento con el Art. 66 CPC.

    El motivo es obligatorio en la practica: queda en la bitacora junto a los
    campos que cambiaron, para que un plazo fatal rectificado sea explicable
    despues (quien lo cambio, cuando y por que).
    """
    plazo = db.uno("SELECT * FROM plazos WHERE id = ?", (plazo_id,))
    if not plazo:
        raise ValueError(f"plazo {plazo_id} no existe")
    auth.exigir(db, usuario, "plazo.editar", plazo["causa_id"])

    campos: dict = {}
    if descripcion is not None:
        campos["descripcion"] = descripcion
    if es_fatal is not None:
        campos["es_fatal"] = 1 if es_fatal else 0
    if dias is not None:
        campos["dias"] = dias
    if fecha_notificacion is not None:
        campos["fecha_notificacion"] = fecha_notificacion
    if not campos:
        raise ValueError("no se indico ningun campo que actualizar")

    calculo = None
    nuevos_dias = campos.get("dias", plazo["dias"])
    nueva_notificacion = campos.get("fecha_notificacion", plazo["fecha_notificacion"])
    if nuevos_dias and nueva_notificacion:
        calculo = plazos.vencimiento(
            dt.date.fromisoformat(nueva_notificacion), int(nuevos_dias), sabado_habil=sabado_habil
        )
        campos["fecha_vencimiento"] = calculo["fecha_vencimiento"]

    asignaciones = ", ".join(f"{col} = ?" for col in campos)
    db.ejecutar(f"UPDATE plazos SET {asignaciones} WHERE id = ?", (*campos.values(), plazo_id))
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "plazo.editar", "plazos", plazo_id,
        f"motivo: {motivo or 'no indicado'} | campos: {sorted(campos)}",
    )
    return {
        "id": plazo_id,
        "campos_actualizados": sorted(campos),
        "fecha_vencimiento": campos.get("fecha_vencimiento", plazo["fecha_vencimiento"]),
        "calculo": calculo,
    }


def cancelar_plazo(db: DB, usuario: dict, plazo_id: int, motivo: str) -> None:
    """Deja un plazo sin efecto sin borrarlo.

    No se elimina la fila a proposito: un plazo fatal que se creo mal y despues
    se corrige tiene que quedar rastro, o el expediente no es auditable.
    """
    plazo = db.uno("SELECT * FROM plazos WHERE id = ?", (plazo_id,))
    if not plazo:
        raise ValueError(f"plazo {plazo_id} no existe")
    auth.exigir(db, usuario, "plazo.editar", plazo["causa_id"])
    db.ejecutar("UPDATE plazos SET estado = 'cancelado' WHERE id = ?", (plazo_id,))
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "plazo.cancelar", "plazos", plazo_id,
        f"motivo: {motivo}",
    )


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
            "responsable_id": extra.get("responsable_id"),
        },
    )
    cuando = f"{tipo} del {fecha} {hora or ''}".strip()
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "audiencia.crear", "audiencias", audiencia_id,
        f"{cuando} (causa {causa_id})",
    )
    notificaciones.avisar_asignacion(
        db, usuario, "audiencia", audiencia_id, f"{tipo} del {fecha}",
        causa_id=causa_id, responsable_id=extra.get("responsable_id"),
        cuando=f"{fecha} {hora or ''}".strip(), audiencia_id=audiencia_id,
    )
    return audiencia_id


def actualizar_audiencia(
    db: DB,
    usuario: dict,
    audiencia_id: int,
    tipo: str | None = None,
    fecha: str | None = None,
    hora: str | None = None,
    modalidad: str | None = None,
    lugar_o_url: str | None = None,
    minuta: str | None = None,
    motivo: str | None = None,
) -> dict:
    """Corrige una audiencia existente (fecha, hora, modalidad, lugar, minuta).

    Mismo criterio que los plazos: una audiencia mal cargada se rectifica, no se
    duplica ni se borra. El motivo queda en la bitácora.
    """
    audiencia = db.uno("SELECT * FROM audiencias WHERE id = ?", (audiencia_id,))
    if not audiencia:
        raise ValueError(f"audiencia {audiencia_id} no existe")
    auth.exigir(db, usuario, "audiencia.editar", audiencia["causa_id"])

    campos: dict = {}
    for columna, valor in (
        ("tipo", tipo), ("fecha", fecha), ("hora", hora),
        ("modalidad", modalidad), ("lugar_o_url", lugar_o_url), ("minuta", minuta),
    ):
        if valor is not None:
            campos[columna] = valor
    if not campos:
        raise ValueError("no se indico ningun campo que actualizar")

    asignaciones = ", ".join(f"{col} = ?" for col in campos)
    db.ejecutar(f"UPDATE audiencias SET {asignaciones} WHERE id = ?", (*campos.values(), audiencia_id))
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "audiencia.editar", "audiencias", audiencia_id,
        f"motivo: {motivo or 'no indicado'} | campos: {sorted(campos)}",
    )
    return {"id": audiencia_id, "campos_actualizados": sorted(campos)}


def cancelar_audiencia(db: DB, usuario: dict, audiencia_id: int, motivo: str) -> None:
    """Deja una audiencia sin efecto sin borrarla (p. ej. un duplicado)."""
    audiencia = db.uno("SELECT * FROM audiencias WHERE id = ?", (audiencia_id,))
    if not audiencia:
        raise ValueError(f"audiencia {audiencia_id} no existe")
    auth.exigir(db, usuario, "audiencia.editar", audiencia["causa_id"])
    db.ejecutar("UPDATE audiencias SET estado = 'cancelada' WHERE id = ?", (audiencia_id,))
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "audiencia.cancelar", "audiencias", audiencia_id,
        f"motivo: {motivo}",
    )


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


# ------------------------------------------------------- IA y transferencias
def autorizar_ia(
    db: DB,
    usuario: dict,
    causa_id: int,
    alcance: str = "analisis",
    titular: str | None = None,
    base_licitud: str | None = None,
) -> int:
    """Deja registrado que esta causa puede tratarse con IA, hasta que se revoque."""
    auth.exigir(db, usuario, "ia.autorizar", causa_id)
    if alcance not in ("analisis", "redaccion", "ambos"):
        raise ValueError("alcance debe ser analisis, redaccion o ambos")
    autorizacion_id = db.insertar(
        "autorizaciones_ia",
        {
            "causa_id": causa_id,
            "alcance": alcance,
            "titular": titular,
            "registrado_por": usuario["id"],
            **({"base_licitud": base_licitud} if base_licitud else {}),
        },
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "ia.autorizar", "autorizaciones_ia",
        autorizacion_id, f"causa={causa_id} alcance={alcance} titular={titular or 's/informar'}",
    )
    return autorizacion_id


def revocar_ia(db: DB, usuario: dict, causa_id: int) -> int:
    """Revoca las autorizaciones vigentes de una causa (el titular puede arrepentirse)."""
    auth.exigir(db, usuario, "ia.autorizar", causa_id)
    vigentes = db.todos(
        "SELECT id FROM autorizaciones_ia WHERE causa_id = ? AND vigente = 1", (causa_id,)
    )
    for fila in vigentes:
        db.ejecutar(
            "UPDATE autorizaciones_ia SET vigente = 0, revocada_en = CURRENT_TIMESTAMP WHERE id = ?",
            (fila["id"],),
        )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "ia.revocar", "autorizaciones_ia",
        causa_id, f"{len(vigentes)} autorizacion(es) revocada(s)",
    )
    return len(vigentes)


def estado_ia(db: DB, usuario: dict, causa_id: int) -> dict:
    auth.exigir(db, usuario, "ia.leer", causa_id)
    vigente = db.uno(
        "SELECT * FROM autorizaciones_ia WHERE causa_id = ? AND vigente = 1 ORDER BY id DESC",
        (causa_id,),
    )
    enviadas = db.uno(
        "SELECT COUNT(*) AS total FROM transferencias_ia WHERE causa_id = ?", (causa_id,)
    )
    return {
        "causa_id": causa_id,
        "autorizado": bool(vigente),
        "autorizacion": dict(vigente) if vigente else None,
        "transferencias": enviadas["total"] if enviadas else 0,
        "terminos_a_minimizar": ia.terminos_de_causa(db, causa_id),
    }


def registrar_transferencia(
    db: DB,
    usuario: dict,
    causa_id: int,
    proveedor: str,
    payload: str,
    modelo: str | None = None,
    documentos: str | None = None,
    redactado: bool = False,
) -> dict:
    """Registra que un texto de la causa salió hacia un proveedor de IA.

    Exige autorización vigente: sin ella, se niega y queda el intento en la bitácora.
    Guarda hash y tamaño, no el contenido — así se puede demostrar qué salió sin
    duplicar el expediente dentro de la base.
    """
    auth.exigir(db, usuario, "ia.enviar", causa_id)
    if proveedor not in ia.PROVEEDORES:
        raise ValueError(f"proveedor desconocido: {proveedor} (ver openlegal ia proveedores)")
    autorizacion = db.uno(
        "SELECT * FROM autorizaciones_ia WHERE causa_id = ? AND vigente = 1 ORDER BY id DESC",
        (causa_id,),
    )
    if not autorizacion:
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "ia.sin_autorizacion", "transferencias_ia",
            causa_id, f"proveedor={proveedor} bloqueado",
        )
        raise auth.ErrorPermiso(
            f"la causa {causa_id} no tiene autorización vigente para tratarse con IA; "
            f"regístrala con `openlegal ia autorizar --causa {causa_id} --titular \"...\"`"
        )
    resumen = db.uno("SELECT caratula FROM causas WHERE id = ?", (causa_id,))
    transferencia_id = db.insertar(
        "transferencias_ia",
        {
            "causa_id": causa_id,
            "autorizacion_id": autorizacion["id"],
            "usuario_id": usuario["id"],
            "proveedor": proveedor,
            "modelo": modelo,
            "destino_pais": ia.PROVEEDORES[proveedor]["pais"],
            "documentos": documentos or (resumen["caratula"] if resumen else None),
            "caracteres": len(payload),
            "hash_payload": ia.hash_payload(payload),
            "redactado": 1 if redactado else 0,
        },
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "ia.comunicar", "transferencias_ia",
        transferencia_id,
        f"causa={causa_id} proveedor={proveedor} modelo={modelo or 's/i'} "
        f"caracteres={len(payload)} redactado={'si' if redactado else 'no'}",
    )
    return {
        "id": transferencia_id,
        "proveedor": proveedor,
        "destino_pais": ia.PROVEEDORES[proveedor]["pais"],
        "hash_payload": ia.hash_payload(payload),
        "caracteres": len(payload),
        "redactado": redactado,
    }


def transferencias_ia(db: DB, usuario: dict, causa_id: int | None = None) -> list[dict]:
    auth.exigir(db, usuario, "ia.leer")
    if causa_id is not None:
        auth.exigir(db, usuario, "ia.leer", causa_id)
        return db.todos(
            "SELECT t.*, u.nombre AS usuario FROM transferencias_ia t "
            "LEFT JOIN usuarios u ON u.id = t.usuario_id WHERE t.causa_id = ? ORDER BY t.id DESC",
            (causa_id,),
        )
    visibles = [c["id"] for c in auth.causas_visibles(db, usuario)]
    if not visibles:
        return []
    marcadores = ", ".join("?" for _ in visibles)
    return db.todos(
        f"SELECT t.*, u.nombre AS usuario, c.caratula FROM transferencias_ia t "
        f"LEFT JOIN usuarios u ON u.id = t.usuario_id LEFT JOIN causas c ON c.id = t.causa_id "
        f"WHERE t.causa_id IN ({marcadores}) ORDER BY t.id DESC LIMIT 50",
        tuple(sorted(visibles)),
    )


# ---------------------------------------------------------------- documentos
def registrar_documento(
    db: DB,
    usuario: dict,
    causa_id: int,
    nombre: str,
    ruta: str | None = None,
    tipo: str | None = None,
    visibilidad: str = "interno",
) -> int:
    """Registra un documento del expediente y le calcula el hash de integridad.

    El hash se guarda al momento de incorporarlo: es lo que después permite demostrar
    que el escrito que está en el archivo es el mismo que se incorporó. Si el archivo no
    está (se registra la referencia y el papel se guarda aparte), queda sin hash y se
    dice, en vez de dejar un campo vacío que parezca verificado.
    """
    auth.exigir(db, usuario, "documento.crear", causa_id)
    datos = {
        "causa_id": causa_id,
        "nombre": nombre,
        "ruta": ruta,
        "tipo": tipo,
        "visibilidad": visibilidad,
        "subido_por": usuario["id"],
    }
    if ruta:
        archivo = pathlib.Path(ruta)
        if archivo.is_file():
            contenido = archivo.read_bytes()
            datos["hash_sha256"] = hashlib.sha256(contenido).hexdigest()
            datos["bytes"] = len(contenido)
    documento_id = db.insertar("documentos", datos)
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "documento.registrar", "documentos", documento_id,
        f"{nombre} · sha256={datos.get('hash_sha256', '(sin archivo)')}",
    )
    return documento_id


def verificar_documento(db: DB, usuario: dict, documento_id: int) -> dict:
    """Vuelve a calcular el hash y dice si el archivo cambió desde que se registró."""
    fila = db.uno("SELECT * FROM documentos WHERE id = ?", (documento_id,))
    if not fila:
        raise ValueError(f"no existe el documento {documento_id}")
    auth.exigir(db, usuario, "documento.leer", fila["causa_id"])

    ruta = fila.get("ruta")
    if not ruta or not pathlib.Path(ruta).is_file():
        return {
            "documento_id": documento_id, "nombre": fila["nombre"], "estado": "sin_archivo",
            "detalle": "el registro apunta a un archivo que no está en esta máquina",
        }
    contenido = pathlib.Path(ruta).read_bytes()
    actual = hashlib.sha256(contenido).hexdigest()
    registrado = fila.get("hash_sha256")
    if not registrado:
        return {
            "documento_id": documento_id, "nombre": fila["nombre"], "estado": "sin_hash_registrado",
            "detalle": "se incorporó antes de que el CRM calculara hashes; hay que reincorporarlo "
                       "para poder verificarlo",
        }
    estado = "intacto" if actual == registrado else "cambio"
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], f"documento.verificar.{estado}", "documentos",
        documento_id, f"{fila['nombre']} · sha256={actual}",
    )
    return {
        "documento_id": documento_id, "nombre": fila["nombre"], "estado": estado,
        "hash_registrado": registrado, "hash_actual": actual, "bytes": len(contenido),
        "detalle": "coincide con lo incorporado" if estado == "intacto"
        else "el archivo cambió después de incorporarse al expediente",
    }


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
