#!/usr/bin/env python
"""Spike: ¿aporta Jev (TypeSafe System One) al intake legal?

No reemplaza nada: mide. Le hace a Jev las preguntas que hoy resolvemos con el
modelo grande (o con regexes frágiles) sobre el MISMO expediente, y compara con lo
que ya sabemos que es verdad. Sirve para decidir si entra al pipeline como
pre-filtro barato o si se queda fuera.

La clave NO se escribe aquí y nunca se imprime: se lee de $TYPESAFE_API_KEY o de
~/.config/openlegal/typesafe.key (permisos 600).

Uso:
    python scripts/spike_jev.py                 # expediente de la carpeta de prueba
    python scripts/spike_jev.py --lineas        # además, detección de nombres por línea
    python scripts/spike_jev.py --dry-run       # sin red: muestra las preguntas y sale
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODELO = "jev-latest"
RUTA_CLAVE = pathlib.Path.home() / ".config" / "openlegal" / "typesafe.key"
EXPEDIENTE = pathlib.Path("/home/pablo/Escritorio/Causas-prueba/C-1234-2026/_texto_verdadero.txt")

# Lo que sabemos de este expediente (es sintético y ya lo verificamos):
#   - es una DEMANDA LABORAL por despido injustificado (Juzgado del Trabajo)
#   - el proveído confiere traslado a la DEMANDADA por DIEZ DÍAS HÁBILES
#   - contiene datos personales: RUT y nombres de tres personas naturales
VERDAD = {
    "tipo_documento": "demanda laboral",
    "abre_plazo": "si",
    "destinatario": "demandada",
    "nombres_que_debe_pescar": [
        "MARÍA FERNANDA PÉREZ SOTO",
        "JORGE ANDRÉS PÉREZ GALLARDO",
        "Carolina Silva Rojas",
    ],
}


def leer_clave() -> str | None:
    """La clave, sin dejarla nunca en la salida."""
    del_entorno = os.environ.get("TYPESAFE_API_KEY")
    if del_entorno:
        return del_entorno.strip()
    if RUTA_CLAVE.exists():
        modo = oct(RUTA_CLAVE.stat().st_mode)[-3:]
        if modo not in ("600", "400"):
            print(f"  AVISO: {RUTA_CLAVE} tiene permisos {modo}; se recomiendan 600")
        return RUTA_CLAVE.read_text(encoding="utf-8").strip()
    return None


def preguntas(con_lineas: bool, lineas: list[str]) -> dict:
    """Las cinco preguntas del intake, más el fan-out de nombres si se pide."""
    q = {
        "tipo_documento": {
            "type": "choice",
            "instructions": "¿Qué tipo de documento judicial chileno es este texto?",
            "criteria": {
                "demanda laboral": "demanda por despido, prestaciones, en juzgado del trabajo",
                "demanda civil": "cobro de pesos, cumplimiento, en juzgado civil",
                "proveido": "resolución que da trámite",
                "sentencia": "fallo definitivo",
                "cedula": "acta de notificación por cédula",
                "escrito": "presentación de parte",
                "otro": None,
            },
        },
        "abre_plazo": {
            "type": "noul",
            "instructions": (
                "¿Este texto concede un plazo a alguna parte para actuar "
                "(traslado, contestación, recurso)?"
            ),
        },
        "destinatario": {
            "type": "choice",
            "instructions": "¿A qué parte le corre el plazo que concede el texto?",
            "criteria": {"demandada": None, "demandante": None, "ninguna": None},
        },
        "dias_del_plazo": {
            "type": "choice",
            "instructions": "¿Cuántos días de plazo concede el texto, según el texto mismo?",
            "criteria": {"ocho": None, "diez": None, "cinco": None, "otro": None, "no concede": None},
        },
        "urgencia": {
            "type": "score",
            "instructions": "¿Qué tan urgente es que un abogado revise este documento?",
            "criteria": [
                "informativo, sin plazo corriendo",
                "revisar esta semana",
                "plazo fatal corriendo, esta semana",
                "plazo fatal que vence en días",
                "crisis: se pierde el plazo hoy o mañana",
            ],
        },
        "datos_personales": {
            "type": "noul",
            "instructions": (
                "¿El texto contiene datos personales identificatorios "
                "(nombres de personas naturales, RUT, correo o teléfono)?"
            ),
        },
    }
    if con_lineas:
        for i, linea in enumerate(lineas):
            q[f"linea_{i}"] = {
                "type": "noul",
                "instructions": (
                    "¿Esta línea contiene el nombre propio de una persona natural "
                    "(no de una empresa)?"
                ),
                "state": linea,
            }
    return q


def llamar(clave: str, state, questions: dict, timeout: int = 60) -> dict:
    cuerpo = json.dumps({"state": state, "model": MODELO, "questions": questions}).encode("utf-8")
    peticion = urllib.request.Request(
        ENDPOINT,
        data=cuerpo,
        headers={
            "Authorization": f"Bearer {clave}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def resumir(nombre: str, dato: dict) -> str:
    for campo in ("choice", "score", "noul"):
        if isinstance(dato, dict) and campo in dato:
            conf = dato.get("confidence")
            conf = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
            return f"{campo}={dato[campo]!r} confianza={conf}"
    return json.dumps(dato, ensure_ascii=False)[:80]


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike de Jev para el intake legal")
    parser.add_argument("--lineas", action="store_true", help="añade la detección de nombres por línea")
    parser.add_argument("--dry-run", action="store_true", help="no llama a la API: muestra el payload")
    args = parser.parse_args()

    if not EXPEDIENTE.exists():
        print(f"  falta el expediente de prueba: {EXPEDIENTE}")
        return 1
    texto = EXPEDIENTE.read_text(encoding="utf-8")
    lineas = [l.strip() for l in texto.splitlines() if len(l.strip()) > 25] if args.lineas else []

    clave = leer_clave()
    if args.dry_run:
        q = preguntas(args.lineas, lineas[:2])
        print(f"  endpoint : {ENDPOINT}")
        print(f"  modelo   : {MODELO}")
        print(f"  state    : {len(texto)} caracteres del expediente")
        print(f"  preguntas: {len(q)} → {sorted(q)}")
        print("  (dry-run: no se llamó a la API)")
        return 0
    if not clave:
        print("  falta la clave. Dos formas de dejarla, y ninguna pasa por el chat:")
        print("    1) export TYPESAFE_API_KEY=...   (en el shell desde el que corres esto)")
        print(f"    2) printf '%s' 'LA-CLAVE' > {RUTA_CLAVE} && chmod 600 {RUTA_CLAVE}")
        return 2

    print(f"  expediente: {EXPEDIENTE.name} ({len(texto)} caracteres)")
    q = preguntas(args.lineas, lineas)
    print(f"  preguntando {len(q)} cosas en UNA sola llamada (fan-out)…\n")
    inicio = time.time()
    try:
        respuesta = llamar(clave, texto, q)
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "replace")[:300]
        print(f"  la API respondió HTTP {error.code}: {detalle}")
        return 3
    except Exception as error:  # noqa: BLE001
        print(f"  falló la llamada: {type(error).__name__}: {error}")
        return 3
    demora = time.time() - inicio

    respuestas = respuesta.get("answers") or respuesta.get("results") or respuesta
    print(f"  respuesta en {demora*1000:.0f} ms | uso: {respuesta.get('usage') or respuesta.get('tokens') or 'n/d'}\n")

    print("  == Las preguntas del intake ==")
    aciertos = 0
    for nombre in ("tipo_documento", "abre_plazo", "destinatario", "dias_del_plazo", "urgencia", "datos_personales"):
        dato = respuestas.get(nombre) if isinstance(respuestas, dict) else None
        if dato is None:
            continue
        valor = str(dato.get("choice") if isinstance(dato, dict) else dato).lower()
        esperado = VERDAD.get({"tipo_documento": "tipo_documento", "abre_plazo": "abre_plazo",
                               "destinatario": "destinatario"}.get(nombre, ""), "")
        marca = ""
        if esperado:
            acierto = esperado in valor or (esperado == "si" and valor in ("si", "true", "1"))
            aciertos += 1 if acierto else 0
            marca = "  ✓" if acierto else f"  ✗ (esperábamos {esperado})"
        print(f"    {nombre:18} {resumir(nombre, dato) if isinstance(dato, dict) else dato}{marca}")

    if args.lineas:
        print("\n  == Detección de nombres por línea (donde nuestro regex falla) ==")
        encontrados = []
        for i, linea in enumerate(lineas):
            dato = respuestas.get(f"linea_{i}")
            if not isinstance(dato, dict):
                continue
            probabilidad = dato.get("noul")
            if isinstance(probabilidad, (int, float)) and probabilidad >= 0.5:
                encontrados.append(linea)
                print(f"    [{probabilidad:.2f}] {linea[:80]}")
        for nombre in VERDAD["nombres_que_debe_pescar"]:
            hallado = any(nombre.lower() in l.lower() for l in encontrados)
            print(f"    {'✓' if hallado else '✗'} {nombre}")
        print(f"\n    líneas marcadas: {len(encontrados)}")

    print(f"\n  == Veredicto de una corrida ==")
    print(f"    latencia: {demora*1000:.0f} ms | preguntas: {len(q)} | costo: ver usage arriba")
    print("    Compáralo con el agente grande: varios segundos y muchos más tokens por turno,")
    print("    pero con razonamiento y herramientas. Decide con estos números, no con el folleto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
