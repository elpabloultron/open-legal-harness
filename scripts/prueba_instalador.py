"""Prueba del instalador de oficina, para CI y para correr a mano.

    python3 scripts/prueba_instalador.py

Corre el instalador con un HOME desechable y `--solo-generar`, así que no toca los servicios
de la máquina donde se ejecuta. Comprueba que escriba los cuatro servicios con el comando y
la base correctos, que **no** active nada cuando se le pide sólo generar, y que correrlo dos
veces deje lo mismo (idempotente): un instalador que duplica al segundo intento es un
problema esperando a ocurrir.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[1]
INSTALADOR = RAIZ / "scripts" / "instalar_oficina.sh"

fallos: list[str] = []


def check(condicion: bool, mensaje: str) -> None:
    if condicion:
        print(f"  ✓ {mensaje}")
    else:
        print(f"  ✗ {mensaje}")
        fallos.append(mensaje)


def correr(casa: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    entorno = {**os.environ, "HOME": str(casa)}
    # El CLI puede venir del entorno virtual del repo o del PATH del sistema.
    entorno["OPENLEGAL"] = str(RAIZ / ".venv" / "bin" / "openlegal")
    return subprocess.run(
        ["bash", str(INSTALADOR), "--solo-generar", "--puerto", "9911", *extra],
        capture_output=True, text=True, env=entorno, cwd=str(RAIZ),
    )


casa = pathlib.Path(tempfile.mkdtemp(prefix="prueba-oficina-"))
(casa / ".openlegal").mkdir(parents=True)
base = f"sqlite://{casa}/.openlegal/estudio.db"
panel_texto = ""

print("=== 1. el instalador escribe los servicios y no activa nada ===")
resultado = correr(casa, "--db", base)
print("  " + resultado.stdout.strip().split("=== los")[0].strip().replace("\n", "\n  "))
check(resultado.returncode == 0, "termina bien")
check("no se activa nada" in resultado.stdout, "avisa que no activó nada")

unidades = casa / ".config" / "systemd" / "user"
archivos = sorted(p.name for p in unidades.glob("*")) if unidades.is_dir() else []
esperados = ["openlegal-avisos.service", "openlegal-avisos.timer",
             "openlegal-panel.service", "openlegal-respaldo.service", "openlegal-respaldo.timer"]
check(archivos == esperados, f"escribe los cinco servicios ({', '.join(archivos) or 'ninguno'})")

if archivos == esperados:
    panel = (unidades / "openlegal-panel.service").read_text(encoding="utf-8")
    panel_texto = panel
    check(base in panel, "el panel apunta a la base pedida")
    check("serve --host 0.0.0.0 --port 9911" in panel, "el panel escucha en el puerto pedido")
    check("Restart=on-failure" in panel, "el panel se levanta solo si se cae")

    avisos = (unidades / "openlegal-avisos.service").read_text(encoding="utf-8")
    check("notificar --generar --enviar" in avisos, "los avisos encolan y despachan")
    timer = (unidades / "openlegal-avisos.timer").read_text(encoding="utf-8")
    check("OnUnitActiveSec=10min" in timer, "los avisos corren cada diez minutos")

    respaldo = (unidades / "openlegal-respaldo.service").read_text(encoding="utf-8")
    check("respaldo.sh" in respaldo, "el respaldo usa el guion del repositorio")

print("\n=== 2. con --sin-avisos no instala la parte de avisos ===")
otra = pathlib.Path(tempfile.mkdtemp(prefix="prueba-oficina-"))
(otra / ".openlegal").mkdir(parents=True)
resultado = correr(otra, "--db", f"sqlite://{otra}/.openlegal/estudio.db", "--sin-avisos")
archivos = sorted(p.name for p in (otra / ".config" / "systemd" / "user").glob("*"))
check("openlegal-avisos.timer" not in archivos, "no escribe el temporizador de avisos")
check("openlegal-panel.service" in archivos, "pero sí el panel")

print("\n=== 3. correrlo dos veces deja lo mismo ===")
correr(casa, "--db", base)
archivos_despues = sorted(p.name for p in unidades.glob("*"))
check(archivos_despues == esperados, f"sigue habiendo cinco servicios, no diez ({len(archivos_despues)})")
check((unidades / "openlegal-panel.service").read_text(encoding="utf-8") == panel_texto,
      "el contenido es el mismo")

shutil.rmtree(casa, ignore_errors=True)
shutil.rmtree(otra, ignore_errors=True)

if fallos:
    print(f"\n✗ {len(fallos)} comprobación(es) fallaron")
    sys.exit(1)
print("\n✓ el instalador de oficina funciona")
