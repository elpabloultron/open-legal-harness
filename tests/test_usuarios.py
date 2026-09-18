"""Pruebas de los datos del personal del estudio: teléfono, rol, contraseña y baja.

Solo libreria estandar:  python3 -m unittest discover -s tests

Lo que se cuida acá: que el estudio nunca quede sin quien lo administre, y que cambiar los
datos de una persona deje rastro en la bitácora.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth  # noqa: E402
from openlegal.db import DB  # noqa: E402


class BaseConEstudio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.db = DB(f"sqlite:///{self.raiz / 'test.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socio = self._usuario("Sofia Soto", "socia@test.cl", "socio")
        self.abogado = self._usuario("Ana Perez", "ana@test.cl", "abogado")

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, "clave-segura")
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def bitacora(self, accion: str) -> list[dict]:
        return self.db.todos("SELECT * FROM auditoria WHERE accion = ?", (accion,))


class TestActualizarUsuario(BaseConEstudio):
    def test_carga_el_telefono_para_los_avisos(self):
        fila = auth.actualizar_usuario(self.db, self.socio, "ana@test.cl", telefono="+56912345678")

        self.assertEqual(fila["telefono"], "+56912345678")
        self.assertEqual(self.db.uno("SELECT telefono FROM usuarios WHERE id = ?", (self.abogado["id"],))["telefono"],
                         "+56912345678")

    def test_cada_cambio_queda_en_la_bitacora(self):
        auth.actualizar_usuario(self.db, self.socio, "ana@test.cl", telefono="+56912345678")

        rastro = self.bitacora("usuario.actualizar")
        self.assertEqual(len(rastro), 1)
        self.assertEqual(rastro[0]["usuario_id"], self.socio["id"])
        self.assertIn("telefono", rastro[0]["detalle"])

    def test_cambia_el_rol(self):
        fila = auth.actualizar_usuario(self.db, self.socio, "ana@test.cl", rol="paralegal")
        self.assertEqual(fila["rol"], "paralegal")

    def test_deja_a_alguien_inactivo(self):
        fila = auth.actualizar_usuario(self.db, self.socio, "ana@test.cl", activo=False)
        self.assertEqual(fila["activo"], 0)

    def test_cambia_la_contrasena_sin_dejarla_escrita_en_la_bitacora(self):
        auth.actualizar_usuario(self.db, self.socio, "ana@test.cl", password="otra-clave-larga")
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (self.abogado["id"],))

        self.assertTrue(auth.verificar_password("otra-clave-larga", fila["password_hash"]))
        self.assertNotIn("otra-clave-larga", self.bitacora("usuario.actualizar")[0]["detalle"])

    def test_no_deja_al_estudio_sin_su_ultimo_socio(self):
        with self.assertRaises(ValueError) as caso:
            auth.actualizar_usuario(self.db, self.socio, "socia@test.cl", activo=False)
        self.assertIn("último socio", str(caso.exception))

        with self.assertRaises(ValueError):
            auth.actualizar_usuario(self.db, self.socio, "socia@test.cl", rol="abogado")

    def test_si_hay_otro_socio_el_cambio_se_permite(self):
        otro = self._usuario("Rosa Vera", "rosa@test.cl", "socio")

        fila = auth.actualizar_usuario(self.db, self.socio, "socia@test.cl", activo=False)
        self.assertEqual(fila["activo"], 0)
        self.assertEqual(self.db.uno("SELECT activo FROM usuarios WHERE id = ?", (otro["id"],))["activo"], 1)

    def test_exige_permiso(self):
        with self.assertRaises(auth.ErrorPermiso):
            auth.actualizar_usuario(self.db, self.abogado, "socia@test.cl", rol="paralegal")
        self.assertEqual(self.db.uno("SELECT rol FROM usuarios WHERE id = ?", (self.socio["id"],))["rol"], "socio")

    def test_sin_cambios_avisa(self):
        with self.assertRaises(ValueError):
            auth.actualizar_usuario(self.db, self.socio, "ana@test.cl")

    def test_usuario_inexistente_avisa(self):
        with self.assertRaises(ValueError):
            auth.actualizar_usuario(self.db, self.socio, "nadie@test.cl", telefono="+56900000000")


if __name__ == "__main__":
    unittest.main()
