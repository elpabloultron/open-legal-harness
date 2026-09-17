"""Pruebas de humo del nucleo: roles, aislamiento por causa, plazos Art. 66 CPC.

Solo libreria estandar:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, plazos, service  # noqa: E402
from openlegal.db import DB  # noqa: E402


class BaseConDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(f"sqlite:///{pathlib.Path(self.tmp.name) / 'test.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socio = self._usuario("Sofia Soto", "socia@test.cl", "socio", "clave-segura")
        self.abogado = self._usuario("Ana Perez", "ana@test.cl", "abogado", "clave-segura")
        self.otro_abogado = self._usuario("Luis Rojas", "luis@test.cl", "abogado", "clave-segura")
        self.paralegal = self._usuario("Carla Diaz", "carla@test.cl", "paralegal", "clave-segura")
        self.admin = self._usuario("Pedro Fin", "pedro@test.cl", "administrativo", "clave-segura")

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol, password) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, password)
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        fila.pop("password_hash", None)
        fila["token"] = ""
        return fila


class TestSchemaYEstudio(BaseConDB):
    def test_migracion_idempotente(self):
        self.db.migrar()
        tablas = {f["name"] for f in self.db.todos("SELECT name FROM sqlite_master WHERE type='table'")}
        for esperada in ("estudios", "usuarios", "causas", "causa_equipo", "plazos", "audiencias", "auditoria"):
            self.assertIn(esperada, tablas)

    def test_rol_invalido(self):
        with self.assertRaises(ValueError):
            auth.crear_usuario(self.db, self.estudio, "X", "x@test.cl", "superadmin")

    def test_autenticacion(self):
        self.assertIsNotNone(auth.autenticar(self.db, "socia@test.cl", "clave-segura"))
        self.assertIsNone(auth.autenticar(self.db, "socia@test.cl", "clave-mala"))
        sesion = auth.autenticar(self.db, "socia@test.cl", "clave-segura")
        self.assertEqual(auth.usuario_por_token(self.db, sesion["token"])["email"], "socia@test.cl")


class TestAislamientoPorCausa(BaseConDB):
    def setUp(self):
        super().setUp()
        self.cliente = service.crear_cliente(self.db, self.socio, "Constructora Andes SpA", "76.543.210-K")
        self.causa = service.crear_causa(
            self.db, self.socio, "Perez con Andes SpA", cliente_id=self.cliente, materia="laboral"
        )
        service.asignar(self.db, self.socio, self.causa, self.abogado["id"], "responsable")
        service.asignar(self.db, self.socio, self.causa, self.paralegal["id"], "apoyo")

    def test_socio_ve_todas_las_causas(self):
        self.assertEqual(len(auth.causas_visibles(self.db, self.socio)), 1)
        self.assertTrue(auth.puede(self.db, self.socio, "causa.leer", self.causa))

    def test_abogado_solo_sus_causas(self):
        self.assertTrue(auth.puede(self.db, self.abogado, "causa.leer", self.causa))
        self.assertFalse(auth.puede(self.db, self.otro_abogado, "causa.leer", self.causa))
        self.assertEqual(auth.causas_visibles(self.db, self.otro_abogado), [])
        with self.assertRaises(auth.ErrorPermiso):
            auth.exigir(self.db, self.otro_abogado, "causa.leer", self.causa)

    def test_abogado_no_puede_asignar_ni_ver_honorarios_de_todos(self):
        self.assertFalse(auth.puede(self.db, self.abogado, "causa.asignar", self.causa))
        self.assertFalse(auth.puede(self.db, self.abogado, "honorario.leer.todos"))
        self.assertFalse(auth.puede(self.db, self.paralegal, "honorario.leer", self.causa))

    def test_administrativo_ve_honorarios_pero_no_redacta(self):
        self.assertTrue(auth.puede(self.db, self.admin, "honorario.editar"))
        self.assertFalse(auth.puede(self.db, self.admin, "documento.crear", self.causa))
        self.assertTrue(auth.puede(self.db, self.admin, "causa.leer", self.causa))

    def test_paralegal_puede_plazos_pero_no_cerrar_causas(self):
        self.assertTrue(auth.puede(self.db, self.paralegal, "plazo.crear", self.causa))
        self.assertFalse(auth.puede(self.db, self.paralegal, "plazo.cerrar", self.causa))

    def test_cliente_no_ve_lo_interno(self):
        cliente_u = self._usuario("Cliente Andes", "cliente@test.cl", "cliente", "clave-segura")
        self.assertTrue(auth.puede(self.db, cliente_u, "causa.leer.propia"))
        self.assertFalse(auth.puede(self.db, cliente_u, "honorario.leer"))
        self.assertFalse(auth.puede(self.db, cliente_u, "documento.crear"))
        self.assertFalse(auth.puede(self.db, cliente_u, "auditoria.leer"))

    def test_causa_de_otro_estudio_es_invisible(self):
        otro_estudio = auth.crear_estudio(self.db, "Otro Estudio", modo="oficina")
        ajeno = auth.crear_usuario(self.db, otro_estudio, "Intruso", "intruso@otro.cl", "socio", "x")
        intruso = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (ajeno,))
        intruso.pop("password_hash", None)
        self.assertFalse(auth.puede(self.db, intruso, "causa.leer", self.causa))
        self.assertEqual(auth.causas_visibles(self.db, intruso), [])

    def test_auditoria_registra_cada_accion(self):
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria ORDER BY id")]
        self.assertIn("causa.crear", acciones)
        self.assertIn("causa.asignar", acciones)
        self.assertIn("cliente.crear", acciones)

    def test_panel_socio(self):
        datos = service.panel(self.db, self.socio)
        self.assertEqual(datos["plazos_pendientes"], 0)
        carga = {f["nombre"]: f["causas"] for f in datos["carga_por_abogado"]}
        self.assertEqual(carga["Ana Perez"], 1)


class TestPlazosArt66(BaseConDB):
    def test_fin_de_semana_y_feriados_no_cuentan(self):
        # Notificacion jueves 17-09-2026. 18 y 19 sep son feriados, 20 es domingo.
        resultado = plazos.vencimiento(dt.date(2026, 9, 17), 3)
        self.assertEqual(resultado["fecha_vencimiento"], "2026-09-23")
        dias = {d["fecha"]: d["dia_contado"] for d in resultado["detalle"]}
        self.assertIsNone(dias["2026-09-18"])
        self.assertIsNone(dias["2026-09-19"])
        self.assertIsNone(dias["2026-09-20"])
        self.assertEqual(dias["2026-09-21"], 1)
        self.assertEqual(dias["2026-09-23"], 3)

    def test_sabado_es_habil(self):
        resultado = plazos.vencimiento(dt.date(2026, 9, 21), 5)
        self.assertEqual(resultado["fecha_vencimiento"], "2026-09-26")

    def test_domingo_siempre_suspende(self):
        # 27-09-2026 es domingo: el computo no puede caer en domingo.
        resultado = plazos.vencimiento(dt.date(2026, 9, 25), 3)
        self.assertNotEqual(dt.date.fromisoformat(resultado["fecha_vencimiento"]).weekday(), 6)

    def test_dias_invalidos(self):
        with self.assertRaises(ValueError):
            plazos.vencimiento(dt.date(2026, 9, 17), 0)

    def test_plazo_guardado_en_causa(self):
        cliente = service.crear_cliente(self.db, self.socio, "Cliente Plazo")
        causa = service.crear_causa(self.db, self.socio, "Plazo con Cliente", cliente_id=cliente)
        resultado = service.crear_plazo(
            self.db, self.socio, causa, "Contestar demanda", dias=8, fecha_notificacion="2026-09-17"
        )
        self.assertEqual(resultado["fecha_vencimiento"], "2026-09-29")
        proximos = service.vencimientos(self.db, self.socio, "2026-09-01", 60)
        self.assertEqual(len(proximos), 1)
        service.marcar_cumplido(self.db, self.socio, resultado["id"])
        self.assertEqual(service.vencimientos(self.db, self.socio, "2026-09-01", 60), [])


if __name__ == "__main__":
    unittest.main()
