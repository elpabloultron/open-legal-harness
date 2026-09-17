"""Pruebas de la capa web (panel del CRM). Se saltan si falta el extra [ui]."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, service  # noqa: E402
from openlegal.db import DB  # noqa: E402

try:
    from fastapi.testclient import TestClient

    from openlegal.web import crear_app

    TIENE_UI = True
except ModuleNotFoundError:  # pragma: no cover - depende del entorno
    TIENE_UI = False

TOKEN = "token-de-prueba"


@unittest.skipUnless(TIENE_UI, "sin el extra [ui] no se prueban las rutas web")
class TestPanelWeb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        url = f"sqlite:///{pathlib.Path(self.tmp.name) / 'panel.db'}"
        db = DB(url)
        db.migrar()
        estudio = auth.crear_estudio(db, "Estudio Panel", modo="oficina")
        socio = auth.crear_usuario(db, estudio, "Sofia Soto", "socia@panel.cl", "socio", "clave")
        abogado = auth.crear_usuario(db, estudio, "Ana Perez", "ana@panel.cl", "abogado", "clave")
        self.socio = db.uno("SELECT * FROM usuarios WHERE id = ?", (socio,))
        self.socio.pop("password_hash", None)
        abogado_fila = db.uno("SELECT * FROM usuarios WHERE id = ?", (abogado,))
        abogado_fila.pop("password_hash", None)
        cliente = service.crear_cliente(db, self.socio, "Constructora Andes SpA", "76.543.210-K")
        self.causa = service.crear_causa(db, self.socio, "Perez con Andes SpA", cliente_id=cliente)
        service.asignar(db, self.socio, self.causa, abogado, "responsable")
        service.crear_plazo(
            db, self.socio, self.causa, "Contestar demanda", dias=8, fecha_notificacion="2026-09-17"
        )
        db.cerrar()
        self.cliente = TestClient(crear_app(url, TOKEN))

    def tearDown(self):
        self.tmp.cleanup()

    def test_token_obligatorio(self):
        self.assertEqual(self.cliente.get("/").status_code, 401)
        self.assertEqual(self.cliente.get("/api/causas").status_code, 401)
        self.assertEqual(self.cliente.get("/api/causas", headers={"X-OpenLegal-Token": "malo"}).status_code, 401)

    def test_panel_y_estado(self):
        respuesta = self.cliente.get(f"/?token={TOKEN}")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("CRM Jurídico", respuesta.text)

        estado = self.cliente.get("/api/estado", headers={"X-OpenLegal-Token": TOKEN}).json()
        self.assertEqual(estado["estudio"]["nombre"], "Estudio Panel")
        self.assertEqual(estado["usuario"]["rol"], "socio")
        self.assertEqual(estado["conteos"]["causas"], 1)

    def test_causas_y_vencimientos(self):
        cabeceras = {"X-OpenLegal-Token": TOKEN}
        causas = self.cliente.get("/api/causas", headers=cabeceras).json()
        self.assertEqual(len(causas), 1)
        self.assertEqual(causas[0]["caratula"], "Perez con Andes SpA")
        self.assertEqual(causas[0]["proximo_vencimiento"]["fecha_vencimiento"], "2026-09-29")
        self.assertEqual(causas[0]["equipo"], ["Sofia Soto", "Ana Perez"])

        plazos = self.cliente.get("/api/plazos?dias=60&desde=2026-09-01", headers=cabeceras).json()
        self.assertEqual(len(plazos), 1)
        self.assertEqual(plazos[0]["fecha_vencimiento"], "2026-09-29")

    def test_crear_plazo_desde_el_panel(self):
        cabeceras = {"X-OpenLegal-Token": TOKEN}
        creado = self.cliente.post(
            "/api/plazos",
            headers=cabeceras,
            json={
                "causa_id": self.causa,
                "descripcion": "Apelar sentencia",
                "dias": 5,
                "notificacion": "2026-09-21",
            },
        ).json()
        self.assertEqual(creado["fecha_vencimiento"], "2026-09-26")

        calculo = self.cliente.get(
            "/api/calculo?notificacion=2026-09-17&dias=8", headers=cabeceras
        ).json()
        self.assertEqual(calculo["fecha_vencimiento"], "2026-09-29")
        festivos = [d for d in calculo["detalle"] if not d["habil"]]
        self.assertEqual({d["fecha"] for d in festivos}, {"2026-09-18", "2026-09-19", "2026-09-20", "2026-09-27"})

        cumplido = self.cliente.post(f"/api/plazos/{creado['id']}/cumplido", headers=cabeceras).json()
        self.assertTrue(cumplido["ok"])

    def test_panel_kpis(self):
        datos = self.cliente.get("/api/panel", headers={"X-OpenLegal-Token": TOKEN}).json()
        self.assertEqual(datos["plazos_pendientes"], 1)
        self.assertEqual(datos["causas_por_estado"][0]["total"], 1)


if __name__ == "__main__":
    unittest.main()
