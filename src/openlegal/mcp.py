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

from . import auth, honorarios, ia, ia_proxy, plazos, seguridad, service
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

    def cerrar(self) -> None:
        """Suelta la conexion del MCP, si se llego a abrir.

        Hace falta de verdad: en Windows un archivo abierto no se puede borrar, asi que
        una conexion viva hace fallar la limpieza de las bases temporales de las
        pruebas; y el servidor debe soltar la base al terminar, en vez de esperar al
        recolector de basura.
        """
        if self._db is not None:
            self._db.cerrar()
            self._db = None
        self._usuario = None


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
        "name": "crm_cliente_buscar",
        "description": (
            "Busca clientes del estudio por nombre, RUT, correo o teléfono. Búscalo antes de "
            "crear uno: el cliente duplicado después hay que limpiarlo a mano, porque el CRM "
            "no borra."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "texto a buscar (vacío = todos)"},
                "limite": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "crm_cliente_crear",
        "description": (
            "Crea la ficha de un cliente y devuelve su cliente_id. El RUT se guarda tal como "
            "te lo entregaron; si el dígito verificador no cuadra, la respuesta trae un aviso "
            "—no lo corrijas por tu cuenta ni inventes uno—. Si es persona jurídica, hace "
            "falta el representante legal: en juicio se pregunta quién obliga a la sociedad."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "rut": {"type": "string", "description": "con puntos y guion, o sin ellos"},
                "tipo_persona": {
                    "type": "string",
                    "enum": ["natural", "juridica"],
                    "default": "natural",
                },
                "representante_legal": {"type": "string", "description": "obligatorio si es jurídica"},
                "email": {"type": "string"},
                "telefono": {"type": "string", "description": "formato +56 9 xxxx xxxx, para poder avisarle por SMS"},
                "direccion": {"type": "string"},
            },
            "required": ["nombre"],
        },
    },
    {
        "name": "crm_causa_crear",
        "description": (
            "Abre una causa (el expediente) y devuelve su causa_id. Si el cliente ya existe, "
            "pásale su cliente_id (búscalo antes con crm_cliente_buscar). La carátula es "
            "obligatoria: una causa sin nombre es basura en el sistema, y no se borra, sólo se "
            "archiva."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "caratula": {"type": "string", "description": "por ejemplo 'Herrera con Fondo del Norte'"},
                "cliente_id": {"type": "integer"},
                "rol_rit": {"type": "string", "description": "Rol/RIT tal como sale en la carpeta del tribunal"},
                "tribunal": {"type": "string"},
                "materia": {"type": "string", "description": "civil, laboral, penal, familia, ..."},
                "contraparte": {"type": "string"},
                "cuantia_clp": {"type": "integer"},
                "observaciones": {"type": "string"},
            },
            "required": ["caratula"],
        },
    },
    {
        "name": "crm_agenda",
        "description": (
            "Próximas audiencias de las causas que este usuario puede ver, dentro de los "
            "próximos N días. Úsala antes de agendar algo nuevo (para no citar a dos partes a "
            "la misma hora) y para responder 'qué tengo esta semana'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dias": {"type": "integer", "default": 30, "description": "ventana en días"},
            },
        },
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
    {
        "name": "crm_envios_ia",
        "description": (
            "Lista los últimos envíos a proveedores de IA que quedaron registrados (los del CRM "
            "y los que pasaron por el proxy local del estudio). Devuelve METADATOS: proveedor, "
            "modelo, país de destino, cantidad de caracteres, hash SHA-256 del texto, si iba "
            "minimizado, la causa, quién lo mandó, de dónde salió y si el proxy lo bloqueó con "
            "su motivo. El contenido de lo que se envió NO se guarda en ninguna parte, así que "
            "acá no vas a encontrar el texto: el hash sirve para probar que lo que está en el "
            "expediente es lo mismo que salió, no para leerlo. Para qué sirve: es la prueba de "
            "licitud del tratamiento (qué salió, cuándo, hacia dónde y bajo qué autorización), "
            "lo que la ley pide poder demostrar (art. 13 letra e) y arts. 27 a 29 de la Ley "
            "19.628 en su texto reformado por la Ley 21.719). Úsala para responder «¿qué se "
            "mandó de esta causa?» o para revisar los intentos bloqueados por falta de "
            "autorización."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer", "description": "sólo los envíos de esa causa"},
                "bloqueados": {
                    "type": "boolean",
                    "default": False,
                    "description": "sólo los que NO se reenviaron (sin autorización, sin poder minimizar, o sin conexión con el proveedor), con su motivo",
                },
                "limite": {"type": "integer", "default": 50, "description": "cuántos envíos devolver (por defecto 50)"},
            },
        },
    },
    {
        "name": "crm_honorario_registrar",
        "description": (
            "Registra un honorario pactado en una causa. Los montos son enteros en CLP, sin "
            "puntos ni decimales (350000 son $350.000). La retención NO la calcula el CRM ni la "
            "inventes: es un dato que el estudio copia de su boleta y va en 'retencion_sii'. Si "
            "no la tienes, omítela y el líquido quedará igual al bruto — la cuenta de dividendos "
            "lo dirá por escrito, así que avísale al abogado. No hay integración con el SII ni "
            "emisión de boletas: el CRM registra lo que el estudio declara."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "modalidad": {
                    "type": "string",
                    "enum": list(honorarios.MODALIDADES),
                    "default": "fijo",
                    "description": "fijo, por hora, cuota litis o mixto",
                },
                "monto_pactado": {"type": "integer", "description": "lo acordado con el cliente, en CLP enteros"},
                "descripcion": {"type": "string", "description": "qué se pactó, en palabras (por ejemplo 'Demanda civil, primera instancia')"},
                "fecha": {"type": "string", "description": "desde cuándo rige el pacto: YYYY-MM-DD (por defecto hoy)"},
                "monto_bruto": {"type": "integer", "description": "monto bruto de la boleta del estudio, en CLP enteros"},
                "retencion_sii": {
                    "type": "integer",
                    "description": "retención que el estudio copia de su boleta, en CLP enteros (no la calcules)",
                },
            },
            "required": ["causa_id"],
        },
    },
    {
        "name": "crm_gasto_registrar",
        "description": (
            "Registra un gasto de la causa (notaría, receptor, tasas, peritaje) que adelantó el "
            "estudio y que después se le pasa al cliente en la cuenta de dividendos. El monto es "
            "un entero en CLP (25000 son $25.000). Si el gasto lo pagó el cliente, pásalo en "
            "'pagado_por_estudio': false para que no engorde lo que se le cobra. Guarda el "
            "respaldo en 'comprobante' (boleta, factura, recibo): un gasto sin respaldo es difícil "
            "de sostener frente al cliente, y el CRM no lo completa por su cuenta."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "concepto": {"type": "string", "description": "qué se pagó (por ejemplo 'Notaría 45, autorización de firma')"},
                "monto": {"type": "integer", "description": "CLP enteros, mayor que 0"},
                "fecha": {"type": "string", "description": "YYYY-MM-DD (por defecto hoy)"},
                "comprobante": {"type": "string", "description": "boleta, factura o recibo que respalda el gasto"},
                "pagado_por_estudio": {
                    "type": "boolean",
                    "default": True,
                    "description": "true = lo adelantó el estudio y se le cuenta al cliente; false = lo pagó el cliente",
                },
            },
            "required": ["causa_id", "concepto", "monto"],
        },
    },
    {
        "name": "crm_pago_registrar",
        "description": (
            "Registra un abono del cliente en una causa. El monto va en CLP enteros (200000 son "
            "$200.000). Registrar un pago es un acto con consecuencias: baja lo que el cliente "
            "debe, queda en su cuenta y en la bitácora con tu nombre — confirma el monto y la "
            "fecha con quien te lo pidió (o contra el comprobante) antes de escribirlo, y no "
            "redondees ni completes nada por tu cuenta. Con 'honorario_id' el pago queda imputado "
            "a ese honorario y el CRM recalcula si quedó parcial o pagado; el honorario tiene que "
            "ser de ESTA causa (si es de otra, la herramienta lo rechaza)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "causa_id": {"type": "integer"},
                "monto": {"type": "integer", "description": "CLP enteros, mayor que 0"},
                "fecha": {"type": "string", "description": "la del comprobante: YYYY-MM-DD (por defecto hoy)"},
                "medio": {
                    "type": "string",
                    "enum": list(honorarios.MEDIOS),
                    "default": "transferencia",
                },
                "referencia": {"type": "string", "description": "n° de transferencia, cheque o comprobante"},
                "nota": {"type": "string"},
                "honorario_id": {"type": "integer", "description": "honorario de esta causa al que se imputa el pago"},
            },
            "required": ["causa_id", "monto"],
        },
    },
    {
        "name": "crm_cuenta_dividendos",
        "description": (
            "Lee la cuenta de dividendos de una causa: honorarios pactados y líquidos, gastos, "
            "pagos recibidos, saldo y advertencias. Es de sólo lectura. Los montos vienen en CLP "
            "enteros. El saldo es honorarios líquidos + gastos por cuenta del cliente - pagos. Si "
            "un honorario no tiene retención declarada, la cuenta lo advierte: no supongas la "
            "tasa ni completes el dato. Esta cuenta no es un documento tributario."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"causa_id": {"type": "integer"}},
            "required": ["causa_id"],
        },
    },
]


def ejecutar(nombre: str, argumentos: dict, ctx: Contexto) -> dict:
    """Despacha una llamada de herramienta. Devuelve el resultado ya en JSON."""
    db, usuario = ctx.db, ctx.usuario

    if nombre == "crm_estudio":
        conteos = {}
        for tabla in ("clientes", "causas", "plazos", "audiencias", "transferencias_ia"):
            fila = db.uno(f"SELECT COUNT(*) AS total FROM {tabla}")
            conteos[tabla] = (fila or {}).get("total", 0)
        return {
            "usuario": usuario["nombre"],
            "rol": usuario["rol"],
            "motor": db.dialecto,
            "conteos": conteos,
        }

    if nombre == "crm_cliente_buscar":
        auth.exigir(db, usuario, "cliente.leer")
        texto = (argumentos.get("texto") or "").strip().lower()
        limite = entero(argumentos.get("limite") or 20, "limite", minimo=1)
        filas = db.todos(
            "SELECT id, nombre, rut, tipo_persona, representante_legal, email, telefono, direccion "
            "FROM clientes WHERE estudio_id = ? ORDER BY nombre",
            (usuario["estudio_id"],),
        )
        if texto:
            campos = ("nombre", "rut", "email", "telefono", "direccion")
            filas = [
                f
                for f in filas
                if texto in " ".join(str(f.get(campo) or "") for campo in campos).lower()
            ]
        return {
            "total": len(filas),
            "clientes": [
                {
                    **f,
                    "rut_valido": seguridad.rut_valido(f["rut"]) if f.get("rut") else None,
                }
                for f in filas[:limite]
            ],
        }

    if nombre == "crm_cliente_crear":
        nombre_cliente = str(argumentos.get("nombre") or "").strip()
        if not nombre_cliente:
            raise ErrorArgumento(
                "'nombre' no puede ir vacío: sin nombre no hay a quién notificarle un plazo ni "
                "a quién cobrarle."
            )
        tipo = str(argumentos.get("tipo_persona") or "natural").strip().lower()
        if tipo not in ("natural", "juridica"):
            raise ErrorArgumento("'tipo_persona' tiene que ser 'natural' o 'juridica'.")
        representante = (argumentos.get("representante_legal") or "").strip() or None
        if tipo == "juridica" and not representante:
            raise ErrorArgumento(
                "'representante_legal' es obligatorio en una persona jurídica: es a quien se le "
                "notifica y quien la obliga."
            )
        rut = (argumentos.get("rut") or "").strip() or None
        cliente_id = service.crear_cliente(
            db,
            usuario,
            nombre_cliente,
            rut,
            tipo_persona=tipo,
            representante_legal=representante,
            email=(argumentos.get("email") or "").strip() or None,
            telefono=(argumentos.get("telefono") or "").strip() or None,
            direccion=(argumentos.get("direccion") or "").strip() or None,
        )
        creado = db.uno(
            "SELECT id, nombre, rut, tipo_persona, representante_legal, email, telefono, direccion "
            "FROM clientes WHERE id = ?",
            (cliente_id,),
        )
        aviso = None
        if rut and not seguridad.rut_valido(rut):
            aviso = (
                f"el RUT {rut} no cuadra con el módulo 11. Queda guardado igual, pero confírmalo "
                "con quien te lo entregó antes de usarlo en un escrito"
            )
        return {"cliente_id": cliente_id, "cliente": creado, "aviso": aviso}

    if nombre == "crm_causa_crear":
        caratula = str(argumentos.get("caratula") or "").strip()
        if not caratula:
            raise ErrorArgumento(
                "'caratula' es obligatoria: es el nombre con que el tribunal y el estudio "
                "identifican la causa (por ejemplo 'Herrera con Fondo del Norte')."
            )
        cliente_pedido = argumentos.get("cliente_id")
        cliente_de_la_causa: int | None = None
        if cliente_pedido is not None:
            cliente_de_la_causa = entero(cliente_pedido, "cliente_id", minimo=1)
            existe = db.uno(
                "SELECT id FROM clientes WHERE id = ? AND estudio_id = ?",
                (cliente_de_la_causa, usuario["estudio_id"]),
            )
            if not existe:
                raise ErrorArgumento(
                    f"no hay cliente {cliente_de_la_causa} en este estudio: búscalo con "
                    "crm_cliente_buscar o créalo con crm_cliente_crear"
                )
        cuantia = argumentos.get("cuantia_clp")
        causa_id = service.crear_causa(
            db,
            usuario,
            caratula,
            cliente_id=cliente_de_la_causa,
            rol_rit=(argumentos.get("rol_rit") or "").strip() or None,
            tribunal=(argumentos.get("tribunal") or "").strip() or None,
            materia=(argumentos.get("materia") or "").strip() or None,
            contraparte=(argumentos.get("contraparte") or "").strip() or None,
            cuantia_clp=entero(cuantia, "cuantia_clp", minimo=0) if cuantia is not None else None,
            observaciones=(argumentos.get("observaciones") or "").strip() or None,
        )
        return {
            "causa_id": causa_id,
            "causa": db.uno("SELECT * FROM causas WHERE id = ?", (causa_id,)),
            "siguiente_paso": (
                "cárgale los plazos con crm_plazo_crear (el vencimiento lo calcula el CRM con el "
                "Art. 66 CPC: no lo calcules vos) y las audiencias con crm_audiencia_crear"
            ),
        }

    if nombre == "crm_agenda":
        auth.exigir(db, usuario, "audiencia.leer")
        dias = entero(argumentos.get("dias") or 30, "dias", minimo=1)
        hoy = dt.date.today()
        hasta = hoy + dt.timedelta(days=dias)
        visibles = sorted({c["id"] for c in auth.causas_visibles(db, usuario)})
        if not visibles:
            return {"desde": hoy.isoformat(), "hasta": hasta.isoformat(), "total": 0, "audiencias": []}
        marcadores = ",".join("?" * len(visibles))
        filas = db.todos(
            "SELECT a.id, a.causa_id, c.caratula, c.rol_rit, a.tipo, a.fecha, a.hora, a.modalidad, "
            "a.lugar_o_url, a.estado FROM audiencias a JOIN causas c ON c.id = a.causa_id "
            f"WHERE a.causa_id IN ({marcadores}) AND a.fecha >= ? AND a.fecha <= ? "
            "AND a.estado <> 'cancelada' ORDER BY a.fecha, a.hora",
            (*visibles, hoy.isoformat(), hasta.isoformat()),
        )
        return {
            "desde": hoy.isoformat(),
            "hasta": hasta.isoformat(),
            "total": len(filas),
            "audiencias": filas,
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
        causa_pedida = argumentos.get("causa_id")
        hoy = dt.date.today()
        if causa_pedida:
            auth.exigir(db, usuario, "plazo.leer", int(causa_pedida))
            filas = db.todos(
                "SELECT * FROM plazos WHERE causa_id = ? AND estado = 'pendiente' "
                "AND fecha_vencimiento IS NOT NULL ORDER BY fecha_vencimiento",
                (int(causa_pedida),),
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
        causa_pedida = argumentos.get("causa_id")
        terminos = list(argumentos.get("terminos") or [])
        if causa_pedida:
            auth.exigir(db, usuario, "causa.leer", int(causa_pedida))
            terminos += ia.terminos_de_causa(db, int(causa_pedida))
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

    if nombre == "crm_envios_ia":
        causa_pedida = argumentos.get("causa_id")
        limite = entero(argumentos.get("limite") or 50, "limite", minimo=1)
        causa_filtro: int | None = None
        visibles_filtro: list[int] | None = None
        if causa_pedida:
            auth.exigir(db, usuario, "ia.leer", int(causa_pedida))
            causa_filtro = int(causa_pedida)
        else:
            auth.exigir(db, usuario, "ia.leer")
            visibles_filtro = [c["id"] for c in auth.causas_visibles(db, usuario)]
        filas = ia_proxy.envios_recientes(
            db, causa_id=causa_filtro, solo_bloqueados=bool(argumentos.get("bloqueados", False)),
            limite=limite, visibles=visibles_filtro, estudio_id=usuario["estudio_id"],
        )
        return {
            "total": len(filas),
            "envios": filas,
            "aviso": (
                f"{ia_proxy.AVISO_SIN_CONTENIDO}. El hash es lo que permite demostrar que el "
                "texto que salió es el que está en el expediente: no intentes reconstruir el "
                "contenido a partir de él."
            ),
        }

    if nombre == "crm_honorario_registrar":
        modalidad = str(argumentos.get("modalidad") or "fijo").strip().lower()
        if modalidad not in honorarios.MODALIDADES:
            raise ErrorArgumento(
                f"'modalidad' tiene que ser una de {', '.join(honorarios.MODALIDADES)}; llegó: {modalidad!r}"
            )
        pactado = argumentos.get("monto_pactado")
        bruto = argumentos.get("monto_bruto")
        retencion = argumentos.get("retencion_sii")
        if pactado is None and bruto is None:
            raise ErrorArgumento(
                "'monto_pactado' o 'monto_bruto' son obligatorios: sin monto no hay honorario que registrar. "
                "Los montos son enteros en CLP, sin puntos ni decimales (350000 son $350.000)."
            )
        honorario_id = honorarios.registrar_honorario(
            db,
            usuario,
            entero(argumentos.get("causa_id"), "causa_id", minimo=1),
            modalidad,
            monto_pactado=entero(pactado, "monto_pactado", minimo=0) if pactado is not None else None,
            descripcion=(argumentos.get("descripcion") or "").strip() or None,
            fecha=fecha_iso(argumentos["fecha"], "fecha").isoformat() if argumentos.get("fecha") else None,
            monto_bruto=entero(bruto, "monto_bruto", minimo=0) if bruto is not None else None,
            retencion_sii=entero(retencion, "retencion_sii", minimo=0) if retencion is not None else None,
        )
        fila = db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,)) or {}
        aviso = None
        if fila.get("monto_bruto") is not None and fila.get("retencion_sii") is None:
            # El CRM no puede saber si el honorario no está afecto a retención o si falta el dato:
            # lo dice y lo deja anotado, que es lo único honesto que puede hacer.
            aviso = (
                "el líquido quedó igual al bruto porque no se declaró retención. La tasa es un dato del estudio "
                "(sale de su boleta): pregúntala y regístrala en 'retencion_sii' junto con el bruto antes de dar "
                "el líquido por bueno; mientras tanto la cuenta de dividendos lo está advirtiendo por escrito."
            )
        return {"honorario_id": honorario_id, "honorario": fila, "aviso": aviso}

    if nombre == "crm_gasto_registrar":
        concepto = str(argumentos.get("concepto") or "").strip()
        if not concepto:
            raise ErrorArgumento(
                "'concepto' no puede ir vacío: di qué se pagó (notaría, receptor, tasas, peritaje). Ese texto es "
                "el que el cliente va a leer en su cuenta de dividendos."
            )
        gasto_id = honorarios.registrar_gasto(
            db,
            usuario,
            entero(argumentos.get("causa_id"), "causa_id", minimo=1),
            concepto,
            entero(argumentos.get("monto"), "monto", minimo=1),
            fecha=fecha_iso(argumentos["fecha"], "fecha").isoformat() if argumentos.get("fecha") else None,
            comprobante=(argumentos.get("comprobante") or "").strip() or None,
            pagado_por_estudio=bool(argumentos.get("pagado_por_estudio", True)),
        )
        fila = db.uno("SELECT * FROM gastos WHERE id = ?", (gasto_id,)) or {}
        return {
            "gasto_id": gasto_id,
            "gasto": fila,
            "aviso": None if fila.get("comprobante") else
            "el gasto quedó sin comprobante. Anota el respaldo cuando lo tengas: un gasto sin "
            "respaldo es difícil de sostener frente al cliente.",
        }

    if nombre == "crm_pago_registrar":
        monto = entero(argumentos.get("monto"), "monto", minimo=1)
        medio = str(argumentos.get("medio") or "transferencia").strip().lower()
        if medio not in honorarios.MEDIOS:
            raise ErrorArgumento(
                f"'medio' tiene que ser uno de {', '.join(honorarios.MEDIOS)}; llegó: {medio!r}"
            )
        causa_id = entero(argumentos.get("causa_id"), "causa_id", minimo=1)
        honorario_pedido = argumentos.get("honorario_id")
        pago_id = honorarios.registrar_pago(
            db,
            usuario,
            causa_id,
            monto,
            fecha=fecha_iso(argumentos["fecha"], "fecha").isoformat() if argumentos.get("fecha") else None,
            medio=medio,
            referencia=(argumentos.get("referencia") or "").strip() or None,
            nota=(argumentos.get("nota") or "").strip() or None,
            honorario_id=entero(honorario_pedido, "honorario_id", minimo=1) if honorario_pedido is not None else None,
        )
        respuesta: dict[str, Any] = {
            "pago_id": pago_id,
            "causa_id": causa_id,
            "monto": monto,
            "registrado_por": usuario["nombre"],
            "recordatorio": (
                "el abono quedó en la cuenta del cliente y en la bitácora. Si el monto o la fecha no calzan con el "
                "comprobante, no se corrige borrando: avísale al estudio para que lo rectifique con otro registro."
            ),
        }
        if honorario_pedido is not None:
            respuesta["honorario"] = honorarios.recalcular_honorario(db, int(honorario_pedido))
            try:
                respuesta["saldo_de_la_causa"] = honorarios.cuenta(db, usuario, causa_id)["totales"]["saldo"]
            except auth.ErrorPermiso:  # un rol que registra pagos pero no lee la cuenta
                respuesta["saldo_de_la_causa"] = None
        return respuesta

    if nombre == "crm_cuenta_dividendos":
        return honorarios.cuenta(db, usuario, entero(argumentos.get("causa_id"), "causa_id", minimo=1))

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
                "serverInfo": {"name": "open-legal-harness", "version": "0.1.0"},
                "instructions": (
                    "CRM jurídico chileno local (Open Legal Harness). Antes de enviar el expediente a un modelo, "
                    "revisa crm_ia_estado y usa crm_ia_redactar para minimizar; registra el envío "
                    "con crm_ia_registrar. Si el modelo se llama a través del proxy local del estudio, "
                    "cada uso de IA queda registrado solo: revisa crm_envios_ia (son metadatos: el "
                    "contenido no se guarda). Los plazos se calculan con el Art. 66 CPC "
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
