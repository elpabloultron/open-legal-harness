"""Pruebas del servidor MCP del CRM: protocolo y herramientas.

La última prueba arranca el servidor de verdad por stdio (subproceso), que es como
lo va a usar el agente: si el handshake falla, sirve de nada que las funciones
internas estén bien.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from openlegal import auth, service  # noqa: E402
from openlegal.db import DB  # noqa: E402
from openlegal.mcp import Contexto, HERRAMIENTAS, responder  # noqa: E402


class BaseMCP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f"sqlite:///{pathlib.Path(self.tmp.name) / 'mcp.db'}"
        self.db = DB(self.url)
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio MCP", modo="oficina")
        socio_id = auth.crear_usuario(self.db, self.estudio, "Sofía Soto", "socia@mcp.cl", "socio", "clave")
        abogado_id = auth.crear_usuario(self.db, self.estudio, "Ana Pérez", "ana@mcp.cl", "abogado", "clave")
        self.socio = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (socio_id,))
        self.socio.pop("password_hash", None)
        self.cliente_id = service.crear_cliente(
            self.db, self.socio, "Constructora Andes SpA", "76.543.210-3", tipo_persona="juridica"
        )
        self.causa = service.crear_causa(
            self.db, self.socio, "Pérez con Andes SpA", cliente_id=self.cliente_id,
            rol_rit="C-1234-2026", materia="laboral",
        )
        service.asignar(self.db, self.socio, self.causa, abogado_id, "responsable")
        self.ctx = Contexto(self.url, "socia@mcp.cl")

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def llamar(self, herramienta: str, **argumentos):
        solicitud = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": herramienta, "arguments": argumentos},
        }
        respuesta = responder(solicitud, self.ctx)
        resultado = respuesta["result"]
        texto = resultado["content"][0]["text"]
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            datos = texto  # los errores vuelven como texto legible, no como JSON
        return datos, resultado["isError"]


class TestProtocolo(BaseMCP):
    def test_initialize_declara_herramientas(self):
        respuesta = responder({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, self.ctx)
        resultado = respuesta["result"]
        self.assertIn("tools", resultado["capabilities"])
        self.assertEqual(resultado["serverInfo"]["name"], "openlegal-crm")
        self.assertIn("Art. 66", resultado["instructions"])

    def test_tools_list_expone_esquemas(self):
        respuesta = responder({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.ctx)
        herramientas = respuesta["result"]["tools"]
        self.assertEqual(len(herramientas), len(HERRAMIENTAS))
        nombres = {h["name"] for h in herramientas}
        for esperada in ("crm_causa_leer", "crm_plazo_crear", "crm_audiencia_crear",
                         "crm_ia_redactar", "crm_ia_registrar"):
            self.assertIn(esperada, nombres)
        for herramienta in herramientas:
            self.assertEqual(herramienta["inputSchema"]["type"], "object")
            self.assertTrue(herramienta["description"])

    def test_notificaciones_no_responden(self):
        self.assertIsNone(responder({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.ctx))
        self.assertIsNone(responder({"jsonrpc": "2.0", "method": "notifications/cancelled"}, self.ctx))

    def test_metodo_desconocido(self):
        respuesta = responder({"jsonrpc": "2.0", "id": 9, "method": "resources/list"}, self.ctx)
        self.assertEqual(respuesta["error"]["code"], -32601)


class TestHerramientas(BaseMCP):
    def test_estudio_y_busqueda(self):
        estudio, error = self.llamar("crm_estudio")
        self.assertFalse(error)
        self.assertEqual(estudio["usuario"], "Sofía Soto")
        self.assertEqual(estudio["conteos"]["causas"], 1)

        busqueda, _ = self.llamar("crm_causa_buscar", texto="andes")
        self.assertEqual(busqueda["total"], 1)
        self.assertEqual(busqueda["causas"][0]["rol_rit"], "C-1234-2026")

        vacia, _ = self.llamar("crm_causa_buscar", texto="no existe")
        self.assertEqual(vacia["total"], 0)

    def test_leer_causa_completa(self):
        plazo = service.crear_plazo(
            self.db, self.socio, self.causa, "Contestar demanda", dias=8, fecha_notificacion="2026-09-17"
        )
        service.crear_audiencia(self.db, self.socio, self.causa, "Preparatoria", "2026-10-05", "09:30")
        detalle, error = self.llamar("crm_causa_leer", causa_id=self.causa)
        self.assertFalse(error)
        self.assertEqual(detalle["causa"]["caratula"], "Pérez con Andes SpA")
        self.assertEqual(len(detalle["plazos"]), 1)
        self.assertEqual(detalle["plazos"][0]["fecha_vencimiento"], plazo["fecha_vencimiento"])
        self.assertEqual(len(detalle["audiencias"]), 1)
        self.assertEqual([f["nombre"] for f in detalle["equipo"]], ["Sofía Soto", "Ana Pérez"])

    def test_calcular_plazo_art_66(self):
        calculo, error = self.llamar("crm_plazo_calcular", notificacion="2026-09-17", dias=8)
        self.assertFalse(error)
        self.assertEqual(calculo["fecha_vencimiento"], "2026-09-29")
        self.assertFalse(calculo["feriados_pendientes_de_validacion"], "2026 ya está validado")
        self.assertEqual(calculo["advertencias"], [])

    def test_calculo_que_cruza_a_un_año_sin_validar_avisa(self):
        calculo, error = self.llamar("crm_plazo_calcular", notificacion="2026-12-30", dias=3)
        self.assertFalse(error)
        self.assertTrue(any("2027" in a for a in calculo["advertencias"]))

    def test_crear_plazo_y_verlo_en_el_listado(self):
        creado, error = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Contestar demanda",
            dias=8, notificacion="2026-09-17",
        )
        self.assertFalse(error)
        self.assertEqual(creado["fecha_vencimiento"], "2026-09-29")
        self.assertEqual(len(creado["detalle"]), 12)  # incluye feriados y domingo

        listado, _ = self.llamar("crm_plazo_listar", causa_id=self.causa)
        self.assertEqual(listado["total"], 1)

        cumplido, _ = self.llamar("crm_plazo_cumplido", plazo_id=creado["plazo_id"])
        self.assertTrue(cumplido["ok"])
        listado, _ = self.llamar("crm_plazo_listar", causa_id=self.causa)
        self.assertEqual(listado["total"], 0)

    def test_agenda_audiencia_y_documento(self):
        audiencia, error = self.llamar(
            "crm_audiencia_crear", causa_id=self.causa, tipo="Comparendo", fecha="2026-09-30",
            hora="11:00", modalidad="remota", lugar_o_url="https://zoom.us/j/1",
        )
        self.assertFalse(error)
        self.assertGreater(audiencia["audiencia_id"], 0)

        documento, _ = self.llamar(
            "crm_documento_registrar", causa_id=self.causa, nombre="demanda.pdf",
            ruta="/tmp/demanda.pdf", visibilidad="interno",
        )
        self.assertGreater(documento["documento_id"], 0)
        # lo registrado queda en la bitácora
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria")]
        self.assertIn("audiencia.crear", acciones)
        self.assertIn("documento.registrar", acciones)

    def test_minimizar_y_registrar_envio(self):
        estado, _ = self.llamar("crm_ia_estado", causa_id=self.causa)
        self.assertFalse(estado["autorizado"])
        self.assertIn("Constructora Andes SpA", estado["terminos_a_minimizar"])

        # sin autorización, el registro se niega con un error legible para el agente
        _, error = self.llamar(
            "crm_ia_registrar", causa_id=self.causa, proveedor="anthropic", texto="expediente"
        )
        self.assertTrue(error)

        service.autorizar_ia(self.db, self.socio, self.causa, titular="Representante legal")
        limpio, _ = self.llamar(
            "crm_ia_redactar", causa_id=self.causa,
            texto="Demanda de 76.543.210-3 contra Constructora Andes SpA",
        )
        self.assertNotIn("76.543.210-3", limpio["texto_minimizado"])
        self.assertEqual(len(limpio["mapa_local"]), 2)

        registro, error = self.llamar(
            "crm_ia_registrar", causa_id=self.causa, proveedor="anthropic", modelo="claude-opus-4.7",
            texto=limpio["texto_minimizado"], redactado=True,
        )
        self.assertFalse(error)
        self.assertTrue(registro["redactado"])

    def test_error_legible_cuando_la_causa_no_existe(self):
        # el agente tiene que poder leer el motivo, no un volcado técnico
        mensaje, error = self.llamar("crm_plazo_listar", causa_id=999)
        self.assertTrue(error)
        self.assertIn("ErrorPermiso", str(mensaje))


class TestServidorRealPorStdio(BaseMCP):
    def test_handshake_y_creacion_de_plazo(self):
        """Arranca `openlegal mcp` de verdad y le habla como le hablaría el agente."""
        entorno = {
            "OPENLEGAL_MCP_DB": self.url,
            "OPENLEGAL_MCP_USUARIO": "socia@mcp.cl",
            "PATH": "/usr/bin:/bin",
        }
        proceso = subprocess.Popen(
            [sys.executable, "-m", "openlegal.cli", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=RAIZ, env={**entorno, "PYTHONPATH": str(RAIZ / "src")},
        )
        try:
            def pedir(solicitud: dict) -> dict:
                proceso.stdin.write(json.dumps(solicitud) + "\n")
                proceso.stdin.flush()
                return json.loads(proceso.stdout.readline())

            inicio = pedir({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(inicio["result"]["serverInfo"]["name"], "openlegal-crm")

            listado = pedir({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertGreaterEqual(len(listado["result"]["tools"]), 12)

            creacion = pedir({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "crm_plazo_crear",
                    "arguments": {
                        "causa_id": self.causa, "descripcion": "Contestar demanda",
                        "dias": 8, "notificacion": "2026-09-17",
                    },
                },
            })
            resultado = json.loads(creacion["result"]["content"][0]["text"])
            self.assertFalse(creacion["result"]["isError"])
            self.assertEqual(resultado["fecha_vencimiento"], "2026-09-29")

            # y quedó guardado en la base
            guardado = self.db.uno(
                "SELECT descripcion, fecha_vencimiento FROM plazos WHERE causa_id = ?", (self.causa,)
            )
            self.assertEqual(guardado["fecha_vencimiento"], "2026-09-29")
        finally:
            proceso.stdin.close()
            proceso.terminate()
            proceso.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
