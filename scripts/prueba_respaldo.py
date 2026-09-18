"""Prueba del respaldo de la oficina, para CI y para correr a mano.

    python3 scripts/prueba_respaldo.py

Comprueba lo que importa de un respaldo: que la copia **abra y traiga los datos**, que la
rotación conserve las últimas copias y no borre de más, y que una base que no existe se
informe como error en vez de dejar un archivo vacío que parece un respaldo.
"""
from __future__ import annotations

import glob
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

RAIZ = pathlib.Path(__file__).resolve().parents[1]
RESPALDO = RAIZ / "scripts" / "respaldo.sh"

fallos: list[str] = []


def check(condicion: bool, mensaje: str) -> None:
    if condicion:
        print(f"  ✓ {mensaje}")
    else:
        print(f"  ✗ {mensaje}")
        fallos.append(mensaje)


def el_comando() -> list[str]:
    """Con qué se invoca el CRM: el comando instalado, el del entorno del repo, o python -m."""
    en_el_path = shutil.which("openlegal")
    if en_el_path:
        return [en_el_path]
    del_repo = RAIZ / ".venv" / "bin" / "openlegal"
    if del_repo.is_file():
        return [str(del_repo)]
    return [sys.executable, "-m", "openlegal"]


def comando(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(argumentos[0]), *argumentos[1:]], capture_output=True, text=True)


trabajo = pathlib.Path(tempfile.mkdtemp(prefix="prueba-respaldo-"))
base = trabajo / "estudio.db"
destino = trabajo / "respaldos"
CRM = el_comando()

print(f"=== preparo un estudio de verdad (con {' '.join(CRM)}) ===")
entorno = dict(os.environ)
if CRM[-1] == "openlegal" and CRM[1:2] == ["-m"]:
    entorno["PYTHONPATH"] = str(RAIZ / "src")
crear_estudio = subprocess.run(
    [*CRM, "--db", f"sqlite:///{base}", "estudio", "crear", "--nombre", "Estudio Respaldo",
     "--modo", "oficina"],
    capture_output=True, text=True, env=entorno,
)
check(crear_estudio.returncode == 0, "el estudio de prueba se crea")

print("\n=== 1. el respaldo de una base real ===")
resultado = comando("bash", str(RESPALDO), "--db", f"sqlite:///{base}", "--destino", str(destino))
print("  " + resultado.stdout.strip().replace("\n", "\n  "))
check(resultado.returncode == 0, "el respaldo termina bien")

copias = sorted(glob.glob(str(destino / "estudio-*.db")))
check(len(copias) == 1, "queda una copia")
if copias:
    con = sqlite3.connect(copias[-1])
    con.row_factory = sqlite3.Row
    check(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "la copia abre y está íntegra")
    tablas = con.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table'").fetchone()["n"]
    check(tablas >= 15, f"la copia trae el esquema completo ({tablas} tablas)")
    con.close()

print("\n=== 2. la rotación conserva las últimas y no borra de más ===")
for _ in range(3):
    time.sleep(1.1)   # los nombres llevan el segundo
    comando("bash", str(RESPALDO), "--db", f"sqlite:///{base}", "--destino", str(destino), "--conservar", "2")

quedan = sorted(p.name for p in destino.glob("estudio-*"))
check(len(quedan) == 2, f"con --conservar 2 quedan 2 copias ({len(quedan)})")

print("\n=== 3. una base que no existe lo dice, no deja un archivo que parece respaldo ===")
resultado = comando("bash", str(RESPALDO), "--db", f"sqlite:///{trabajo / 'no-existe.db'}",
                    "--destino", str(destino))
check(resultado.returncode == 1, "termina con error")
check("no encuentro la base" in resultado.stderr, "y explica por qué")
check(len(sorted(destino.glob("estudio-*"))) == 2, "sin dejar copias falsas")

shutil.rmtree(trabajo, ignore_errors=True)

if fallos:
    print(f"\n✗ {len(fallos)} comprobación(es) fallaron")
    sys.exit(1)
print("\n✓ el respaldo de la oficina funciona")
