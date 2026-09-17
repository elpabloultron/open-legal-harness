"""Pruebas de integracion contra PostgreSQL real (modo oficina).

Se saltan solas si no hay servidor configurado. Para correrlas:

    docker compose up -d
    LEGALCRM_TEST_PG=postgresql://legal:CAMBIAR_CLAVE@localhost:55432/estudio_test \
      PYTHONPATH=src python3 -m unittest tests.test_postgres_opcional -v

La base indicada se limpia (DROP SCHEMA public CASCADE) antes de cada prueba.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, plazos, service  # noqa: E402
from openlegal.db import DB  # noqa: E402

URL = os.environ.get("LEGALCRM_TEST_PG")


@unittest.skipUnless(URL, "sin LEGALCRM_TEST_PG: se omiten las pruebas de PostgreSQL")
class TestPostgres(unittest.TestCase):
    def setUp(self):
        self.db = DB(URL)
        for sentencia in ("DROP SCHEMA public CASCADE", "CREATE SCHEMA public"):
            self.db.ejecutar(sentencia)
        tablas = self.db.migrar()
        self.assertEqual(len(tablas), 17)

    def tearDown(self):
        self.db.cerrar()

    def test_ciclo_completo_con_bitacora(self):
        estudio = auth.crear_estudio(self.db, "Estudio PG", modo="oficina")
        socio_id = auth.crear_usuario(self.db, estudio, "Sofia Soto", "socia@pg.cl", "socio", "clave")
        abogado_id = auth.crear_usuario(
            self.db, estudio, "Ana Perez", "ana@pg.cl", "abogado", "clave", actor_id=socio_id
        )
        self.assertGreater(socio_id, 0)
        self.assertGreater(abogado_id, 0)

        socio = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (socio_id,))
        socio.pop("password_hash", None)
        abogado = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (abogado_id,))
        abogado.pop("password_hash", None)

        cliente = service.crear_cliente(self.db, socio, "Constructora Andes SpA", "76.543.210-K")
        causa = service.crear_causa(self.db, socio, "Perez con Andes SpA", cliente_id=cliente)
        service.asignar(self.db, socio, causa, abogado_id, "responsable")
        plazo = service.crear_plazo(
            self.db, socio, causa, "Contestar demanda", dias=8, fecha_notificacion="2026-09-17"
        )
        self.assertEqual(plazo["fecha_vencimiento"], "2026-09-29")

        # aislamiento y permisos, igual que en SQLite
        self.assertTrue(auth.puede(self.db, abogado, "causa.leer", causa))
        self.assertEqual(len(auth.causas_visibles(self.db, abogado)), 1)
        self.assertEqual(len(service.vencimientos(self.db, socio, "2026-09-01", 60)), 1)

        # la bitacora es lo que se rompia con el id devuelto como diccionario
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria ORDER BY id")]
        for esperada in ("usuario.crear", "cliente.crear", "causa.crear", "causa.asignar", "plazo.crear"):
            self.assertIn(esperada, acciones)
        # el actor de usuario.crear es el socio, no el usuario creado
        fila = self.db.uno(
            "SELECT usuario_id FROM auditoria WHERE accion = 'usuario.crear' AND entidad_id = ?",
            (abogado_id,),
        )
        self.assertEqual(fila["usuario_id"], socio_id)

    def test_autenticacion_y_sesion(self):
        estudio = auth.crear_estudio(self.db, "Estudio PG", modo="oficina")
        auth.crear_usuario(self.db, estudio, "Sofia Soto", "socia@pg.cl", "socio", "clave-segura")
        self.assertIsNone(auth.autenticar(self.db, "socia@pg.cl", "mala"))
        sesion = auth.autenticar(self.db, "socia@pg.cl", "clave-segura")
        self.assertEqual(auth.usuario_por_token(self.db, sesion["token"])["email"], "socia@pg.cl")

    def test_plazos_dias_habiles(self):
        resultado = plazos.vencimiento(__import__("datetime").date(2026, 9, 17), 3)
        self.assertEqual(resultado["fecha_vencimiento"], "2026-09-23")


if __name__ == "__main__":
    unittest.main()
