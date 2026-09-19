"""Proxy local: advierte y registra CADA uso de IA, en el dialecto de OpenAI y en el de Anthropic.

El CRM ya tenía todo el aparato legal para mandar datos de una causa a un modelo
—autorización por causa, minimización con marcadores y el registro `transferencias_ia`—
pero ese aparato sólo actuaba cuando el envío **pasaba por el CRM** (`ia.redactar`, las
herramientas `crm_ia_*`). El harness (`dsh`) y cualquier otra herramienta hablan DIRECTO
con el proveedor del modelo: por ahí no había aviso, ni minimización, ni registro de nada.
Un estudio podía creer que tenía todo documentado y tener, en realidad, un hueco por donde
salían expedientes sin dejar rastro.

Este módulo es ese hueco cerrado. Es un proxy local que habla **los dos dialectos** que usan las
herramientas de verdad —el de OpenAI y el de Anthropic—, al que se apunta una herramienta
cambiando una sola cosa: la dirección base. El harness (`dsh`), por ejemplo, usa por defecto el
protocolo `messages` (el de Anthropic) y arma la URL como `baseURL + /v1/messages`, así que su
`baseURL` es el proxy **sin** `/v1` y **sin** `/anthropic`: el adaptador agrega `/v1/messages` él
mismo.

    POST /v1/chat/completions   dialecto OpenAI    → base_url           pass-through fiel (SSE por trozos)
    POST /v1/messages           dialecto Anthropic → base_url_anthropic pass-through fiel (SSE por trozos)
    GET  /v1/models             catálogo del proveedor (con `anthropic-version` va al de Anthropic)
    GET  /salud                 el proxy, para que el panel sepa si está corriendo

Son **dos direcciones aguas arriba** y no una porque el dialecto no dice lo mismo en los dos
lados: el mismo proveedor publica `/v1/chat/completions` en una dirección y `/v1/messages` en
otra (`https://api.deepseek.com/anthropic`, por ejemplo). Cada pedido va a la dirección de su
dialecto, y el registro es **uno solo** para los dos: `via = 'openai-compat'` o
`'anthropic-compat'`, con el mismo aparato de autorización, minimización y aviso detrás.


Escucha **sólo** en 127.0.0.1 y no tiene forma de que sea de otra manera: un proxy de
datos de clientes expuesto a la red no es un proxy, es una brecha. Y detrás puede estar
cualquier proveedor (o un modelo local): lo que se configura es la dirección aguas arriba.

Antes de reenviar, cada llamada pasa por cuatro decisiones:

1. **Cuenta y huella.** Se cuenta el largo del texto que sale y se calcula su SHA-256 con
   `ia.hash_payload`. El hash es lo que después permite decir «esto es lo que se mandó»
   sin tener que guardar el expediente dentro de la base.
2. **Minimiza** (si `minimizar` está activo, que es lo que corresponde): aplica
   `ia.redactar` con los términos de la causa —RUT, correos, teléfonos y los nombres de la
   ficha— y avisa cuántos marcadores puso. Si el pedido trae algo que no se puede minimizar
   (una imagen en base64, por ejemplo), **no reenvía**: negarse es mejor que mandar datos
   personales cuando el estudio pidió minimizarlos.
3. **Comprueba la autorización** de la causa (cabecera `X-OpenLegal-Causa` o la causa por
   defecto de la configuración). Sin autorización vigente y con `permitir_sin_autorizacion`
   apagado, NO reenvía: contesta un error que dice qué hacer y lo registra como bloqueado.
4. **Registra** el envío en `transferencias_ia` (proveedor, modelo, país de destino,
   caracteres, hash, si iba minimizado, causa, autorización) con `origen='proxy'`, y deja su
   entrada en `auditoria`. Los envíos bloqueados también se registran: el intento es
   justamente lo que hay que poder demostrar.

El dialecto de Anthropic lleva el texto de otra manera —`system` (una cadena o una lista de
bloques) y `messages[].content` (una cadena o una lista de bloques, con `tool_use` y
`tool_result` en medio)—, así que tiene su propio recorrido. Se minimiza todo lo que es texto de
la causa: el `system`, los bloques `text`, el contenido de los `tool_result` (que es donde
vuelven los datos que sacaron las herramientas del CRM), los `input` de los `tool_use` y también
las **descripciones de las `tools`** —sí se minimizan: son texto que el estudio escribió y puede
llevar un nombre o un RUT—. Los `input_schema` **no** se tocan: son la estructura de la
herramienta y enmascarar un valor de ahí (el `enum` de un campo, por ejemplo) rompería la
herramienta aguas arriba; si en un esquema van datos de personas, el error está en la
herramienta, no en la minimización. Los bloques `redacted_thinking` pasan tal cual porque son un
bloque cifrado del proveedor, no texto de la causa; los `thinking` sí se minimizan, con la
advertencia de que un proveedor que valide la firma del razonamiento puede rechazar el bloque
modificado. Y un bloque que no sé minimizar —una imagen, un documento— no se reenvía: se corta
exactamente igual que en el dialecto de OpenAI.

Lo que este módulo NO hace, y conviene tener claro:

- **No guarda el contenido de nada.** Ni en la base, ni en un archivo, ni en la salida: sólo
  metadatos. La respuesta del modelo pasa de largo (por trozos, sin guardarse) y nunca se
  imprime.
- **No guarda ni imprime la `api_key`.** Vive en `~/.openlegal/ia_proxy.json` (permisos 600),
  se manda aguas arriba en la cabecera `Authorization` y no aparece en ningún registro, ni en
  el resumen de estado, ni en los avisos.
- **No falla en silencio.** Si no puede registrar (la base no está, `openlegal init` sin
  correr), no reenvía y lo dice: sin registro no hay aviso, y el sentido del proxy es
  justamente el aviso.

El aviso de cada envío sale por la terminal del proxy (una línea) y, si `avisar_escritorio`
está activo, también por `notify-send`, con un tope para no tapar la pantalla — salvo los
envíos bloqueados, que siempre se notifican porque son los que hay que mirar.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import pathlib
import shutil
import socket
import subprocess
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import auth, ia
from .db import DB

#: Dirección de escucha. No es configurable a propósito: el proxy es de esta máquina.
DIRECCION = "127.0.0.1"

#: Puerto por defecto (el harness usa 8801 y el CRM 8899, para no pisarse).
PUERTO_POR_DEFECTO = 8790

#: Cómo se llama este camino en el registro: la API de OpenAI y sus imitadoras.
VIA = "openai-compat"

#: El mismo proxy, hablado en el dialecto de Anthropic (`/v1/messages`). Se registra distinto
#: para que la bitácora diga por qué protocolo salió cada envío.
VIA_OPENAI = VIA
VIA_ANTHROPIC = "anthropic-compat"

#: Las rutas que atiende el proxy, por dialecto.
RUTA_CHAT = "/v1/chat/completions"
RUTA_MESSAGES = "/v1/messages"
RUTA_MODELOS = "/v1/models"
RUTA_SALUD = "/salud"

#: Dirección aguas arriba del dialecto Anthropic. El mismo proveedor publica los dos dialectos
#: en direcciones distintas: DeepSeek, por ejemplo, en `…/anthropic` (y el adaptador del harness
#: le agrega `/v1/messages`).
BASE_URL_ANTHROPIC_POR_DEFECTO = "https://api.deepseek.com/anthropic"

#: La versión del dialecto que se manda si el cliente no la trajo (la API de Anthropic la exige).
ANTHROPIC_VERSION = "2023-06-01"

#: El tipo de error del dialecto de Anthropic que le corresponde a cada código HTTP.
TIPOS_ANTHROPIC: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
}

#: Lo que se contesta cuando el pedido va por una ruta que este proxy no habla: dice qué sí
#: atiende, para que quien lo lea sepa qué cambiar y no tenga que adivinar.
AYUDA_RUTAS = (
    f"este proxy atiende {RUTA_CHAT} (dialecto OpenAI), {RUTA_MESSAGES} (dialecto Anthropic) y "
    f"GET {RUTA_MODELOS}. Si tu herramienta habla otro protocolo, apuntala a la ruta del dialecto "
    "que corresponda —para el de Anthropic, la dirección base es el proxy sin `/v1` ni "
    "`/anthropic`, porque el cliente agrega `/v1/messages`—; no lo reenvío a ciegas."
)

#: Cada cuánto, como máximo, se manda un aviso igual al escritorio.
MINUTOS_ENTRE_AVISOS = 5

#: Tamaño de los trozos con que se reenvía el streaming: nada se guarda entero en memoria.
TROZO = 8192

#: Lo que el registro guarda y lo que no. Va en los avisos y en los textos de usuario,
#: para que nadie crea que acá se puede leer lo que se mandó.
AVISO_SIN_CONTENIDO = (
    "el registro tiene metadatos —proveedor, modelo, país, caracteres, hash y si iba "
    "minimizado—; el contenido de lo que se mandó no se guarda en ninguna parte"
)

RUTA_CONFIG = pathlib.Path.home() / ".openlegal" / "ia_proxy.json"

#: Configuración por defecto. Todo se puede cambiar en el archivo o en `guardar_config`.
PORDEFECTO: dict[str, Any] = {
    #: 'deepseek', 'openai', 'anthropic', 'local' o lo que sea: es una etiqueta del registro.
    "proveedor": "",
    #: La dirección aguas arriba del dialecto de OpenAI, hasta el /v1. Acá se apunta al proveedor real.
    "base_url": "https://api.deepseek.com/v1",
    #: La dirección aguas arriba del dialecto de Anthropic (la de `/v1/messages`). Ojo: la del
    #: harness NO es esta, sino la del proxy (`http://127.0.0.1:8790`), porque su adaptador le
    #: agrega `/v1/messages` él mismo.
    "base_url_anthropic": BASE_URL_ANTHROPIC_POR_DEFECTO,
    #: Modelo que se anota cuando el pedido no trae `model`.
    "modelo_por_defecto": "",
    #: La clave del proveedor. Vive acá y en ningún otro lado: nunca se registra ni se imprime.
    "api_key": "",
    #: País de destino (si se deja vacío se toma el del proveedor, de `ia.PROVEEDORES`).
    "destino_pais": "",
    #: La causa de los envíos que no traen la cabecera `X-OpenLegal-Causa`.
    "causa_por_defecto": None,
    #: Minimizar por defecto: es la regla del estudio, no una comodidad.
    "minimizar": True,
    #: Si se permite (y se registra) un envío de una causa sin autorización vigente.
    "permitir_sin_autorizacion": False,
    #: Avisar además por el escritorio, con tope.
    "avisar_escritorio": True,
    "puerto": PUERTO_POR_DEFECTO,
    #: Base del CRM donde se registra. Vacío = la del CRM (LEGALCRM_DB_URL / ~/.openlegal).
    "base_de_datos": None,
    #: Segundos de espera hablando aguas arriba (un modelo con razonamiento tarda).
    "espera_segundos": 600,
}

#: Cabeceras que no se reenvían ni se devuelven: son de la conexión, no del pedido.
CABECERAS_SALTADAS = {
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "accept-encoding", "te", "upgrade", "proxy-authorization", "trailer",
}

#: Cabeceras con credenciales: las pone el proxy, nunca se copian del cliente sin mirar.
CABECERAS_DE_CLAVE = ("authorization", "x-api-key")


class ErrorDelProxy(ValueError):
    """Un envío que el proxy no atiende, con el código HTTP que le corresponde."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo


# ====================================================================== config
def ruta_config() -> pathlib.Path:
    """Dónde está la configuración, resolviéndola al usarla.

    Se resuelve en el momento (y no al importar) por la misma razón que en `notificaciones`:
    que la variable de entorno valga aunque se defina después, y que las pruebas puedan
    apuntar a un archivo temporal sin tocar la configuración real del estudio.
    """
    return pathlib.Path(os.environ.get("OPENLEGAL_IA_PROXY_CONFIG") or RUTA_CONFIG)


def cargar_config(ruta: pathlib.Path | None = None) -> dict:
    """La configuración vigente: los valores por defecto, pisados por el archivo."""
    archivo = ruta or ruta_config()
    config = dict(PORDEFECTO)
    if archivo.is_file():
        try:
            guardado = json.loads(archivo.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{archivo} no es JSON válido: {exc}") from exc
        if not isinstance(guardado, dict):
            raise ValueError(f"{archivo} tiene que ser un objeto JSON con la configuración del proxy")
        # Un `null` en el archivo significa «déjalo en el valor por defecto»: escribir null
        # no puede apagar la minimización sin que nadie lo note.
        config.update({clave: valor for clave, valor in guardado.items() if valor is not None})
    if config.get("causa_por_defecto"):
        config["causa_por_defecto"] = int(config["causa_por_defecto"])
    config["puerto"] = int(config.get("puerto") or PUERTO_POR_DEFECTO)
    return config


def guardar_config(cambios: dict, ruta: pathlib.Path | None = None) -> dict:
    """Guarda la configuración **con permisos 600** y de forma atómica.

    El archivo tiene la `api_key` del proveedor: modo 600 (sólo el dueño) y escritura atómica
    —se arma aparte y se reemplaza—, igual que `~/.openlegal/notificaciones.json`. Un valor
    vacío o `None` significa «no lo cambies», así que guardar el puerto no borra la clave.
    """
    if not isinstance(cambios, dict):
        raise ValueError("la configuración tiene que ser un objeto")
    archivo = ruta or ruta_config()
    actual: dict = {}
    if archivo.is_file():
        try:
            guardado = json.loads(archivo.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{archivo} no es JSON válido: corrígelo antes de guardar") from exc
        if isinstance(guardado, dict):
            actual = guardado
    for clave, valor in cambios.items():
        # Vacío o None = «no lo cambies» (no es lo mismo que borrar): es la misma regla que
        # sigue el módulo de avisos con las claves.
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            continue
        actual[clave] = valor
    archivo.parent.mkdir(parents=True, exist_ok=True)
    temporal = archivo.with_name(archivo.name + ".nuevo")
    descriptor = os.open(temporal, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as salida:
        salida.write(json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True))
        salida.flush()
        os.fsync(salida.fileno())
    os.chmod(temporal, 0o600)
    os.replace(temporal, archivo)
    return actual


def permisos_del_archivo(archivo: pathlib.Path | None = None) -> str:
    """Los permisos del archivo en texto ('600', '644'), o 'no está'."""
    ruta = archivo or ruta_config()
    try:
        return oct(ruta.stat().st_mode & 0o777)[2:].zfill(3)
    except OSError:
        return "no está"


def proveedor_inferido(base_url: str) -> str:
    """A qué proveedor apunta esa dirección, por el dominio. Cadena vacía si no lo reconozco.

    Es una comodidad para el registro, no una verdad: si el estudio sabe a quién le manda,
    que lo declare en `proveedor` y manda la configuración.
    """
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    if not host:
        return ""
    conocidos = {
        "deepseek": ("deepseek.com",),
        "openai": ("openai.com", "openai.azure.com"),
        "anthropic": ("anthropic.com",),
        "local": ("localhost", "127.0.0.1", "::1", "0.0.0.0"),
    }
    for nombre, dominios in conocidos.items():
        if any(host == dominio or host.endswith("." + dominio) for dominio in dominios):
            return nombre
    return ""


def proveedor_de(config: dict, clave_base: str = "base_url") -> str:
    """El nombre del proveedor para el registro: lo declarado, lo inferido o 'otro'.

    La dirección por la que se infiere es la del dialecto del pedido (`base_url` o
    `base_url_anthropic`): si no está declarado, un proveedor puede cambiar de dirección según el
    dialecto y el registro tiene que decir a dónde salió *ese* envío.
    """
    declarado = str(config.get("proveedor") or "").strip()
    return declarado or proveedor_inferido(str(config.get(clave_base) or "")) or "otro"


def pais_de(config: dict, proveedor: str) -> str:
    """El país de destino que se anota. Nunca se inventa: si no se sabe, se dice."""
    declarado = str(config.get("destino_pais") or "").strip()
    if declarado:
        return declarado
    datos = ia.PROVEEDORES.get(proveedor)
    return str(datos["pais"]) if datos else "sin verificar"


def activo(puerto: int | None = None, direccion: str = DIRECCION) -> bool:
    """¿Hay algo escuchando en esa dirección? (para que el panel diga si el proxy corre)."""
    puerto = int(puerto or cargar_config()["puerto"])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonda:
        sonda.settimeout(0.3)
        return sonda.connect_ex((direccion, puerto)) == 0


def resumen(config: dict | None = None) -> dict:
    """El estado del proxy **sin secretos**: es lo que ven la terminal y el panel.

    La `api_key` no sale nunca de acá: lo único que se dice es si está configurada o falta.
    """
    config = config or cargar_config()
    archivo = ruta_config()
    proveedor = proveedor_de(config)
    puerto = int(config.get("puerto") or PUERTO_POR_DEFECTO)
    permisos = permisos_del_archivo(archivo)
    avisos: list[str] = []
    if not archivo.is_file():
        avisos.append(
            f"no hay configuración en {archivo}: el proxy usa los valores por defecto "
            "(conviene crear el archivo con 'proveedor' y 'base_url' para que el registro "
            "diga a quién salió cada envío)"
        )
    elif permisos not in ("600", "400"):
        avisos.append(
            f"los permisos de {archivo} son {permisos}: deberían ser 600, porque ahí está la "
            "api_key del proveedor"
        )
    if not str(config.get("api_key") or "").strip():
        avisos.append(
            "no hay api_key configurada: el proxy sólo sirve si el cliente manda su propia "
            "cabecera Authorization (y entonces la clave no vive en el proxy)"
        )
    if not str(config.get("base_url_anthropic") or "").strip():
        avisos.append(
            "no hay 'base_url_anthropic' configurada: los pedidos del dialecto Anthropic "
            "(POST /v1/messages, los del harness) van a ser rechazados en vez de salir. Poné ahí "
            "la dirección de ese dialecto (por ejemplo https://api.deepseek.com/anthropic)."
        )
    if not str(config.get("proveedor") or "").strip() and not proveedor_inferido(str(config.get("base_url") or "")):
        avisos.append(
            f"no reconozco el proveedor por la dirección aguas arriba ({config.get('base_url')}): "
            "declara 'proveedor' en el archivo para que el registro diga a quién salió el envío"
        )
    return {
        "archivo": str(archivo),
        "permisos": permisos,
        "puerto": puerto,
        "url": f"http://{DIRECCION}:{puerto}/v1",
        # La dirección del harness: el adaptador le agrega `/v1/messages` él mismo, así que va
        # SIN `/v1` y SIN `/anthropic`. Es el error más fácil de cometer y el más difícil de ver.
        "url_anthropic": f"http://{DIRECCION}:{puerto}",
        "activo": activo(puerto),
        "proveedor": proveedor,
        "base_url": config.get("base_url"),
        "base_url_anthropic": config.get("base_url_anthropic"),
        "modelo_por_defecto": config.get("modelo_por_defecto") or None,
        "destino_pais": pais_de(config, proveedor),
        "minimizar": bool(config.get("minimizar")),
        "permitir_sin_autorizacion": bool(config.get("permitir_sin_autorizacion")),
        "avisar_escritorio": bool(config.get("avisar_escritorio")),
        "causa_por_defecto": config.get("causa_por_defecto"),
        "api_key": "configurada" if str(config.get("api_key") or "").strip() else "falta",
        "base_de_datos": config.get("base_de_datos") or "(la del CRM)",
        "via": VIA,
        "via_anthropic": VIA_ANTHROPIC,
        "avisos": avisos,
    }


# ================================================================== minimizar
def _mensajes(payload: dict) -> list:
    mensajes = payload.get("messages")
    if not isinstance(mensajes, list) or not mensajes:
        raise ValueError("el pedido no trae 'messages': no sé qué texto va a salir")
    return mensajes


def texto_del_payload(payload: dict) -> str:
    """El texto que sale hacia el modelo, tal como se va a mandar.

    Es sobre esto —y no sobre el JSON completo— que se cuenta el largo y se calcula el hash:
    lo que se registra es el dato que salió, no los parámetros de la llamada. Si el pedido
    trae contenido que no es texto (una imagen adjunta, por ejemplo), se levanta `ValueError`
    con el motivo: quien decide qué hacer con eso es el llamador.
    """
    partes: list[str] = []
    for indice, mensaje in enumerate(_mensajes(payload)):
        if not isinstance(mensaje, dict):
            raise ValueError(f"el mensaje {indice} no es un objeto JSON")
        contenido = mensaje.get("content")
        if contenido is None:
            continue
        if isinstance(contenido, str):
            partes.append(contenido)
            continue
        if isinstance(contenido, list):
            for parte in contenido:
                if isinstance(parte, dict) and isinstance(parte.get("text"), str):
                    partes.append(parte["text"])
                else:
                    raise ValueError(
                        f"el mensaje {indice} trae contenido que no sé minimizar (adjuntos, "
                        "imágenes o datos en base64): mándalo como texto plano"
                    )
            continue
        raise ValueError(
            f"el mensaje {indice} trae contenido que no sé minimizar ({type(contenido).__name__})"
        )
    return "\n".join(partes)


def minimizar_payload(
    payload: dict, terminos: list[str] | None = None, avisos: list[str] | None = None
) -> tuple[dict, int]:
    """Devuelve (payload con los textos minimizados, cuántos marcadores puso).

    Trabaja sobre una copia: el payload original no se toca. Los términos son los de la ficha
    de la causa, y conviene sumar los nombres propios del documento —el CRM no los conoce y
    lo que no se declara no se enmascara—.
    """
    limpio = json.loads(json.dumps(payload))
    marcadores = 0
    for mensaje in _mensajes(limpio):
        contenido = mensaje.get("content")
        if isinstance(contenido, str):
            nuevo, mapa = ia.redactar(contenido, terminos, avisos)
            mensaje["content"] = nuevo
            marcadores += len(mapa)
    return limpio, marcadores


def _huella(texto: str) -> tuple[int, str]:
    return len(texto), ia.hash_payload(texto)


# ====================================================== minimizar (Anthropic)
#: En el dialecto de Anthropic los bloques que sí se pueden minimizar.
TIPOS_DE_TEXTO = ("text", "thinking")

#: Bloques que pasan sin tocarse: son un cifrado del proveedor, no texto de la causa.
TIPOS_OPACOS = ("redacted_thinking",)


def _mensajes_anthropic(payload: dict) -> list:
    """Los mensajes del dialecto de Anthropic. Un pedido sin ellos no se sabe qué manda."""
    mensajes = payload.get("messages")
    if not isinstance(mensajes, list) or not mensajes:
        raise ValueError("el pedido no trae 'messages': no sé qué texto va a salir")
    return mensajes


def _campos_de_json(valor: Any, contenedor: Any = None, clave: Any = None) -> list[tuple[Any, Any, str]]:
    """Todos los textos de un JSON cualquiera (el `input` de un `tool_use`, por ejemplo).

    Se devuelven como (contenedor, clave, texto) para poder reemplazarlos en su lugar. La
    estructura —claves, números, booleanos— no se toca: sólo se minimiza lo que es texto.
    """
    if isinstance(valor, str):
        return [(contenedor, clave, valor)]
    if isinstance(valor, dict):
        campos: list[tuple[Any, Any, str]] = []
        for clave_hija, valor_hijo in valor.items():
            campos += _campos_de_json(valor_hijo, valor, clave_hija)
        return campos
    if isinstance(valor, list):
        campos = []
        for indice, valor_hijo in enumerate(valor):
            campos += _campos_de_json(valor_hijo, valor, indice)
        return campos
    return []


def _campos_del_bloque(bloque: dict, donde: str) -> list[tuple[Any, Any, str]]:
    """Los textos minimizables de un bloque de contenido del dialecto de Anthropic.

    Se minimiza lo que es texto: los bloques de texto, el razonamiento (`thinking`), el contenido
    de un `tool_result` (por ahí vuelven los datos que sacó una herramienta del CRM) y los textos
    del `input` de un `tool_use`. Un bloque que no se puede minimizar —una imagen, un documento en
    base64— **no** se saltea en silencio: levanta `ValueError` y el pedido no se reenvía.
    """
    tipo = str(bloque.get("type") or "text")
    if tipo in TIPOS_DE_TEXTO:
        texto = bloque.get(tipo)
        if not isinstance(texto, str):
            raise ValueError(f"{donde} es un bloque de tipo {tipo!r} pero no trae texto")
        return [(bloque, tipo, texto)]
    if tipo in TIPOS_OPACOS:
        return []
    if tipo == "tool_use":
        return _campos_de_json(bloque.get("input"))
    if tipo == "tool_result":
        return _campos_de_bloque_contenido(
            bloque.get("content"), bloque, "content", f"{donde} (resultado de una herramienta)"
        )
    raise ValueError(
        f"{donde} es un bloque de tipo {tipo!r}, que no sé minimizar (imágenes, documentos en "
        "base64 u otro contenido adjunto): mándalo como texto plano"
    )


def _campos_de_bloque_contenido(
    valor: Any, contenedor: Any, clave: Any, donde: str
) -> list[tuple[Any, Any, str]]:
    """Los textos de un `system` o de un `content`: una cadena, o una lista de bloques."""
    if valor is None:
        return []
    if isinstance(valor, str):
        return [(contenedor, clave, valor)]
    if not isinstance(valor, list):
        raise ValueError(f"{donde} trae contenido que no sé minimizar ({type(valor).__name__})")
    campos: list[tuple[Any, Any, str]] = []
    for indice, bloque in enumerate(valor):
        if not isinstance(bloque, dict):
            raise ValueError(f"{donde} trae un bloque que no es un objeto JSON (posición {indice})")
        campos += _campos_del_bloque(bloque, f"{donde}, bloque {indice}")
    return campos


def _campos_de_herramientas(payload: dict) -> list[tuple[Any, Any, str]]:
    """Las descripciones de las `tools`: texto que escribió el estudio y puede llevar datos.

    Sí se minimizan (una descripción puede contar de qué va la causa). Los `input_schema` no: son
    la estructura de la herramienta y enmascarar un valor de ahí la rompería aguas arriba.
    """
    herramientas = payload.get("tools")
    if herramientas is None:
        return []
    if not isinstance(herramientas, list):
        raise ValueError("'tools' tiene que ser una lista de herramientas")
    campos: list[tuple[Any, Any, str]] = []
    for indice, herramienta in enumerate(herramientas):
        if not isinstance(herramienta, dict):
            raise ValueError(f"la herramienta {indice} no es un objeto JSON")
        descripcion = herramienta.get("description")
        if isinstance(descripcion, str) and descripcion:
            campos.append((herramienta, "description", descripcion))
        elif descripcion is not None and not isinstance(descripcion, str):
            raise ValueError(f"la herramienta {indice} trae una 'description' que no es texto")
    return campos


def campos_de_texto_anthropic(payload: dict) -> list[tuple[Any, Any, str]]:
    """Todo el texto del pedido, en el dialecto de Anthropic, como (contenedor, clave, texto).

    Levanta `ValueError` con el motivo si hay algo que no se puede minimizar: quien decide qué
    hacer con eso es el llamador (el proxy no reenvía).
    """
    campos: list[tuple[Any, Any, str]] = []
    campos += _campos_de_bloque_contenido(payload.get("system"), payload, "system", "el 'system'")
    for indice, mensaje in enumerate(_mensajes_anthropic(payload)):
        if not isinstance(mensaje, dict):
            raise ValueError(f"el mensaje {indice} no es un objeto JSON")
        campos += _campos_de_bloque_contenido(
            mensaje.get("content"), mensaje, "content", f"el mensaje {indice}"
        )
    campos += _campos_de_herramientas(payload)
    return campos


def texto_del_payload_anthropic(payload: dict) -> str:
    """El texto que sale hacia el modelo, tal como se va a mandar (dialecto Anthropic).

    Es sobre esto —y no sobre el JSON completo— que se cuenta el largo y se calcula el hash: lo
    que se registra es el dato que salió, no los parámetros de la llamada.
    """
    return "\n".join(texto for _, _, texto in campos_de_texto_anthropic(payload))


def minimizar_payload_anthropic(
    payload: dict, terminos: list[str] | None = None, avisos: list[str] | None = None
) -> tuple[dict, int]:
    """Devuelve (payload minimizado, cuántos marcadores puso) para el dialecto de Anthropic.

    Trabaja sobre una copia: el payload original no se toca. Se recorre el `system`, los bloques
    de cada `content`, el contenido de los `tool_result`, los textos de los `input` de los
    `tool_use` y las descripciones de las `tools` —y nada más—: un bloque que no se puede
    minimizar levanta `ValueError` y no se reenvía.
    """
    limpio = json.loads(json.dumps(payload))
    marcadores = 0
    for contenedor, clave, texto in campos_de_texto_anthropic(limpio):
        nuevo, mapa = ia.redactar(texto, terminos, avisos)
        contenedor[clave] = nuevo
        marcadores += len(mapa)
    return limpio, marcadores


# =================================================================== dialectos
@dataclass(frozen=True)
class Dialecto:
    """Un dialecto de la API que este proxy atiende.

    Junta lo que cambia entre uno y otro: la ruta que se escucha, la clave de configuración con la
    dirección aguas arriba, la ruta que se le agrega a esa dirección al reenviar, cómo se saca el
    texto del pedido (para contar, hashear y minimizar) y a qué `via` se registra.
    """

    nombre: str
    via: str
    clave_base: str
    ruta_aguas_arriba: str
    texto: Callable[[dict], str]
    minimizar: Callable[..., tuple[dict, int]]
    #: Dónde cuelga el catálogo de modelos de esa dirección aguas arriba (los dos dialectos tienen
    #: un `GET /v1/models`, pero en una dirección base distinta).
    ruta_modelos: str = "/models"
    de_anthropic: bool = False

    def base_url(self, config: dict) -> str:
        """La dirección aguas arriba que le corresponde, sin la barra final.

        En el dialecto de Anthropic se tolera un `/v1` de más al final —la ruta que se agrega ya
        lo trae—, que es el error de dedo más común al configurarlo.
        """
        base = str(config.get(self.clave_base) or "").strip().rstrip("/")
        if self.de_anthropic and base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base


OPENAI = Dialecto(
    nombre="openai",
    via=VIA_OPENAI,
    clave_base="base_url",
    ruta_aguas_arriba="/chat/completions",
    texto=texto_del_payload,
    minimizar=minimizar_payload,
)

ANTHROPIC = Dialecto(
    nombre="anthropic",
    via=VIA_ANTHROPIC,
    clave_base="base_url_anthropic",
    ruta_aguas_arriba="/v1/messages",
    texto=texto_del_payload_anthropic,
    minimizar=minimizar_payload_anthropic,
    # La dirección base del dialecto es la del proveedor sin `/v1` (en Anthropic es
    # `https://api.anthropic.com`; en DeepSeek, `https://api.deepseek.com/anthropic`).
    ruta_modelos="/v1/models",
    de_anthropic=True,
)

#: Las rutas de escritura que atiende el proxy, con su dialecto.
DIALECTOS: dict[str, Dialecto] = {RUTA_CHAT: OPENAI, RUTA_MESSAGES: ANTHROPIC}


def dialecto_probable(ruta: str) -> Dialecto:
    """Con qué dialecto contestar una ruta que no se atiende: se mira la ruta, y si no, OpenAI."""
    return ANTHROPIC if "messages" in ruta else OPENAI


def error_api(codigo: int, dialecto: Dialecto, mensaje: str, tipo: str | None = None) -> dict:
    """El cuerpo de un error, en el dialecto que el cliente entiende.

    El mensaje es el mismo —una frase accionable, con qué hacer—; lo que cambia es el sobre: el de
    Anthropic lleva `{"type": "error", "error": {...}}` con un tipo estándar del dialecto (los
    clientes de Anthropic miran ahí), y el de OpenAI, su `{"error": {...}}` de siempre.
    """
    if dialecto.de_anthropic:
        return {
            "type": "error",
            "error": {"type": TIPOS_ANTHROPIC.get(codigo, "api_error"), "message": mensaje},
        }
    return {"error": {"message": mensaje, "type": tipo or "error", "code": codigo}}


# =================================================================== registro
def registrar(
    db: DB,
    *,
    proveedor: str,
    modelo: str | None,
    destino_pais: str | None,
    texto: str,
    redactado: bool = False,
    causa_id: int | None = None,
    estudio_id: int | None = None,
    autorizacion_id: int | None = None,
    bloqueado: bool = False,
    motivo_bloqueo: str | None = None,
    usuario_id: int | None = None,
    via: str = VIA,
) -> dict:
    """Anota un envío (o un intento bloqueado) en `transferencias_ia`, con origen 'proxy'.

    Guarda metadatos, nunca contenido: proveedor, modelo, país, caracteres, hash SHA-256 del
    texto, si iba minimizado y —cuando se pudo averiguar— la causa, su autorización y el
    estudio. `via` dice por qué dialecto salió (`openai-compat` o `anthropic-compat`). Deja
    además su entrada en `auditoria`, que es la bitácora del estudio.
    """
    caracteres, huella = _huella(texto)
    transferencia_id = db.insertar(
        "transferencias_ia",
        {
            "causa_id": causa_id,
            "autorizacion_id": autorizacion_id,
            "usuario_id": usuario_id,
            "proveedor": proveedor,
            "modelo": modelo,
            "destino_pais": destino_pais,
            "documentos": None,
            "caracteres": caracteres,
            "hash_payload": huella,
            "redactado": 1 if redactado else 0,
            "origen": "proxy",
            "via": via,
            "bloqueado": 1 if bloqueado else 0,
            "motivo_bloqueo": motivo_bloqueo,
            "estudio_id": estudio_id,
        },
    )
    detalle = (
        f"origen=proxy via={via} proveedor={proveedor} modelo={modelo or 's/i'} "
        f"causa={causa_id or 'sin causa'} caracteres={caracteres} "
        f"redactado={'si' if redactado else 'no'}"
    )
    if bloqueado:
        auth.auditar(
            db, estudio_id, usuario_id, "ia.sin_autorizacion", "transferencias_ia",
            transferencia_id, f"{detalle} bloqueado: {motivo_bloqueo}",
        )
    else:
        auth.auditar(db, estudio_id, usuario_id, "ia.comunicar", "transferencias_ia", transferencia_id, detalle)
    return {
        "id": transferencia_id,
        "proveedor": proveedor,
        "modelo": modelo,
        "destino_pais": destino_pais,
        "caracteres": caracteres,
        "hash_payload": huella,
        "redactado": bool(redactado),
        "causa_id": causa_id,
        "bloqueado": bool(bloqueado),
        "motivo_bloqueo": motivo_bloqueo,
    }


def autorizacion_vigente(db: DB, causa_id: int) -> dict | None:
    """La autorización de IA vigente de una causa, si la hay."""
    return db.uno(
        "SELECT * FROM autorizaciones_ia WHERE causa_id = ? AND vigente = 1 ORDER BY id DESC",
        (causa_id,),
    )


def envios_recientes(
    db: DB,
    *,
    causa_id: int | None = None,
    solo_bloqueados: bool = False,
    limite: int = 50,
    visibles: list[int] | None = None,
    estudio_id: int | None = None,
) -> list[dict]:
    """Los últimos envíos, como metadatos. Nunca devuelve contenido: no se guarda.

    `visibles` acota a las causas que ese usuario puede ver (el mismo criterio que el resto
    del CRM). Un envío **sin causa** no se puede acotar por causa: se muestra al estudio al
    que quedó atribuido y, cuando no se pudo averiguar el estudio, a quien puede leer IA en
    esta instalación —el proxy es local, así que no hay tercero—, y eso queda dicho acá.
    """
    condiciones: list[str] = []
    parametros: list[Any] = []
    if causa_id is not None:
        condiciones.append("t.causa_id = ?")
        parametros.append(causa_id)
    elif visibles is not None:
        sin_estudio = "(t.estudio_id = ? OR t.estudio_id IS NULL)"
        if visibles:
            marcadores = ", ".join("?" for _ in visibles)
            condiciones.append(
                f"(t.causa_id IN ({marcadores}) OR (t.causa_id IS NULL AND {sin_estudio}))"
            )
            parametros.extend(sorted(visibles))
        else:
            condiciones.append(f"(t.causa_id IS NULL AND {sin_estudio})")
        parametros.append(estudio_id if estudio_id is not None else -1)
    if solo_bloqueados:
        condiciones.append("t.bloqueado = 1")
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return db.todos(
        "SELECT t.id, t.causa_id, t.autorizacion_id, t.origen, t.via, t.proveedor, t.modelo, "
        "t.destino_pais, t.caracteres, t.hash_payload, t.redactado, t.bloqueado, t.motivo_bloqueo, "
        "t.creado_en, c.caratula, u.nombre AS usuario "
        "FROM transferencias_ia t "
        "LEFT JOIN causas c ON c.id = t.causa_id "
        "LEFT JOIN usuarios u ON u.id = t.usuario_id "
        f"{donde} ORDER BY t.id DESC LIMIT ?",
        (*parametros, max(1, int(limite))),
    )


def autorizaciones(
    db: DB, *, causa_id: int | None = None, visibles: list[int] | None = None, limite: int = 100
) -> list[dict]:
    """Las autorizaciones de IA registradas por causa, con quién las puso."""
    condiciones: list[str] = []
    parametros: list[Any] = []
    if causa_id is not None:
        condiciones.append("a.causa_id = ?")
        parametros.append(causa_id)
    elif visibles is not None:
        if not visibles:
            return []
        marcadores = ", ".join("?" for _ in visibles)
        condiciones.append(f"a.causa_id IN ({marcadores})")
        parametros.extend(sorted(visibles))
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return db.todos(
        "SELECT a.id, a.causa_id, a.alcance, a.base_licitud, a.titular, a.vigente, a.creado_en, "
        "a.revocada_en, c.caratula, u.nombre AS registrado_por_nombre "
        "FROM autorizaciones_ia a "
        "LEFT JOIN causas c ON c.id = a.causa_id "
        "LEFT JOIN usuarios u ON u.id = a.registrado_por "
        f"{donde} ORDER BY a.id DESC LIMIT ?",
        (*parametros, max(1, int(limite))),
    )


# ===================================================================== avisos
def resumen_de_envio(
    *,
    proveedor: str,
    modelo: str | None,
    pais: str | None,
    causa_id: int | None,
    caracteres: int,
    hash_payload: str,
    minimizado: bool,
    marcadores: int = 0,
) -> str:
    """El resumen de un envío, en una línea. Sin contenido y sin claves, por construcción."""
    partes = [
        f"{proveedor}/{modelo or 'modelo s/i'}",
        f"destino={pais or 'sin verificar'}",
        f"causa {causa_id}" if causa_id else "SIN CAUSA (no se puede atribuir a un expediente)",
        f"{caracteres} caracteres",
        f"hash={hash_payload[:16]}…",
        f"minimizado={'sí' if minimizado else 'no'}"
        + (f" ({marcadores} marcadores)" if minimizado and marcadores else ""),
    ]
    return " | ".join(partes)


def notificar_escritorio(titulo: str, cuerpo: str, bloqueado: bool = False) -> None:
    """Manda el aviso al escritorio con `notify-send`. Si no está, no se inventa nada."""
    ejecutable = shutil.which("notify-send")
    if not ejecutable:
        raise FileNotFoundError("no hay notify-send en este equipo")
    subprocess.run(
        [ejecutable, "-a", "Open Legal Harness", "-u", "critical" if bloqueado else "normal", titulo, cuerpo],
        check=False,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Avisador:
    """Un aviso por envío: siempre por la terminal; al escritorio con tope.

    El tope (5 minutos por defecto) es para que una sesión de trabajo con el harness no tape
    la pantalla de avisos. Los envíos **bloqueados** no pasan por el tope: son pocos y son
    justamente los que hay que ver.
    """

    def __init__(
        self,
        config: dict,
        salida: Callable[[str], Any] = print,
        notificar: Callable[..., None] | None = None,
        intervalo_segundos: int = MINUTOS_ENTRE_AVISOS * 60,
    ):
        self.config = config
        self.salida = salida
        self.notificar = notificar or notificar_escritorio
        self.intervalo = intervalo_segundos
        self._ultimo: dt.datetime | None = None
        self._sin_escritorio = False
        self._candado = threading.Lock()

    def envio(
        self, resumen: str, bloqueado: bool = False, ahora: dt.datetime | None = None
    ) -> bool:
        """Avisa de un envío. Devuelve si además salió al escritorio."""
        self.salida(f"{'IA BLOQUEADA' if bloqueado else 'IA ENVIADA'}: {resumen}")
        if not self.config.get("avisar_escritorio"):
            return False
        momento = ahora or dt.datetime.now()
        with self._candado:
            if not bloqueado and self._ultimo is not None and momento - self._ultimo < dt.timedelta(seconds=self.intervalo):
                self.salida(
                    f"  (no lo mando al escritorio: ya avisé hace menos de {self.intervalo // 60} minutos)"
                )
                return False
            self._ultimo = momento
        if self._sin_escritorio:
            return False
        try:
            self.notificar("Open Legal Harness: uso de IA", resumen, bloqueado)
        except FileNotFoundError:
            self._sin_escritorio = True
            self.salida(
                "  AVISO: no encontré notify-send en este equipo: los avisos van a salir sólo "
                "por esta terminal (el envío quedó registrado igual)"
            )
            return False
        except (OSError, subprocess.SubprocessError) as exc:
            self.salida(f"  AVISO: no pude avisar al escritorio ({exc})")
            return False
        return True


# =================================================================== el proxy
class ServidorIA(ThreadingHTTPServer):
    """El servidor del proxy: su configuración, su base y su avisador."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, direccion: tuple[str, int], config: dict, base_de_datos: str | None = None):
        super().__init__(direccion, _Manejador)
        self.config = config
        self.db_url = base_de_datos or config.get("base_de_datos")
        self.proveedor = proveedor_de(config)
        self.destino_pais = pais_de(config, self.proveedor)
        self.avisador = Avisador(config)
        self._db: DB | None = None
        self._candado_db = threading.Lock()

    def db(self) -> DB:
        """La base del CRM, abierta una vez y migrada. Falla si no se puede: no hay registro."""
        with self._candado_db:
            if self._db is None:
                db = DB(self.db_url)
                db.migrar()
                self._db = db
            return self._db

    def cerrar(self) -> None:
        with self._candado_db:
            if self._db is not None:
                self._db.cerrar()
                self._db = None

    def estudio_de(self, db: DB, causa_id: int | None) -> int | None:
        """A qué estudio pertenece el envío. Se pregunta; si no se puede saber, queda nulo."""
        if causa_id is not None:
            fila = db.uno("SELECT estudio_id FROM causas WHERE id = ?", (causa_id,))
            return int(fila["estudio_id"]) if fila and fila.get("estudio_id") is not None else None
        estudios = db.todos("SELECT id FROM estudios ORDER BY id LIMIT 2")
        return int(estudios[0]["id"]) if len(estudios) == 1 else None

    def datos_del_dialecto(self, dialecto: Dialecto) -> tuple[str, str]:
        """Proveedor y país de destino de un envío, según el dialecto por el que sale.

        Lo declarado manda; si no, se infiere de la dirección **de ese dialecto** (`base_url` o
        `base_url_anthropic`), porque un mismo proveedor puede estar en dos direcciones y el
        registro tiene que decir a cuál salió este envío.
        """
        proveedor = proveedor_de(self.config, dialecto.clave_base)
        return proveedor, pais_de(self.config, proveedor)


class _Manejador(BaseHTTPRequestHandler):
    """Atiende los pedidos: traduce, avisa, decide y reenvía. Nunca toca el contenido."""

    protocol_version = "HTTP/1.1"
    server_version = "OpenLegalIAProxy/1.0"
    server: ServidorIA

    def log_message(self, formato: str, *args: Any) -> None:
        # El log de acceso por defecto va a stderr con fecha y código; acá se deja corto, en
        # una línea, y SIN el cuerpo ni las cabeceras (por ahí viajaría la api_key).
        self._escribir(f"· {self.command} {self.path}")

    def _escribir(self, linea: str) -> None:
        print(linea, flush=True)

    # ------------------------------------------------------------- respuestas
    def _json(self, codigo: int, datos: dict) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)
        self.wfile.flush()

    def _error_api(self, codigo: int, mensaje: str, tipo: str | None = None, dialecto: Dialecto = OPENAI) -> None:
        """Los errores salen en el dialecto del cliente, con el mensaje que el estudio entiende.

        El mensaje es el mismo —qué pasó y qué hacer—; lo que cambia es el sobre: el del dialecto
        de Anthropic lleva su `{"type": "error", "error": {...}}`, que es lo que miran sus clientes.
        """
        self._json(codigo, error_api(codigo, dialecto, mensaje, tipo))

    def _reenviar(self, respuesta: Any, streaming: bool) -> None:
        """Copia la respuesta aguas arriba tal cual (o por trozos, si es SSE).

        En streaming se lee con `read1`: devuelve lo que haya llegado en una sola lectura, en
        vez de esperar a juntar un bloque entero. Con `read(n)` —que bloquea hasta tener los n
        bytes— el proxy se tragaría la respuesta completa antes de reenviar la primera línea y
        el abogado vería el texto aparecer de una vez al final, que es exactamente lo que no
        queremos de un pasamanos.
        """
        for nombre, valor in respuesta.headers.items():
            if nombre.lower() in CABECERAS_SALTADAS or nombre.lower() == "content-length":
                continue
            self.send_header(nombre, valor)
        if streaming:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                trozo = respuesta.read1(TROZO) if hasattr(respuesta, "read1") else respuesta.read(TROZO)
                if not trozo:
                    break
                self.wfile.write(f"{len(trozo):X}\r\n".encode("ascii"))
                self.wfile.write(trozo)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        datos = respuesta.read()
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)
        self.wfile.flush()

    # ------------------------------------------------------------- pedidos
    def do_GET(self) -> None:  # noqa: N802 (lo nombra la librería estándar)
        ruta = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if ruta == RUTA_SALUD:
            self._json(
                200,
                {
                    "ok": True,
                    "proveedor": self.server.proveedor,
                    "via": VIA,
                    "vias": [VIA_OPENAI, VIA_ANTHROPIC],
                },
            )
            return
        if ruta != RUTA_MODELOS:
            # Nadie lee el cuerpo de una ruta que no se atiende: la conexión se cierra acá para
            # que esos bytes no queden en el buffer y la librería los lea como el pedido siguiente
            # (un «Bad request syntax» que no causó nadie).
            self.close_connection = True
            self._error_api(404, AYUDA_RUTAS, dialecto=dialecto_probable(ruta))
            return
        # El catálogo es el mismo en los dos dialectos: se reenvía a la dirección del dialecto que
        # lo pide. Lo que lo delata es `anthropic-version`, que sólo manda un cliente de Anthropic.
        dialecto = ANTHROPIC if (self.headers.get("anthropic-version") or "").strip() else OPENAI
        conexion = None
        try:
            conexion, respuesta = self._abrir_aguas_arriba(
                None, dialecto, metodo="GET", ruta=dialecto.ruta_modelos
            )
            self.send_response(respuesta.status)
            self._reenviar(respuesta, streaming=False)
        except ErrorDelProxy as exc:
            self._error_api(exc.codigo, str(exc), dialecto=dialecto)
        finally:
            if conexion is not None:
                conexion.close()

    def do_POST(self) -> None:  # noqa: N802
        ruta = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        dialecto = DIALECTOS.get(ruta)
        if dialecto is None:
            # El cuerpo no se lee (no hay dialecto que lo entienda) y la conexión se cierra acá,
            # para que esos bytes no se lean después como si fueran el pedido siguiente.
            self.close_connection = True
            self._error_api(404, AYUDA_RUTAS, dialecto=dialecto_probable(ruta))
            return
        try:
            cuerpo = self._leer_cuerpo()
            payload = json.loads(cuerpo.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("el cuerpo tiene que ser un objeto JSON")
        except (ValueError, UnicodeDecodeError) as exc:
            self._error_api(400, f"no pude leer el pedido: {exc}", dialecto=dialecto)
            return
        try:
            self._atender(payload, dialecto)
        except ErrorDelProxy as exc:
            self._error_api(exc.codigo, str(exc), dialecto=dialecto)
        except Exception as exc:  # noqa: BLE001 - el proxy sigue en pie y lo dice
            # Nunca se imprime el pedido ni la respuesta: el nombre del error alcanza.
            self._escribir(f"AVISO: falló un envío en el proxy ({type(exc).__name__})")
            self._error_api(
                500,
                "el proxy falló atendiendo este pedido (mira su salida); no se registró nada",
                dialecto=dialecto,
            )

    def _leer_cuerpo(self) -> bytes:
        largo = self.headers.get("Content-Length")
        if largo is None:
            raise ValueError(
                "el pedido no trae Content-Length: mándalo como cualquier cliente de la API "
                "(el proxy reenvía cuerpo, no `Transfer-Encoding: chunked`)"
            )
        try:
            return self.rfile.read(int(largo))
        except ValueError as exc:
            raise ValueError("Content-Length no es un número") from exc

    def _causa_pedida(self) -> int | None:
        cabecera = (self.headers.get("X-OpenLegal-Causa") or "").strip()
        if cabecera:
            try:
                return int(cabecera)
            except ValueError as exc:
                raise ErrorDelProxy(
                    400,
                    f"la cabecera X-OpenLegal-Causa tiene que ser el número de la causa; llegó {cabecera!r}",
                ) from exc
        por_defecto = self.server.config.get("causa_por_defecto")
        return int(por_defecto) if por_defecto else None

    def _config(self) -> dict:
        return self.server.config

    # ------------------------------------------------------- la decisión
    def _atender(self, payload: dict, dialecto: Dialecto) -> None:
        """Decide y reenvía un pedido, en el dialecto que corresponda.

        El camino es el mismo para los dos dialectos: se cuenta y se hashea el texto que sale, se
        minimiza con los términos de la causa, se comprueba la autorización y se registra el envío
        (`via` = el del dialecto) antes de reenviar. Lo único distinto es de dónde se saca el texto
        y a qué dirección aguas arriba va.
        """
        config = self._config()
        proveedor, pais = self.server.datos_del_dialecto(dialecto)
        modelo = str(payload.get("model") or config.get("modelo_por_defecto") or "").strip() or None
        causa_id = self._causa_pedida()

        try:
            db = self.server.db()
        except Exception as exc:  # noqa: BLE001 - la base es la que hace posible el registro
            raise ErrorDelProxy(
                503,
                "no pude abrir la base del CRM para registrar el envío "
                f"({type(exc).__name__}). Sin registro no hay aviso, así que NO reenvío: "
                "revisa la base del estudio (LEGALCRM_DB_URL) o corre `openlegal init`.",
            ) from exc

        if causa_id is not None:
            causa = db.uno("SELECT id FROM causas WHERE id = ?", (causa_id,))
            if not causa:
                raise ErrorDelProxy(
                    400,
                    f"la causa {causa_id} no existe en esta base: revisa la cabecera "
                    "X-OpenLegal-Causa y la causa por defecto del proxy (no mando datos que no "
                    "puedo atribuir a un expediente)",
                )

        # 1. El texto que sale (y si se puede minimizar).
        avisos_min: list[str] = []
        minimizado = False
        marcadores = 0
        if config.get("minimizar"):
            try:
                texto = dialecto.texto(payload)
            except ValueError as exc:
                self._bloquear(
                    db, causa_id, proveedor, modelo, pais,
                    f"no pude minimizar este envío: {exc}",
                    texto=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    redactado=False, autorizacion=None, via=dialecto.via,
                )
                self._error_api(
                    422,
                    f"no reenvié nada: {exc}. El estudio pidió minimizar y este pedido no se "
                    "puede minimizar, así que no sale. Mándalo como texto plano o apaga "
                    "'minimizar' en la configuración del proxy —y entonces el registro va a "
                    "decir que salió sin minimizar—. El intento quedó anotado.",
                    dialecto=dialecto,
                )
                return
            terminos = ia.terminos_de_causa(db, causa_id) if causa_id is not None else []
            payload, marcadores = dialecto.minimizar(payload, terminos, avisos_min)
            minimizado = True
            texto = dialecto.texto(payload)
        else:
            try:
                texto = dialecto.texto(payload)
            except ValueError:
                texto = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                avisos_min.append("sin minimizar y sin poder aislar el texto: el hash es del pedido completo")

        # 2. La autorización de la causa.
        autorizacion = autorizacion_vigente(db, causa_id) if causa_id is not None else None
        if causa_id is not None and autorizacion is None:
            if not config.get("permitir_sin_autorizacion"):
                motivo = f"la causa {causa_id} no tiene autorización vigente de IA"
                self._bloquear(
                    db, causa_id, proveedor, modelo, pais, motivo,
                    texto=texto, redactado=minimizado, autorizacion=None, marcadores=marcadores,
                    via=dialecto.via,
                )
                self._error_api(
                    403,
                    f"este envío no tiene autorización de IA para la causa {causa_id}: autorizala con "
                    f"`openlegal ia autorizar --causa {causa_id}` (indicando quién autoriza) o pedile "
                    "al responsable del estudio que la registre. El intento quedó anotado.",
                    tipo="sin_autorizacion",
                    dialecto=dialecto,
                )
                return
            # El estudio lo permitió a propósito, pero el aviso tiene que decirlo: si alguien
            # lee el registro y ve un envío sin autorización, tiene que saber por qué salió.
            avisos_min.append(
                f"la causa {causa_id} no tiene autorización vigente y 'permitir_sin_autorizacion' "
                "está encendido: el envío salió igual y quedó registrado SIN autorización"
            )
        if causa_id is None:
            avisos_min.append(
                "sin causa: no se puede comprobar autorización (manda la cabecera "
                "X-OpenLegal-Causa o configura 'causa_por_defecto')"
            )

        # 3. Reenviar.
        streaming = bool(payload.get("stream"))
        conexion = None
        try:
            conexion, respuesta = self._abrir_aguas_arriba(payload, dialecto)
        except ErrorDelProxy as exc:
            # No llegó a salir de esta máquina (no pude conectar, o la configuración aguas
            # arriba no sirve): se registra como intento bloqueado, con su motivo.
            self._registrar(
                db, causa_id, proveedor, modelo, pais, texto, minimizado,
                autorizacion, bloqueado=True, motivo_bloqueo=str(exc), via=dialecto.via,
            )
            raise
        # El proveedor contestó: eso ya es una comunicación, aunque haya contestado con error
        # (el texto salió igual). Se registra como envío y el error se reenvía tal cual.
        try:
            self._registrar(
                db, causa_id, proveedor, modelo, pais, texto, minimizado, autorizacion,
                marcadores=marcadores, via=dialecto.via,
            )
            for aviso in avisos_min:
                self._escribir(f"  AVISO MINIMIZACIÓN: {aviso}")
            self.send_response(respuesta.status)
            self._reenviar(respuesta, streaming=streaming)
        finally:
            if conexion is not None:
                conexion.close()

    # ------------------------------------------------------- atajos
    def _abrir_aguas_arriba(
        self, payload: dict | None, dialecto: Dialecto, metodo: str = "POST", ruta: str | None = None
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        """Abre la conexión con el proveedor real, en la dirección del dialecto.

        La `api_key` sale de acá y de ningún otro lado (el cliente manda una cualquiera, o
        ninguna). En el dialecto de Anthropic se manda `x-api-key` —que es su cabecera— y además
        `Authorization: Bearer`, que es lo que leen algunas pasarelas: Anthropic y DeepSeek aceptan
        las dos formas (verificado). Si el cliente no trajo `anthropic-version`, se pone la que el
        dialecto exige.

        Se usa `http.client` y no `urllib.request` por una razón concreta: acá hace falta
        (`conexion`, `respuesta`) separados —para leer por trozos con `read1` y para poder
        cerrar— y hace falta distinguir «no pude hablar con el proveedor» (el envío NO salió)
        de «el proveedor contestó 400» (el texto salió igual y hay que registrarlo).
        """
        config = self._config()
        base = dialecto.base_url(config)
        if not base:
            raise ErrorDelProxy(
                500,
                f"falta '{dialecto.clave_base}' en la configuración del proxy "
                f"({ruta_config()}): sin la dirección aguas arriba del dialecto "
                f"{dialecto.nombre} no puedo reenviar nada. Ponela en el archivo (corré "
                "`openlegal ia-proxy --estado` para ver la configuración vigente).",
            )
        url = base + (ruta if ruta is not None else dialecto.ruta_aguas_arriba)
        partes = urllib.parse.urlsplit(url)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            raise ErrorDelProxy(
                500,
                f"'{dialecto.clave_base}' tiene que ser una dirección http o https del proveedor; "
                f"está: {url}",
            )
        cabeceras: dict[str, str] = {"Accept-Encoding": "identity"}
        for nombre, valor in self.headers.items():
            if nombre.lower() in CABECERAS_SALTADAS or nombre.lower() in CABECERAS_DE_CLAVE:
                continue
            cabeceras[nombre] = valor
        propios = {nombre.lower() for nombre in cabeceras}
        # La clave vive en el proxy: la del cliente sólo se usa si acá no hay ninguna
        # configurada (un proveedor local, por ejemplo, que no pide clave). Y en el registro no
        # entra nunca: lo único que viaja es la cabecera.
        clave = str(config.get("api_key") or "").strip()
        heredada = (self.headers.get("Authorization") or self.headers.get("x-api-key") or "").strip()
        clave = clave or heredada
        if dialecto.de_anthropic:
            if clave:
                limpia = clave[7:].strip() if clave[:7].lower() == "bearer " else clave
                cabeceras["x-api-key"] = limpia
                cabeceras["Authorization"] = f"Bearer {limpia}"
            if "anthropic-version" not in propios:
                cabeceras["anthropic-version"] = ANTHROPIC_VERSION
        elif clave:
            cabeceras["Authorization"] = f"Bearer {clave}"
        if "content-type" not in propios:
            cabeceras["Content-Type"] = "application/json"
        espera = int(config.get("espera_segundos") or 600)
        if partes.scheme == "https":
            conexion: http.client.HTTPConnection = http.client.HTTPSConnection(
                partes.hostname, partes.port or 443, timeout=espera
            )
        else:
            conexion = http.client.HTTPConnection(partes.hostname, partes.port or 80, timeout=espera)
        destino = partes.path + (f"?{partes.query}" if partes.query else "")
        datos = None if payload is None else json.dumps(payload).encode("utf-8")
        try:
            conexion.request(metodo, destino, body=datos, headers=cabeceras)
            return conexion, conexion.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conexion.close()
            raise ErrorDelProxy(
                502,
                f"no pude hablar con el proveedor aguas arriba ({config_segura(config, dialecto)}): {exc}",
            ) from exc

    def _registrar(
        self,
        db: DB,
        causa_id: int | None,
        proveedor: str,
        modelo: str | None,
        pais: str,
        texto: str,
        minimizado: bool,
        autorizacion: dict | None,
        bloqueado: bool = False,
        motivo_bloqueo: str | None = None,
        marcadores: int = 0,
        via: str = VIA,
    ) -> dict | None:
        """Deja el envío (o el intento) en el registro, y avisa."""
        try:
            registro = registrar(
                db,
                proveedor=proveedor,
                modelo=modelo,
                destino_pais=pais,
                texto=texto,
                redactado=minimizado,
                causa_id=causa_id,
                estudio_id=self.server.estudio_de(db, causa_id),
                autorizacion_id=int(autorizacion["id"]) if autorizacion else None,
                bloqueado=bloqueado,
                motivo_bloqueo=motivo_bloqueo,
                via=via,
            )
        except Exception as exc:  # noqa: BLE001 - se dice y se sigue: negarse acá no aporta nada
            self._escribir(
                f"AVISO: no pude registrar el envío en el CRM ({type(exc).__name__}). "
                "El envío sigue, pero el registro quedó incompleto: avísale al responsable."
            )
            return None
        resumen = resumen_de_envio(
            proveedor=registro["proveedor"], modelo=registro["modelo"], pais=registro["destino_pais"],
            causa_id=registro["causa_id"], caracteres=registro["caracteres"],
            hash_payload=registro["hash_payload"], minimizado=registro["redactado"],
            marcadores=marcadores,
        )
        self.server.avisador.envio(resumen, bloqueado=bloqueado)
        return registro

    def _bloquear(
        self,
        db: DB,
        causa_id: int | None,
        proveedor: str,
        modelo: str | None,
        pais: str,
        motivo: str,
        texto: str,
        redactado: bool,
        autorizacion: dict | None,
        marcadores: int = 0,
        via: str = VIA,
    ) -> None:
        """Registra el envío que NO se reenvió: el intento también hay que poder demostrarlo."""
        self._registrar(
            db, causa_id, proveedor, modelo, pais, texto, redactado, autorizacion,
            bloqueado=True, motivo_bloqueo=motivo, marcadores=marcadores, via=via,
        )


def config_segura(config: dict, dialecto: Dialecto = OPENAI) -> str:
    """La dirección aguas arriba en texto, para los mensajes de error. Nunca la clave."""
    base = str(config.get(dialecto.clave_base) or "").strip() or f"(sin {dialecto.clave_base})"
    return f"aguas arriba: {base}"


# ===================================================================== arranque
def crear_servidor(
    config: dict | None = None, base_de_datos: str | None = None, puerto: int | None = None
) -> ServidorIA:
    """Arma el servidor (sin arrancarlo), escuchando sólo en 127.0.0.1.

    Se separa de `servir` para poder levantarlo en una prueba y apagarlo sin matar procesos.
    Si el puerto está tomado lo dice como frase y no como volcado: el caso típico es que ya
    haya un proxy corriendo (o el CRM, que usa el 8899).
    """
    config = config or cargar_config()
    puerto = int(puerto if puerto is not None else config.get("puerto") or PUERTO_POR_DEFECTO)
    try:
        return ServidorIA((DIRECCION, puerto), config, base_de_datos=base_de_datos)
    except OSError as exc:
        raise ValueError(
            f"no pude escuchar en {DIRECCION}:{puerto} ({exc}). Suele ser que ya haya un proxy "
            "corriendo en ese puerto: míralo con `openlegal ia-proxy --estado`, o cambia 'puerto' "
            "en el archivo de configuración del proxy."
        ) from exc


def banner(servidor: ServidorIA) -> list[str]:
    """Las líneas que se imprimen al arrancar: las direcciones, el proveedor y qué hace con los datos."""
    config = servidor.config
    puerto = servidor.server_address[1]
    return [
        "",
        f"proxy de IA escuchando en http://{DIRECCION}:{puerto} (sólo esta máquina)",
        f"  dialecto OpenAI:    base_url=http://{DIRECCION}:{puerto}/v1 → {config.get('base_url')}",
        f"  dialecto Anthropic: baseURL=http://{DIRECCION}:{puerto}   → {config.get('base_url_anthropic')}",
        f"  proveedor: {servidor.proveedor} · país de destino: {servidor.destino_pais} · "
        f"registro: transferencias_ia ({AVISO_SIN_CONTENIDO})",
        f"  minimizar: {'sí' if config.get('minimizar') else 'NO'} · "
        f"permitir sin autorización: {'sí' if config.get('permitir_sin_autorizacion') else 'no'} · "
        f"avisos de escritorio: {'sí' if config.get('avisar_escritorio') else 'no'}",
        "",
        "apunta el harness acá (los dos dialectos van al MISMO registro):",
        "  · protocolo `messages` (el de Anthropic, el que usa dsh por defecto):",
        f"    baseURL = http://{DIRECCION}:{puerto}   ← sin /v1 y sin /anthropic, porque el",
        "    adaptador le agrega `/v1/messages` él mismo.",
        f"  · protocolo de OpenAI (`/v1/chat/completions`): base_url = http://{DIRECCION}:{puerto}/v1",
        "con cualquier api_key (la real vive en el archivo del proxy, permisos 600) y, si el",
        "envío es de una causa, la cabecera X-OpenLegal-Causa: <numero de causa>.",
        "",
    ]


def servir(config: dict | None = None, base_de_datos: str | None = None) -> None:
    """Arranca el proxy y se queda escuchando hasta que lo corten (Ctrl-C).

    Se imprime la dirección y qué proveedor está detrás, para que se pueda copiar el base_url.
    Los errores del dominio (una configuración ilegible, por ejemplo) salen como frase.
    """
    config = config or cargar_config()
    servidor = crear_servidor(config, base_de_datos=base_de_datos)
    for linea in banner(servidor):
        print(linea, flush=True)
    # La base se abre (y se migra) ahora, no en el primer envío: si el registro no está, hay
    # que saberlo antes de apuntarle el harness, no cuando el abogado ya está trabajando.
    try:
        servidor.db()
        print("registro del CRM: listo", flush=True)
    except Exception as exc:  # noqa: BLE001 - se dice y se arranca igual: el proxy lo vuelve a decir
        print(
            f"AVISO: no pude abrir la base del CRM ({type(exc).__name__}): los envíos con causa "
            "van a ser rechazados hasta que la base esté disponible. Corre `openlegal init`.",
            flush=True,
        )
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nproxy de IA detenido", flush=True)
    finally:
        servidor.cerrar()
        servidor.server_close()
