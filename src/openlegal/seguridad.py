"""Segundo factor y señales de acceso, con la librería estándar.

Dos cosas de la «c» de cumplimiento:

- **TOTP (RFC 6238)**: el segundo factor de administradores y socios. Son treinta líneas
  con `hmac` y `base64`, y conviene poder leerlas enteras en vez de confiar en una
  dependencia para algo que se verifica contra los vectores del propio RFC (ver
  tests/test_segundo_factor.py).
- **Intentos fallidos**: quién está probando contraseñas contra el CRM. Es lo que alimenta
  la alerta de accesos anómalos: un ataque de fuerza bruta tiene que dejar rastro, y el
  estudio tiene que poder verlo.

Por qué importa acá más que en otros sistemas: el CRM guarda expedientes de terceros, y
el rol administrador ve todo el estudio. Una contraseña sola, reusada o filtrada, no es
suficiente para eso.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

from .db import DB

#: Cuántos dígitos tiene el código que se le pide a la persona.
DIGITOS = 6

#: Cada cuántos segundos cambia el código (lo fija el RFC).
PERIODO = 30

#: Cuántos períodos hacia atrás y adelante se aceptan. Uno solo: tolera el desfase de
#: reloj entre el teléfono y el servidor sin abrir de más la ventana de un código visto.
VENTANA = 1

#: Cuántos intentos fallidos en la ventana hacen sospechar un ataque.
UMBRAL_INTENTOS = 10


# ------------------------------------------------------------------------ TOTP
def nuevo_secreto(largo: int = 20) -> str:
    """Un secreto nuevo, en base32 (que es lo que entienden las apps de autenticación)."""
    return base64.b32encode(secrets.token_bytes(largo)).decode("ascii")


def _clave(secreto: str) -> bytes:
    """El secreto en bytes, tolerando que venga sin el relleno de base32."""
    limpio = secreto.strip().replace(" ", "").upper()
    return base64.b32decode(limpio + "=" * (-len(limpio) % 8))


def codigo(secreto: str, momento: float | None = None, digitos: int = DIGITOS) -> str:
    """El código TOTP para ese instante (RFC 6238, HMAC-SHA1)."""
    segundos = time.time() if momento is None else momento
    contador = struct.pack(">Q", int(segundos) // PERIODO)
    resumen = hmac.new(_clave(secreto), contador, hashlib.sha1).digest()
    desplazamiento = resumen[-1] & 0x0F
    trozo = struct.unpack(">I", resumen[desplazamiento : desplazamiento + 4])[0] & 0x7FFFFFFF
    return str(trozo % (10**digitos)).zfill(digitos)


def codigo_valido(
    secreto: str | None,
    presentado: str | None,
    momento: float | None = None,
    ventana: int = VENTANA,
) -> bool:
    """¿Sirve este código? Compara en tiempo constante y con la ventana de tolerancia.

    Si no hay secreto o no hay código, la respuesta es NO: el segundo factor falla
    cerrado. Un CRM que se abriera cuando falta el código no tendría segundo factor.
    """
    if not secreto or not presentado:
        return False
    limpio = str(presentado).strip().replace(" ", "")
    if not limpio.isdigit():
        return False
    segundos = time.time() if momento is None else momento
    # Los dígitos que trae el código mandan: así la misma función sirve para los vectores
    # de 8 dígitos del RFC y para el código de 6 que se le pide a una persona.
    for salto in range(-ventana, ventana + 1):
        esperado = codigo(secreto, segundos + salto * PERIODO, len(limpio))
        if hmac.compare_digest(esperado, limpio):
            return True
    return False


def uri_otpauth(secreto: str, email: str, emisor: str = "Open Legal Harness") -> str:
    """La URI que se escanea con la app de autenticación (o se escribe a mano)."""
    parametros = urllib.parse.urlencode(
        {"secret": secreto, "issuer": emisor, "algorithm": "SHA1", "digits": DIGITOS, "period": PERIODO}
    )
    return f"otpauth://totp/{urllib.parse.quote(emisor)}:{urllib.parse.quote(email)}?{parametros}"


# ----------------------------------------------------------- accesos anómalos
def _sello(momento: dt.datetime) -> str:
    """El mismo formato con que SQLite guarda `CURRENT_TIMESTAMP` (UTC, con espacio)."""
    return momento.strftime("%Y-%m-%d %H:%M:%S")


def intentos_fallidos(
    db: DB, minutos: int = 15, email: str | None = None, estudio_id: int | None = None
) -> dict:
    """Cuántos intentos de login fallaron en la ventana, y cuántos por cuenta.

    Se lee de la bitácora (`login.fallido` y `login.2fa.fallido`): no hay un contador
    aparte que se pueda desincronizar de lo que realmente pasó.
    """
    desde = _sello(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutos))
    condiciones = ["accion IN ('login.fallido', 'login.2fa.fallido')", "creado_en >= ?"]
    parametros: list[object] = [desde]
    if estudio_id is not None:
        condiciones.append("(estudio_id = ? OR estudio_id IS NULL)")
        parametros.append(estudio_id)
    if email:
        condiciones.append("detalle LIKE ?")
        parametros.append(f"%{email.lower()}%")
    filas = db.todos(
        f"SELECT accion, detalle, creado_en FROM auditoria WHERE {' AND '.join(condiciones)} "
        "ORDER BY id DESC",
        tuple(parametros),
    )
    por_cuenta: dict[str, int] = {}
    por_motivo: dict[str, int] = {}
    for fila in filas:
        cuenta = (fila.get("detalle") or "").split("·")[0].strip().lower() or "(sin dato)"
        por_cuenta[cuenta] = por_cuenta.get(cuenta, 0) + 1
        por_motivo[fila["accion"]] = por_motivo.get(fila["accion"], 0) + 1
    return {
        "ventana_minutos": minutos,
        "total": len(filas),
        "por_cuenta": por_cuenta,
        "por_motivo": por_motivo,
        "umbral": UMBRAL_INTENTOS,
        "sospechoso": len(filas) >= UMBRAL_INTENTOS,
        "ultimo": filas[0]["creado_en"] if filas else None,
    }
