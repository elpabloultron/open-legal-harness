"""Servidor MCP del CRM (JSON-RPC 2.0 sobre stdio).

Es el puente que quiere el flujo completo: el agente analiza la causa con las
herramientas de open-legal-chile y **escribe de vuelta en el CRM** — plazos,
audiencias, documentos y el registro de lo que mandó a un modelo.

Sigue el mismo patrón que el `mcp_server.py` de open-legal-chile (stdio, JSON-RPC
2.0 hecho a mano, sin dependencias), así que se configura igual en cualquier
cliente: `dsh`, Claude Code, Cursor, Codex.

    openlegal mcp                     # stdio, sin argumentos extra
    openlegal mcp --usuario ana@estudio.cl

Configuración típica en el harness (capa del perfil):

    - id: mcp-openlegal-crm
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: crm
        transport: stdio
        command: /ruta/.venv/bin/openlegal-crm
        args: [mcp]
        env:
          OPENLEGAL_MCP_DB: sqlite:///home/usuario/.openlegal/demo.db
          OPENLEGAL_MCP_USUARIO: socia@estudio.cl

Los datos del CRM no salen de la máquina salvo por lo que el agente decida
mandar al modelo; ese envío queda en `transferencias_ia` (ver `ia_registrar`).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any

from . import auth, ia, plazos, service
from .db import DB

PROTOCOLO = "2026-07-28"


class ErrorArgumento(ValueError):
    """Argumento mal formado. El mensaje debe decirle al agente qué se esperaba.

    Ojo: no es lo mismo que un error de negocio. Si el agente se equivocó en el
    formato puede corregirse solo; si le falta un dato del expediente, tiene que
    preguntarle al abogado. El texto del mensaje es lo único que va a leer.
    """


def _error(ident, mensaje: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "result": {"content": [{"type": "text", "text": mensaje}], "isError": True},
    }


def fecha_iso(valor, campo: str) -> dt.date:
    """Fecha ISO obligatoria. Un 'ayer' o un None tienen que explicarse, no reventar."""
    if isinstance(valor, dt.date):
        return valor
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorArgumento(
            f"'{campo}' debe ser una fecha en formato YYYY-MM-DD (por ejemplo 2026-09-17); "
            f"llegó: {valor!r}"
        )
    try:
        return dt.date.fromisoformat(valor.strip())
    except ValueError as exc:
        raise ErrorArgumento(
            f"'{campo}' no es una fecha real: {valor!r}. Se espera YYYY-MM-DD (por ejemplo 2026-09-17)."
        ) from exc


def entero(valor, campo: str, minimo: int | None = None) -> int:
    """Entero obligatorio, con mensaje útil cuando llega basura."""
    if isinstance(valor, bool) or not isinstance(valor, (int, str, float)):
        raise ErrorArgumento(f"'{campo}' debe ser un número entero; llegó: {valor!r}")
    try:
        numero = int(valor)
    except (TypeError, ValueError) as exc:
        raise ErrorArgumento(f"'{campo}' debe ser un número entero; llegó: {valor!r}") from exc
    if minimo is not None and numero < minimo:
        raise ErrorArgumento(f"'{campo}' debe ser mayor o igual a {minimo}; llegó: {numero}")
    return numero


# --------------------------------------------------------------------- contexto
class Contexto:
    """Base y usuario con el que actúa el agente (por variables de entorno)."""

    def __init__(self, db_url: str | None = None, usuario_email: str | None = None):
        self.db_url = db_url or os.environ.get("OPENLEGAL_MCP_DB") or os.environ.get("LEGALCRM_DB_URL")
        self.usuario_email = usuario_email or os.environ.get("OPENLEGAL_MCP_USUARIO")
        self._db: DB | None = None
        self._usuario: dict | None = None

    @property
    def db(self) -> DB:
        if self._db is None:
            self._db = DB(self.db_url)
            self._db.migrar()
        return self._db

    @property
    def usuario(self) -> dict:
        if self._usuario is None:
            if self.usuario_email:
                fila = self.db.uno(
                    "SELECT * FROM usuarios WHERE email = ? AND activo = 1",
                    (self.usuario_email.lower(),),
                )
                if not fila:
                    raise ValueError(f"no existe el usuario {self.usuario_email} en el CRM")
            else:
                fila = self.db.uno(
                    "SELECT * FROM usuarios WHERE rol IN ('socio','abogado') AND activo = 1 "
                    "ORDER BY CASE rol WHEN 'socio' THEN 0 ELSE 1 END, id LIMIT 1"
                )
            if not fila:
                raise ValueError("el CRM no tiene usuarios activos: corre `openlegal init` y crea uno")
            fila.pop("password_hash", None)
            self._usuario = fila
        return self._usuario


# ------------------------------------------------------------------ herramientas
def _fecha(valor: str | None) -> str | None:
    return valor


HERRAMIENTAS: list[dict[str, Any]] = [
    {
        "name": "crm_estudio",
        "description": "Estudio, usuario activo del CRM y cuántas causas, plazos y audiencias hay.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "crm_causa_buscar",
        "description": "Busca causas por carátula, Rol/RIT, tribunal, materia o contraparte.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "texto a buscar (vacío = todas)"},
                "limite": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "crm_causa_leer",
        "description": "Lee una causa completa: datos, equipo, plazos pendientes, audiencias y documentos.",
        "inputSchema": {
            "type": "object",
            "properties": {"causa_id": {"type": "integer"}},
            "required": ["causa_id"],
        },
    },
    {
        "name": "crm_plazo_calcular",
        "description": (
            "Calcula el vencimiento de un plazo de días hábiles judiciales (Art. 66 CPC): "
            "no cuenta domingos ni feriados, empieza el día siguiente a la notificación, "
            "y devuelve el detalle día por día. El sábado es hábil por defecto (procedimiento "
            "civil); si el abogado sigue el criterio administrativo, pásalo en `sabado_habil: "
            "false`. La regla aplicada vuelve en `regla_dias_habiles`: no la cambies sin decirlo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "notificacion": {"type": "string", "description": "YYYY-MM-DD"},
                "dias": {"type": "integer"},
                "sabado_habil": {
                    "type": "boolean",
                    "default": True,
                    "description": "¿el sábado cuenta como día hábil? (por defecto sí, art. 59 y 66 CPC)",
                },
            },
            "required": ["notificacion", "dias"],
        },
    },
    {
        "name": "crm_plazo_crear",
        "description": "Crea un plazo en una causa, calculando el vencimiento con el Art. 66 CPC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "descripcion": {"type": "string"},
                "dias": {"type": "integer"},
                "notificacion": {"type": "string", "description": "YYYY-MM-DD"},
                "es_fatal": {"type": "boolean", "default": True},
                "tipo": {"type": "string", "enum": ["judicial", "administrativo", "interno"]},
                "sabado_habil": {"type": "boolean", "default": True, "description": "¿el sábado cuenta como día hábil?"},
            },
            "required": ["causa_id", "descripcion", "dias", "notificacion"],
        },
    },
    {
        "name": "crm_plazo_listar",
        "description": (
            "Lista los próximos vencimientos (por defecto 30 días) de las causas visibles. "
            "Cada plazo viene con su `creado_en`, `estado` y `responsable_id`: revisa `creado_en` "
            "antes de concluir que un plazo es erróneo, porque un plazo anterior a tu sesión lo "
            "creó otro usuario (o una carga de datos), no el documento que estás leyendo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dias": {"type": "integer", "default": 30},
                "desde": {"type": "string", "description": "YYYY-MM-DD"},
                "causa_id": {"type": "integer"},
            },
        },
    },
    {
        "name": "crm_plazo_actualizar",
        "description": (
            "Corrige un plazo existente (descripción, días, fecha de notificación, si es fatal) y "
            "recalcula su vencimiento con el Art. 66 CPC. Úsalo cuando detectes un plazo mal "
            "cargado en vez de crear uno nuevo encima. El motivo queda en la bitácora."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plazo_id": {"type": "integer"},
                "descripcion": {"type": "string"},
                "dias": {"type": "integer"},
                "notificacion": {"type": "string", "description": "YYYY-MM-DD"},
                "es_fatal": {"type": "boolean"},
                "motivo": {"type": "string", "description": "Por qué se corrige (queda auditado)"},
                "sabado_habil": {"type": "boolean", "default": True, "description": "¿el sábado cuenta como día hábil?"},
            },
            "required": ["plazo_id", "motivo"],
        },
    },
    {
        "name": "crm_plazo_cancelar",
        "description": (
            "Deja un plazo sin efecto sin borrarlo: la fila y el motivo quedan en la bitácora, "
            "porque un plazo fatal rectificado tiene que ser explicable después."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plazo_id": {"type": "integer"},
                "motivo": {"type": "string"},
            },
            "required": ["plazo_id", "motivo"],
        },
    },
    {
        "name": "crm_plazo_cumplido",
        "description": "Marca un plazo como cumplido.",
        "inputSchema": {
            "type": "object",
            "properties": {"plazo_id": {"type": "integer"}},
            "required": ["plazo_id"],
        },
    },
    {
        "name": "crm_audiencia_crear",
        "description": (
            "Agenda una audiencia en la causa (presencial, remota o híbrida). Antes de crear, "
            "revisa la agenda de la causa: si ya existe la misma audiencia, no la dupliques — "
            "corrígela con crm_audiencia_actualizar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "tipo": {"type": "string", "description": "p. ej. 'Audiencia preparatoria'"},
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "hora": {"type": "string", "description": "HH:MM"},
                "modalidad": {"type": "string", "enum": ["presencial", "remota", "hibrida"]},
                "lugar_o_url": {"type": "string"},
            },
            "required": ["causa_id", "tipo", "fecha"],
        },
    },
    {
        "name": "crm_audiencia_actualizar",
        "description": (
            "Corrige una audiencia existente (tipo, fecha, hora, modalidad, lugar o minuta). "
            "Úsalo cuando cambió la fecha o cuando la audiencia quedó mal cargada. El motivo "
            "queda en la bitácora."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "audiencia_id": {"type": "integer"},
                "tipo": {"type": "string"},
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "hora": {"type": "string", "description": "HH:MM"},
                "modalidad": {"type": "string", "enum": ["presencial", "remota", "hibrida"]},
                "lugar_o_url": {"type": "string"},
                "minuta": {"type": "string"},
                "motivo": {"type": "string", "description": "Por qué se corrige (queda auditado)"},
            },
            "required": ["audiencia_id", "motivo"],
        },
    },
    {
        "name": "crm_audiencia_cancelar",
        "description": (
            "Deja una audiencia sin efecto sin borrarla (por ejemplo, un duplicado que quedó "
            "cargado por error). El motivo queda en la bitácora."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "audiencia_id": {"type": "integer"},
                "motivo": {"type": "string"},
            },
            "required": ["audiencia_id", "motivo"],
        },
    },
    {
        "name": "crm_documento_registrar",
        "description": "Registra un documento en la causa (visibilidad interno o cliente).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "nombre": {"type": "string"},
                "ruta": {"type": "string"},
                "tipo": {"type": "string"},
                "visibilidad": {"type": "string", "enum": ["interno", "cliente"]},
            },
            "required": ["causa_id", "nombre"],
        },
    },
    {
        "name": "crm_auditoria_leer",
        "description": (
            "Lee la bitácora de un registro concreto (plazos, audiencias, documentos, "
            "transferencias_ia). Devuelve quién lo creó, cuándo, y cada cambio con su motivo. "
            "Úsalo ANTES de atribuir un error a un documento: si `creado_en` es anterior al "
            "inicio de tu sesión, ese registro no lo creó la lectura que estás haciendo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entidad": {"type": "string", "description": "Nombre de la tabla, ej. 'plazos'"},
                "entidad_id": {"type": "integer"},
                "limite": {"type": "integer", "default": 25},
            },
            "required": ["entidad", "entidad_id"],
        },
    },
    {
        "name": "crm_ia_estado",
        "description": "Dice si la causa está autorizada para tratarse con IA y qué términos conviene minimizar.",
        "inputSchema": {
            "type": "object",
            "properties": {"causa_id": {"type": "integer"}},
            "required": ["causa_id"],
        },
    },
    {
        "name": "crm_ia_redactar",
        "description": (
            "Minimiza un texto antes de mandarlo a un modelo: reemplaza RUT, correos, "
            "teléfonos y los nombres declarados por marcadores. Devuelve el texto limpio, "
            "el mapa para reidentificar (que se queda en el estudio) y los avisos. "
            "IMPORTANTE: en `terminos` van los nombres propios que aparezcan en el texto "
            "(partes, representantes, testigos) ADEMÁS de los de la ficha de la causa: el "
            "CRM solo conoce los suyos, y lo que no se declara no se enmascara. Revisa el "
            "resultado antes de mandarlo: si ves un nombre o un RUT a la vista, agrégalo a "
            "`terminos` y vuelve a minimizar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string"},
                "causa_id": {"type": "integer", "description": "para tomar los nombres de la causa"},
                "terminos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "nombres propios a enmascarar, incluidos los que aparezcan en el documento",
                },
            },
            "required": ["texto"],
        },
    },
    {
        "name": "crm_ia_registrar",
        "description": (
            "Registra que un texto de la causa se envió a un proveedor de IA. Exige autorización "
            "vigente. Guarda hash y tamaño, no el contenido: es la evidencia de qué salió y cuándo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "proveedor": {"type": "string", "enum": list(ia.PROVEEDORES)},
                "modelo": {"type": "string"},
                "documentos": {"type": "string"},
                "texto": {"type": "string", "description": "lo que se envió (para el hash)"},
                "redactado": {"type": "boolean", "default": False},
            },
            "required": ["causa_id", "proveedor", "texto"],
        },
    },
]


def ejecutar(nombre: str, argumentos: dict, ctx: Contexto) -> dict:
    """Despacha una llamada de herramienta. Devuelve el resultado ya en JSON."""
    db, usuario = ctx.db, ctx.usuario

    if nombre == "crm_estudio":
        conteos = {
            tabla: db.uno(f"SELECT COUNT(*) AS total FROM {tabla}")["total"]
            for tabla in ("clientes", "causas", "plazos", "audiencias", "transferencias_ia")
        }
        return {
            "usuario": usuario["nombre"],
            "rol": usuario["rol"],
            "motor": db.dialecto,
            "conteos": conteos,
        }

    if nombre == "crm_causa_buscar":
        texto = (argumentos.get("texto") or "").strip().lower()
        limite = int(argumentos.get("limite") or 20)
        causas = auth.causas_visibles(db, usuario)
        if texto:
            causas = [
                c
                for c in causas
                if texto in " ".join(
                    str(c.get(campo) or "").lower()
                    for campo in ("caratula", "rol_rit", "tribunal", "materia", "contraparte")
                )
            ]
        return {
            "total": len(causas),
            "causas": [
                {
                    "id": c["id"],
                    "caratula": c["caratula"],
                    "rol_rit": c["rol_rit"],
                    "tribunal": c["tribunal"],
                    "materia": c["materia"],
                    "estado": c["estado_procesal"],
                }
                for c in causas[:limite]
            ],
        }

    if nombre == "crm_causa_leer":
        causa_id = int(argumentos["causa_id"])
        auth.exigir(db, usuario, "causa.leer", causa_id)
        causa = db.uno("SELECT * FROM causas WHERE id = ?", (causa_id,))
        return {
            "causa": causa,
            "equipo": service.equipo_de_causa(db, usuario, causa_id),
            "plazos": db.todos(
                "SELECT id, descripcion, tipo, dias, fecha_notificacion, fecha_vencimiento, es_fatal, estado "
                "FROM plazos WHERE causa_id = ? ORDER BY fecha_vencimiento",
                (causa_id,),
            ),
            "audiencias": db.todos(
                "SELECT id, tipo, fecha, hora, modalidad, lugar_o_url, estado FROM audiencias "
                "WHERE causa_id = ? ORDER BY fecha",
                (causa_id,),
            ),
            "documentos": db.todos(
                "SELECT id, nombre, ruta, tipo, visibilidad FROM documentos WHERE causa_id = ? ORDER BY id",
                (causa_id,),
            ),
        }

    if nombre == "crm_plazo_calcular":
        notificacion = fecha_iso(argumentos.get("notificacion"), "notificacion")
        dias_habiles = entero(argumentos.get("dias"), "dias", minimo=1)
        sabado_habil = bool(argumentos.get("sabado_habil", True))
        resultado = plazos.vencimiento(notificacion, dias_habiles, sabado_habil=sabado_habil)
        return {**resultado, "feriados_pendientes_de_validacion": plazos.feriados_por_validar(notificacion.year)}

    if nombre == "crm_plazo_crear":
        descripcion = str(argumentos.get("descripcion") or "").strip()
        if not descripcion:
            raise ErrorArgumento(
                "'descripcion' no puede ir vacía: escribe qué resolución abre el plazo "
                "(por ejemplo 'Contestar demanda — traslado de 10 días hábiles')."
            )
        resultado = service.crear_plazo(
            db, usuario, entero(argumentos.get("causa_id"), "causa_id", minimo=1), descripcion,
            dias=entero(argumentos.get("dias"), "dias", minimo=1),
            fecha_notificacion=fecha_iso(argumentos.get("notificacion"), "notificacion").isoformat(),
            tipo=str(argumentos.get("tipo") or "judicial"),
            es_fatal=bool(argumentos.get("es_fatal", True)),
            sabado_habil=bool(argumentos.get("sabado_habil", True)),
        )
        calculo_creado = resultado["calculo"] or {}
        return {
            "plazo_id": resultado["id"],
            "fecha_vencimiento": resultado["fecha_vencimiento"],
            "detalle": calculo_creado.get("detalle", []),
            "regla_dias_habiles": calculo_creado.get("regla_dias_habiles"),
            "advertencias": calculo_creado.get("advertencias", []),
        }

    if nombre == "crm_plazo_listar":
        dias = entero(argumentos.get("dias") or 30, "dias", minimo=1)
        desde = argumentos.get("desde")
        if desde is not None:
            desde = fecha_iso(desde, "desde").isoformat()
        causa_id = argumentos.get("causa_id")
        hoy = dt.date.today()
        if causa_id:
            auth.exigir(db, usuario, "plazo.leer", int(causa_id))
            filas = db.todos(
                "SELECT * FROM plazos WHERE causa_id = ? AND estado = 'pendiente' "
                "AND fecha_vencimiento IS NOT NULL ORDER BY fecha_vencimiento",
                (int(causa_id),),
            )
        else:
            filas = service.vencimientos(db, usuario, desde, dias)
        for fila in filas:
            fila["dias_restantes"] = (dt.date.fromisoformat(fila["fecha_vencimiento"]) - hoy).days
        return {"total": len(filas), "plazos": filas}

    if nombre == "crm_plazo_cumplido":
        plazo_marcado = entero(argumentos.get("plazo_id"), "plazo_id", minimo=1)
        service.marcar_cumplido(db, usuario, plazo_marcado)
        return {"ok": True, "plazo_id": plazo_marcado}

    if nombre == "crm_plazo_actualizar":
        motivo = str(argumentos.get("motivo") or "").strip()
        if not motivo:
            raise ErrorArgumento(
                "'motivo' es obligatorio: explica por qué se corrige el plazo. Queda en la "
                "bitácora y es lo que después permite justificar un vencimiento rectificado."
            )
        resultado = service.actualizar_plazo(
            db, usuario, entero(argumentos.get("plazo_id"), "plazo_id", minimo=1),
            descripcion=argumentos.get("descripcion"),
            dias=entero(argumentos["dias"], "dias", minimo=1) if argumentos.get("dias") is not None else None,
            fecha_notificacion=(
                fecha_iso(argumentos["notificacion"], "notificacion").isoformat()
                if argumentos.get("notificacion") is not None else None
            ),
            es_fatal=argumentos.get("es_fatal"),
            motivo=motivo,
            sabado_habil=bool(argumentos.get("sabado_habil", True)),
        )
        calculo = resultado.get("calculo") or {}
        return {
            "plazo_id": resultado["id"],
            "campos_actualizados": resultado["campos_actualizados"],
            "fecha_vencimiento": resultado["fecha_vencimiento"],
            "detalle": calculo.get("detalle", []),
            "regla_dias_habiles": calculo.get("regla_dias_habiles"),
            "advertencias": calculo.get("advertencias", []),
        }

    if nombre == "crm_plazo_cancelar":
        motivo_cancelar = str(argumentos.get("motivo") or "").strip()
        if not motivo_cancelar:
            raise ErrorArgumento(
                "'motivo' es obligatorio: di por qué el plazo deja de ser exigible "
                "(está duplicado, el proveído dice otra cosa, se notificó distinto)."
            )
        service.cancelar_plazo(
            db, usuario, entero(argumentos.get("plazo_id"), "plazo_id", minimo=1), motivo_cancelar
        )
        return {"ok": True, "plazo_id": entero(argumentos.get("plazo_id"), "plazo_id", minimo=1)}

    if nombre == "crm_audiencia_crear":
        audiencia_id = service.crear_audiencia(
            db, usuario, int(argumentos["causa_id"]), str(argumentos["tipo"]),
            str(argumentos["fecha"]), argumentos.get("hora"),
            modalidad=str(argumentos.get("modalidad") or "presencial"),
            lugar_o_url=argumentos.get("lugar_o_url"),
        )
        return {"audiencia_id": audiencia_id}

    if nombre == "crm_audiencia_actualizar":
        motivo_audiencia = str(argumentos.get("motivo") or "").strip()
        if not motivo_audiencia:
            raise ErrorArgumento(
                "'motivo' es obligatorio: di por qué se corrige la audiencia. Queda en la "
                "bitácora y es lo que permite explicar el cambio después."
            )
        resultado = service.actualizar_audiencia(
            db, usuario, entero(argumentos.get("audiencia_id"), "audiencia_id", minimo=1),
            tipo=argumentos.get("tipo"),
            fecha=(fecha_iso(argumentos["fecha"], "fecha").isoformat()
                   if argumentos.get("fecha") is not None else None),
            hora=argumentos.get("hora"),
            modalidad=argumentos.get("modalidad"),
            lugar_o_url=argumentos.get("lugar_o_url"),
            minuta=argumentos.get("minuta"),
            motivo=motivo_audiencia,
        )
        return resultado

    if nombre == "crm_audiencia_cancelar":
        motivo_cancelar_audiencia = str(argumentos.get("motivo") or "").strip()
        if not motivo_cancelar_audiencia:
            raise ErrorArgumento(
                "'motivo' es obligatorio: di por qué la audiencia deja de estar agendada "
                "(está duplicada, se suspendió, cambió de fecha)."
            )
        audiencia_cancelada = entero(argumentos.get("audiencia_id"), "audiencia_id", minimo=1)
        service.cancelar_audiencia(db, usuario, audiencia_cancelada, motivo_cancelar_audiencia)
        return {"ok": True, "audiencia_id": audiencia_cancelada}

    if nombre == "crm_documento_registrar":
        causa_id = int(argumentos["causa_id"])
        auth.exigir(db, usuario, "documento.crear", causa_id)
        documento_id = db.insertar(
            "documentos",
            {
                "causa_id": causa_id,
                "nombre": str(argumentos["nombre"]),
                "ruta": argumentos.get("ruta"),
                "tipo": argumentos.get("tipo"),
                "visibilidad": str(argumentos.get("visibilidad") or "interno"),
                "subido_por": usuario["id"],
            },
        )
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "documento.registrar", "documentos",
            documento_id, f"causa={causa_id} {argumentos['nombre']}",
        )
        return {"documento_id": documento_id}

    if nombre == "crm_auditoria_leer":
        entidad = str(argumentos.get("entidad") or "").strip()
        if not entidad:
            raise ErrorArgumento(
                "'entidad' es obligatorio y es el nombre de la tabla: 'plazos', 'audiencias', "
                "'documentos' o 'transferencias_ia'."
            )
        entidad_id = entero(argumentos.get("entidad_id"), "entidad_id", minimo=1)
        limite = entero(argumentos.get("limite") or 25, "limite", minimo=1)
        permiso = {"plazos": "plazo.leer", "audiencias": "audiencia.leer"}.get(entidad, "auditoria.leer")
        auth.exigir(db, usuario, permiso)
        eventos = db.todos(
            "SELECT a.id, a.accion, a.detalle, a.creado_en, u.nombre AS usuario, u.rol AS rol "
            "FROM auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id "
            "WHERE a.entidad = ? AND a.entidad_id = ? AND a.estudio_id = ? "
            "ORDER BY a.id LIMIT ?",
            (entidad, entidad_id, usuario["estudio_id"], limite),
        )
        if not eventos:
            return {
                "entidad": entidad, "entidad_id": entidad_id, "total": 0,
                "aviso": "Sin eventos en la bitácora: el registro no existe o lo creó una carga "
                         "externa al CRM. No lo atribuyas a la lectura que estás haciendo.",
            }
        primero = eventos[0]
        return {
            "entidad": entidad,
            "entidad_id": entidad_id,
            "total": len(eventos),
            "creado_en": primero["creado_en"],
            "creado_por": primero["usuario"],
            "eventos": eventos,
        }

    if nombre == "crm_ia_estado":
        return service.estado_ia(db, usuario, int(argumentos["causa_id"]))

    if nombre == "crm_ia_redactar":
        causa_id = argumentos.get("causa_id")
        terminos = list(argumentos.get("terminos") or [])
        if causa_id:
            auth.exigir(db, usuario, "causa.leer", int(causa_id))
            terminos += ia.terminos_de_causa(db, int(causa_id))
        avisos: list[str] = []
        limpio, mapa = ia.redactar(str(argumentos["texto"]), terminos, avisos=avisos)
        return {
            "texto_minimizado": limpio,
            "mapa_local": mapa,
            "marcadores": len(mapa),
            "avisos": avisos,
            "recordatorio": (
                "Revisa el texto minimizado antes de enviarlo: lo que no se declaró en "
                "'terminos' no se enmascara. Los marcadores [RUT?·n] son RUT cuyo dígito "
                "verificador no valida."
            ),
        }

    if nombre == "crm_ia_registrar":
        registro = service.registrar_transferencia(
            db, usuario, int(argumentos["causa_id"]), str(argumentos["proveedor"]),
            str(argumentos["texto"]), modelo=argumentos.get("modelo"),
            documentos=argumentos.get("documentos"), redactado=bool(argumentos.get("redactado", False)),
        )
        return registro

    raise ValueError(f"herramienta desconocida: {nombre}")


# ------------------------------------------------------------------- protocolo
def responder(solicitud: dict, ctx: Contexto) -> dict | None:
    metodo = solicitud.get("method")
    ident = solicitud.get("id")

    if metodo == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "result": {
                "protocolVersion": solicitud.get("params", {}).get("protocolVersion", PROTOCOLO),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "openlegal-crm", "version": "0.1.0"},
                "instructions": (
                    "CRM jurídico chileno local. Antes de enviar el expediente a un modelo, "
                    "revisa crm_ia_estado y usa crm_ia_redactar para minimizar; registra el envío "
                    "con crm_ia_registrar. Los plazos se calculan con el Art. 66 CPC "
                    "(días hábiles, sin domingos ni feriados)."
                ),
            },
        }
    if metodo in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None  # notificación: sin respuesta
    if metodo == "ping":
        return {"jsonrpc": "2.0", "id": ident, "result": {}}
    if metodo == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": HERRAMIENTAS}}
    if metodo == "tools/call":
        params = solicitud.get("params") or {}
        nombre = params.get("name")
        argumentos = params.get("arguments") or {}
        try:
            resultado = ejecutar(str(nombre), argumentos, ctx)
            contenido = [{"type": "text", "text": json.dumps(resultado, ensure_ascii=False, indent=1)}]
            return {
                "jsonrpc": "2.0",
                "id": ident,
                "result": {"content": contenido, "isError": False},
            }
        except auth.ErrorPermiso as exc:
            # Permiso: el agente no debe reintentar, debe pedirle al abogado.
            return _error(ident, f"Sin permiso: {exc}")
        except ErrorArgumento as exc:
            # Argumento mal formado: el agente SÍ puede corregirse, así que el
            # mensaje tiene que decirle qué se esperaba, no solo que falló.
            return _error(ident, f"Argumento inválido: {exc}")
        except (ValueError, KeyError) as exc:
            return _error(
                ident,
                f"{type(exc).__name__}: {exc}. Revisa los argumentos contra el esquema de la "
                f"herramienta y reintenta; si el dato no está en el expediente, pregúntale al abogado.",
            )
        except Exception as exc:  # noqa: BLE001 — una herramienta nunca debe tumbar el servidor
            return _error(ident, f"Fallo inesperado en {nombre} ({type(exc).__name__}: {exc}). "
                                 f"El CRM sigue disponible: corrige los argumentos o avisa al usuario.")
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "error": {"code": -32601, "message": f"método no soportado: {metodo}"},
    }


def servir(entrada=None, salida=None, ctx: Contexto | None = None) -> None:
    """Bucle stdio: una línea JSON por mensaje, como el MCP de open-legal-chile."""
    entrada = entrada or sys.stdin
    salida = salida or sys.stdout
    ctx = ctx or Contexto()
    for linea in entrada:
        linea = linea.strip()
        if not linea:
            continue
        try:
            solicitud = json.loads(linea)
        except json.JSONDecodeError:
            salida.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}}) + "\n")
            salida.flush()
            continue
        try:
            respuesta = responder(solicitud, ctx)
        except Exception as exc:  # noqa: BLE001 — el bucle no puede morir por una herramienta
            ident = solicitud.get("id") if isinstance(solicitud, dict) else None
            respuesta = {
                "jsonrpc": "2.0",
                "id": ident,
                "error": {
                    "code": -32603,
                    "message": f"fallo interno del CRM: {type(exc).__name__}: {exc}",
                },
            }
        if respuesta is not None:
            salida.write(json.dumps(respuesta, ensure_ascii=False) + "\n")
            salida.flush()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db_url = usuario = None
    if "--db" in argv:
        db_url = argv[argv.index("--db") + 1]
    if "--usuario" in argv:
        usuario = argv[argv.index("--usuario") + 1]
    servir(ctx=Contexto(db_url, usuario))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
