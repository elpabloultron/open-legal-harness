"""Prueba del panel de verdad: se levanta el servidor real y se lo maneja por HTTP.

    python3 scripts/prueba_panel.py

No usa el cliente de pruebas de Starlette: arranca `openlegal serve` en un puerto libre,
entra con correo y contraseña por HTTP con una cookie de sesión, recorre los módulos y
comprueba lo que importa: que la interfaz sirva, que los módulos respondan, que un módulo
de administración le niegue el paso a quien no administra, y que la clave del correo no
aparezca en ninguna respuesta.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parents[1]
fallos: list[str] = []


def check(condicion: bool, mensaje: str) -> None:
    if condicion:
        print(f"  ✓ {mensaje}")
    else:
        print(f"  ✗ {mensaje}")
        fallos.append(mensaje)


def el_comando() -> list[str]:
    if (RAIZ / ".venv" / "bin" / "openlegal").is_file():
        return [str(RAIZ / ".venv" / "bin" / "openlegal")]
    return [sys.executable, "-m", "openlegal"]


def puerto_libre() -> int:
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        return int(sonda.getsockname()[1])


trabajo = pathlib.Path(tempfile.mkdtemp(prefix="prueba-panel-"))
base = trabajo / "estudio.db"
os.environ["OPENLEGAL_CONFIG"] = str(trabajo / "notificaciones.json")
os.environ["OPENLEGAL_AVISOS_LOG"] = str(trabajo / "avisos.log")
CRM = el_comando()

print(f"=== preparo el estudio (con {' '.join(CRM)}) ===")
for argumentos in (
    ["estudio", "crear", "--nombre", "Estudio Panel", "--modo", "oficina"],
    ["usuario", "crear", "--nombre", "Sofia Soto", "--email", "socia@panel.cl", "--rol", "socio",
     "--password", "clave-larga-1"],
    ["usuario", "crear", "--nombre", "Ana Perez", "--email", "ana@panel.cl", "--rol", "abogado",
     "--password", "clave-larga-2"],
    ["usuario", "crear", "--nombre", "Carla Diaz", "--email", "carla@panel.cl", "--rol", "paralegal",
     "--password", "clave-larga-3"],
):
    resultado = subprocess.run([*CRM, "--db", f"sqlite:///{base}", *argumentos],
                               capture_output=True, text=True)
    if resultado.returncode != 0:
        print(f"  ✗ falló: {' '.join(argumentos)}\n    {resultado.stderr[-200:]}")
        fallos.append("preparación del estudio")
check(not fallos, "el estudio de prueba se crea")

puerto = puerto_libre()
servidor = subprocess.Popen(
    [*CRM, "--db", f"sqlite:///{base}", "serve", "--host", "127.0.0.1", "--port", str(puerto)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
raiz_url = f"http://127.0.0.1:{puerto}"
for _ in range(40):
    time.sleep(0.25)
    try:
        with urllib.request.urlopen(f"{raiz_url}/panel.css", timeout=2):
            break
    except Exception:
        continue
print(f"\n=== el panel está escuchando en {raiz_url} ===")


def pedir(ruta: str, opciones: dict | None = None, cookies=None) -> tuple[int, str, dict]:
    """(código, cuerpo, cabeceras) de una llamada HTTP contra el panel."""
    if cookies is None:
        cookies = http.cookiejar.CookieJar()
    manos = urllib.request.HTTPCookieProcessor(cookies)
    peticion = urllib.request.Request(f"{raiz_url}{ruta}", **(opciones or {}))
    try:
        with urllib.request.build_opener(manos).open(peticion, timeout=10) as respuesta:
            return respuesta.status, respuesta.read().decode("utf-8", "replace"), dict(respuesta.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace"), dict(error.headers)


def entrar(email: str, clave: str):
    galletas = http.cookiejar.CookieJar()
    codigo, cuerpo, _ = pedir("/api/login", {
        "method": "POST",
        "data": json.dumps({"email": email, "password": clave}).encode("utf-8"),
        "headers": {"Content-Type": "application/json"},
    }, galletas)
    check(codigo == 200, f"{email} entra ({codigo})")
    return galletas


# ------------------------------------------------------------------- la página
print("\n=== la interfaz se sirve ===")
codigo, html, cabeceras = pedir("/")
check(codigo == 200 and "data-vista=\"avisos\"" in html, "el HTML trae los módulos")
codigo, js, _ = pedir("/panel.js")
check(codigo == 200 and "vistas" in js, "el JavaScript se sirve")
codigo, css, _ = pedir("/panel.css")
check(codigo == 200 and ".rejilla" in css, "el CSS se sirve")

codigo, _, _ = pedir("/api/avisos")
check(codigo == 401, "sin sesión, los datos no se entregan (401)")

# --------------------------------------------------------------------- la socia
print("\n=== la socia (administra el estudio) ===")
socia = entrar("socia@panel.cl", "clave-larga-1")
for ruta in ("/api/estado", "/api/avisos", "/api/usuarios", "/api/seguridad", "/api/retencion", "/api/clientes"):
    codigo, _, _ = pedir(ruta, None, socia)
    check(codigo == 200, f"GET {ruta} → 200")

codigo, cuerpo, _ = pedir("/api/avisos/config", {
    "method": "POST",
    "data": json.dumps({"email": {
        "host": "smtp.panel.cl", "puerto": 587, "usuario": "avisos@panel.cl",
        "clave": "clave-secretisima-de-prueba", "de": "avisos@panel.cl",
    }}).encode("utf-8"),
    "headers": {"Content-Type": "application/json"},
}, socia)
check(codigo == 200, f"guarda la configuración de correo ({codigo})")
codigo, cuerpo, _ = pedir("/api/avisos", None, socia)
check("clave-secretisima-de-prueba" not in cuerpo, "la clave guardada NO sale en la API")
check("smtp.panel.cl" in cuerpo, "pero sí el servidor configurado (para no reescribirlo)")
config_en_disco = pathlib.Path(os.environ["OPENLEGAL_CONFIG"])
check(config_en_disco.is_file() and oct(config_en_disco.stat().st_mode)[-3:] == "600",
      "el archivo de configuración queda con permisos 600")

codigo, cuerpo, _ = pedir("/api/avisos/probar", {
    "method": "POST", "data": json.dumps({"canal": "consola"}).encode("utf-8"),
    "headers": {"Content-Type": "application/json"},
}, socia)
check(codigo == 200 and json.loads(cuerpo).get("enviado") is True, "el aviso de prueba sale")

codigo, cuerpo, _ = pedir("/api/usuarios", {
    "method": "POST",
    "data": json.dumps({"nombre": "Pedro Rojas", "email": "pedro@panel.cl", "rol": "abogado",
                        "password": "clave-larga-4", "telefono": "+56912345678"}).encode("utf-8"),
    "headers": {"Content-Type": "application/json"},
}, socia)
check(codigo == 200, f"crea una persona desde la interfaz ({codigo})")

codigo, cuerpo, _ = pedir("/api/seguridad/2fa/preparar", {
    "method": "POST", "data": json.dumps({"email": "ana@panel.cl"}).encode("utf-8"),
    "headers": {"Content-Type": "application/json"},
}, socia)
alta = json.loads(cuerpo)
check(codigo == 200 and alta.get("secreto") and alta.get("uri", "").startswith("otpauth://"),
      "el alta del segundo factor devuelve secreto y enlace")

codigo, cuerpo, _ = pedir("/api/seguridad/2fa/confirmar", {
    "method": "POST", "data": json.dumps({"email": "ana@panel.cl", "codigo": "000000"}).encode("utf-8"),
    "headers": {"Content-Type": "application/json"},
}, socia)
check(codigo == 401, f"un código equivocado no activa nada ({codigo})")

# ------------------------------------------------------------------ la paralegal
print("\n=== la paralegal (no administra) ===")
paralegal = entrar("carla@panel.cl", "clave-larga-3")
for ruta in ("/api/usuarios", "/api/seguridad", "/api/retencion"):
    codigo, _, _ = pedir(ruta, None, paralegal)
    check(codigo == 403, f"GET {ruta} le da 403")
codigo, _, _ = pedir("/api/avisos", None, paralegal)
check(codigo == 200, "pero sí puede ver y usar los avisos (200)")

codigo, cuerpo, _ = pedir("/api/sesion", None, paralegal)
check("usuario.gestionar" not in json.loads(cuerpo)["permisos"],
      "la sesión le dice a la interfaz qué módulos le tocan")

servidor.terminate()
try:
    servidor.wait(timeout=10)
except subprocess.TimeoutExpired:  # pragma: no cover
    servidor.kill()
shutil.rmtree(trabajo, ignore_errors=True)

if fallos:
    print(f"\n✗ {len(fallos)} comprobación(es) fallaron")
    sys.exit(1)
print("\n✓ el panel funciona de punta a punta")
