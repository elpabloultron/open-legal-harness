"""Prueba de punta a punta de los avisos: un estudio, plazos, y un servidor SMTP local.

No se simula el correo: se levanta un servidor SMTP de verdad en localhost, se apunta la
configuración del CRM a él y se corre el comando real (`openlegal notificar`). Lo que entra
al servidor es lo que el estudio habría recibido.

    python3 scripts/e2e_avisos.py
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

RAIZ = pathlib.Path(__file__).resolve().parents[1]
PY = RAIZ / ".venv" / "bin" / "python"
CLI = str(RAIZ / ".venv" / "bin" / "openlegal")


class ManejadorSMTP(socketserver.StreamRequestHandler):
    """Servidor SMTP mínimo: saluda, acepta AUTH LOGIN y guarda lo que le mandan."""

    recibidos: list[dict] = []

    def handle(self) -> None:
        self.wfile.write(b"220 localhost ESMTP prueba\r\n")
        mensaje: dict = {"de": "", "para": [], "cuerpo": ""}
        while True:
            linea = self.rfile.readline()
            if not linea:
                return
            texto = linea.decode("utf-8", "replace").strip()
            verbo = texto.split(" ")[0].upper()
            if verbo in ("EHLO", "HELO"):
                self.wfile.write(b"250-localhost\r\n250-AUTH LOGIN PLAIN\r\n250 OK\r\n")
            elif verbo == "AUTH":
                self.wfile.write(b"334 " + base64.b64encode(b"Usuario:") + b"\r\n")
                self.rfile.readline()
                self.wfile.write(b"334 " + base64.b64encode(b"Clave:") + b"\r\n")
                self.rfile.readline()
                self.wfile.write(b"235 autenticado\r\n")
            elif verbo == "MAIL":
                mensaje["de"] = texto
                self.wfile.write(b"250 OK\r\n")
            elif verbo == "RCPT":
                mensaje["para"].append(texto)
                self.wfile.write(b"250 OK\r\n")
            elif verbo == "DATA":
                self.wfile.write(b"354 manda el mensaje\r\n")
                lineas = []
                while True:
                    cuerpo = self.rfile.readline()
                    if not cuerpo or cuerpo.strip() == b".":
                        break
                    lineas.append(cuerpo.decode("utf-8", "replace"))
                mensaje["cuerpo"] = "".join(lineas)
                ManejadorSMTP.recibidos.append(dict(mensaje))
                self.wfile.write(b"250 OK recibido\r\n")
            elif verbo == "QUIT":
                self.wfile.write(b"221 adios\r\n")
                return
            else:
                self.wfile.write(b"250 OK\r\n")


class ServidorSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def cli(*argumentos: str, entorno: dict) -> str:
    resultado = subprocess.run(
        [CLI, "--db", f"sqlite:///{DB}", *argumentos],
        capture_output=True, text=True, env=entorno, cwd=str(RAIZ),
    )
    if resultado.returncode != 0:
        print(f"  ✗ el comando falló: {' '.join(argumentos)}")
        print(f"    stderr: {resultado.stderr[-400:]}")
    return (resultado.stdout + resultado.stderr).strip()


trabajo = pathlib.Path(tempfile.mkdtemp(prefix="e2e-avisos-"))
DB = trabajo / "estudio.db"
config_archivo = trabajo / "notificaciones.json"
avisos_log = trabajo / "avisos.log"

servidor = ServidorSMTP(("127.0.0.1", 0), ManejadorSMTP)
puerto = servidor.server_address[1]
threading.Thread(target=servidor.serve_forever, daemon=True).start()
print(f"=== servidor SMTP de prueba escuchando en 127.0.0.1:{puerto} ===")

config_archivo.write_text(json.dumps({
    "email": {"host": "127.0.0.1", "puerto": puerto, "usuario": "avisos@estudio.cl",
              "clave": "clave-de-prueba", "de": "Estudio Prueba <avisos@estudio.cl>",
              "seguridad": "ninguna"},
    "sms": {"proveedor": "consola"},
    "canales_por_defecto": ["email", "sms"],
    "dias_de_aviso": 3,
}), encoding="utf-8")

entorno = dict(os.environ)
entorno["OPENLEGAL_CONFIG"] = str(config_archivo)
entorno["OPENLEGAL_AVISOS_LOG"] = str(avisos_log)

# --- el estudio, con Python, porque acá se prueban los avisos y no la creación ---
sys.path.insert(0, str(RAIZ / "src"))
from openlegal import auth, service  # noqa: E402
from openlegal.db import DB as Base  # noqa: E402

base = Base(f"sqlite:///{DB}")
base.migrar()
estudio = auth.crear_estudio(base, "Estudio Prueba", modo="oficina")
socio_id = auth.crear_usuario(base, estudio, "Sofia Soto", "socia@test.cl", "socio", "clave-de-prueba")
abogado_id = auth.crear_usuario(base, estudio, "Ana Pérez", "ana@test.cl", "abogado", "clave-de-prueba")
socio = base.uno("SELECT * FROM usuarios WHERE id = ?", (socio_id,))
socio.pop("password_hash", None)
cliente_id = service.crear_cliente(base, socio, "Lucía Herrera", "11.111.111-1", email="lucia@ejemplo.cl")
causa_id = service.crear_causa(base, socio, "Herrera con Fondo del Norte", cliente_id=cliente_id,
                              rol_rit="C-1234-2026", tribunal="1° Juzgado Civil de Santiago", materia="civil")
base.cerrar()

print("\n=== 1. se cargan los teléfonos (para los SMS) ===")
print("  " + cli("usuario", "editar", "--email", "ana@test.cl", "--telefono", "+56912345678", entorno=entorno))
print("  " + cli("usuario", "editar", "--email", "socia@test.cl", "--telefono", "+56987654321", entorno=entorno))

print("\n=== 2. a la abogada le asignan un plazo: el aviso tiene que salir al instante ===")
base = Base(f"sqlite:///{DB}")
plazo = service.crear_plazo(base, socio, causa_id, "Contestar traslado", dias=5,
                            fecha_notificacion="2026-09-14", responsable_id=abogado_id)
print(f"  plazo {plazo['id']} creado, vence el {plazo['fecha_vencimiento']}")
base.cerrar()
print("  " + cli("notificar", "--usuario", "socia@test.cl", "--generar", "--enviar", entorno=entorno).replace("\n", "\n  "))

print("\n=== 3. y un plazo que vence pasado mañana: recordatorio ===")
base = Base(f"sqlite:///{DB}")
segundo = service.crear_plazo(base, socio, causa_id, "Presentar escrito de prueba", dias=3,
                              fecha_notificacion="2026-09-17", responsable_id=socio_id)
base.ejecutar("UPDATE plazos SET fecha_vencimiento = ? WHERE id = ?", ("2026-09-20", segundo["id"]))
base.cerrar()
print("  " + cli("notificar", "--usuario", "socia@test.cl", "--generar", "--enviar", "--dias", "3", entorno=entorno).replace("\n", "\n  "))

print("\n=== 4. los correos que llegaron al servidor de verdad ===")
for numero, mensaje in enumerate(ManejadorSMTP.recibidos, 1):
    asunto = [linea for linea in mensaje["cuerpo"].splitlines() if linea.lower().startswith("subject:")]
    print(f"  --- correo {numero} ---")
    print(f"  de:      {mensaje['de']}")
    print(f"  para:    {' '.join(mensaje['para'])}")
    print(f"  asunto:  {asunto[0] if asunto else '(sin asunto)'}")
    cuerpo = mensaje["cuerpo"].split("\n\n", 1)[1] if "\n\n" in mensaje["cuerpo"] else mensaje["cuerpo"]
    print("  cuerpo:  " + "\n           ".join(cuerpo.strip().splitlines()[:4]))

print("\n=== 5. los SMS de prueba (no salen a la red) ===")
print("  " + (avisos_log.read_text(encoding="utf-8").replace("\n", "\n  ") if avisos_log.is_file() else "(nada)"))
print("\n=== 6. correr otra vez el mismo día no puede duplicar nada ===")
print("  " + cli("notificar", "--usuario", "socia@test.cl", "--generar", "--enviar", "--dias", "3",
                  entorno=entorno).replace("\n", "\n  "))
print(f"  correos recibidos en total: {len(ManejadorSMTP.recibidos)}")

print("\n=== 7. estado de la cola ===")
print("  " + cli("notificar", "--usuario", "socia@test.cl", "--estado", entorno=entorno).replace("\n", "\n  "))
print("  " + cli("notificar", "--config", entorno=entorno).replace("\n", "\n  "))

print("\n=== 8. la cola, en la base, con sus huellas ===")
base = Base(f"sqlite:///{DB}")
for fila in base.todos("SELECT id, canal, destino, estado, asunto, clave FROM notificaciones ORDER BY id"):
    print(f"  {fila['id']} {fila['canal']:5s} {fila['destino']:14s} {fila['estado']:9s} "
          f"{(fila['asunto'] or '')[:48]:50s} {fila['clave']}")
base.cerrar()

servidor.shutdown()
shutil.rmtree(trabajo, ignore_errors=True)
print("\n(listo)")
