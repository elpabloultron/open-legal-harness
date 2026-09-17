"""Pruebas de sesiones multiusuario y roles del panel (se saltan sin el extra [ui])."""
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

TOKEN = "token-panel-prueba"
CLAVE = "clave-segura-2026"


@unittest.skipUnless(TIENE_UI, "sin el extra [ui] no se prueban las sesiones web")
class TestSesionesYRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        url = f"sqlite:///{pathlib.Path(self.tmp.name) / 'sesiones.db'}"
        db = DB(url)
        db.migrar()
        estudio = auth.crear_estudio(db, "Estudio Sesiones", modo="oficina")
        self.socio_id = auth.crear_usuario(db, estudio, "Sofía Soto", "socia@e.cl", "socio", CLAVE)
        self.admin_id = auth.crear_usuario(db, estudio, "Marta Admin", "admin@e.cl", "administrador", CLAVE)
        self.abogado1 = auth.crear_usuario(db, estudio, "Ana Pérez", "ana@e.cl", "abogado", CLAVE)
        self.abogado2 = auth.crear_usuario(db, estudio, "Luis Rojas", "luis@e.cl", "abogado", CLAVE)
        self.secretaria = auth.crear_usuario(db, estudio, "Carmen Díaz", "secretaria@e.cl", "administrativo", CLAVE)
        socio = db.uno("SELECT * FROM usuarios WHERE id = ?", (self.socio_id,))
        socio.pop("password_hash", None)
        cliente = service.crear_cliente(db, socio, "Constructora Andes SpA", "76.543.210-K")
        self.causa = service.crear_causa(db, socio, "Pérez con Andes SpA", cliente_id=cliente)
        service.asignar(db, socio, self.causa, self.abogado1, "responsable")
        service.crear_plazo(db, socio, self.causa, "Contestar demanda", dias=8, fecha_notificacion="2026-09-17")
        db.cerrar()
        self.url = url
        self.cliente_http = TestClient(crear_app(url, TOKEN))

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------- utilidades
    def entrar(self, email: str, clave: str = CLAVE):
        return self.cliente_http.post("/api/login", json={"email": email, "password": clave})

    def salir(self):
        return self.cliente_http.post("/api/logout")

    # -------------------------------------------------------------------- tests
    def test_sin_sesion_ni_token_da_401(self):
        self.assertEqual(self.cliente_http.get("/api/causas").status_code, 401)
        self.assertEqual(self.cliente_http.get("/api/sesion").status_code, 401)

    def test_token_del_panel_sigue_sirviendo_para_el_abogado_solo(self):
        respuesta = self.cliente_http.get("/api/sesion", headers={"X-OpenLegal-Token": TOKEN})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["via"], "token")
        self.assertEqual(respuesta.json()["usuario"]["rol"], "socio")

    def test_el_panel_abre_sin_credenciales_pero_la_api_no(self):
        # La página se sirve siempre (es HTML estático) para que la secretaria pueda entrar.
        self.assertEqual(self.cliente_http.get("/").status_code, 200)
        # ...y la cookie del token se pone sobre ESA respuesta, no en una descartada.
        con_token = self.cliente_http.get(f"/?token={TOKEN}")
        self.assertIn("openlegal_token", con_token.cookies)
        # Los datos siguen exigiendo credenciales.
        self.cliente_http.cookies.clear()
        self.assertEqual(self.cliente_http.get("/api/causas").status_code, 401)

    def test_login_correcto_e_incorrecto(self):
        self.assertEqual(self.entrar("ana@e.cl", "clave-mala").status_code, 401)
        respuesta = self.entrar("ana@e.cl")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["usuario"]["rol"], "abogado")

        sesion = self.cliente_http.get("/api/sesion").json()
        self.assertEqual(sesion["via"], "sesion")
        self.assertEqual(sesion["usuario"]["email"], "ana@e.cl")

        # el intento fallido queda registrado en la bitácora
        db = DB(self.url)
        fallidos = db.todos("SELECT accion, detalle FROM auditoria WHERE accion = 'login.fallido'")
        db.cerrar()
        self.assertEqual(len(fallidos), 1)
        self.assertIn("ana@e.cl", fallidos[0]["detalle"])

    def test_cada_rol_ve_lo_suyo(self):
        # el abogado asignado ve la causa; el otro abogado no ve nada
        self.entrar("ana@e.cl")
        causas_ana = self.cliente_http.get("/api/causas").json()
        self.assertEqual([c["id"] for c in causas_ana], [self.causa])
        self.salir()

        self.entrar("luis@e.cl")
        self.assertEqual(self.cliente_http.get("/api/causas").json(), [])
        self.salir()

        # la secretaria (administrativo) ve todo el estudio para facturar
        self.entrar("secretaria@e.cl")
        self.assertEqual(len(self.cliente_http.get("/api/causas").json()), 1)
        self.assertEqual(self.cliente_http.get("/api/panel").status_code, 200)
        self.salir()

        # el administrador también ve todo y puede gestionar usuarios
        self.entrar("admin@e.cl")
        self.assertEqual(len(self.cliente_http.get("/api/causas").json()), 1)
        db = DB(self.url)
        administrador = db.uno("SELECT * FROM usuarios WHERE id = ?", (self.admin_id,))
        administrador.pop("password_hash", None)
        self.assertTrue(auth.puede(db, administrador, "usuario.gestionar"))
        self.assertFalse(auth.puede(db, administrador, "honorario.leer.todos"))
        db.cerrar()

    def test_logout_invalida_la_sesion(self):
        self.entrar("socia@e.cl")
        self.assertEqual(self.cliente_http.get("/api/sesion").status_code, 200)
        self.assertEqual(self.salir().status_code, 200)
        self.assertEqual(self.cliente_http.get("/api/sesion").status_code, 401)

        db = DB(self.url)
        sesiones = db.uno("SELECT COUNT(*) AS total FROM sesiones")
        acciones = [f["accion"] for f in db.todos("SELECT accion FROM auditoria ORDER BY id")]
        db.cerrar()
        self.assertEqual(sesiones["total"], 0)
        self.assertIn("logout", acciones)

    def test_plazo_creado_por_sesion_queda_firmado(self):
        self.entrar("ana@e.cl")
        creado = self.cliente_http.post(
            "/api/plazos",
            json={"causa_id": self.causa, "descripcion": "Apelar", "dias": 5, "notificacion": "2026-09-21"},
        ).json()
        self.assertEqual(creado["fecha_vencimiento"], "2026-09-26")

        db = DB(self.url)
        fila = db.uno(
            "SELECT u.email FROM plazos p JOIN usuarios u ON u.id = p.responsable_id WHERE p.id = ?",
            (creado["id"],),
        )
        bitacora = db.uno(
            "SELECT u.email FROM auditoria a JOIN usuarios u ON u.id = a.usuario_id "
            "WHERE a.accion = 'plazo.crear' AND a.entidad_id = ?",
            (creado["id"],),
        )
        db.cerrar()
        self.assertEqual(fila["email"], "ana@e.cl")
        self.assertEqual(bitacora["email"], "ana@e.cl")


if __name__ == "__main__":
    unittest.main()
