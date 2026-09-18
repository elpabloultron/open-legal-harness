"""Pruebas del bloqueo por intentos fallidos.

Solo libreria estandar:  python3 -m unittest discover -s tests

Es lo que hace que el panel pueda estar en la red de la oficina sin que se puedan probar
contraseñas a gusto. Lo que se cuida: que el bloqueo valga **también** con la contraseña
correcta (si no, no sirve de nada), que se destrabe solo, que una cuenta no arrastre a otra
parecida, y que destrabar a mano quede anotado.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, seguridad  # noqa: E402
from openlegal.db import DB  # noqa: E402

CLAVE = "clave-segura-de-prueba"


class BaseConUsuarios(unittest.TestCase):
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
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, CLAVE)
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def fallar(self, email: str = "ana@test.cl", veces: int | None = None) -> None:
        for _ in range(veces if veces is not None else seguridad.BLOQUEO_INTENTOS):
            auth.autenticar(self.db, email, "clave-equivocada")

    def bloquear_por_2fa(self, email: str = "ana@test.cl") -> None:
        for _ in range(seguridad.BLOQUEO_INTENTOS):
            auth.auditar(self.db, self.estudio, None, "login.2fa.fallido", "usuarios", None,
                         f"{email} · contraseña correcta, código ausente o inválido")


class TestBloqueo(BaseConUsuarios):
    def test_sin_fallos_no_hay_bloqueo(self):
        estado = seguridad.bloqueo(self.db, "ana@test.cl")
        self.assertFalse(estado["bloqueada"])
        self.assertEqual(estado["intentos"], 0)

    def test_unos_pocos_fallos_no_bloquean(self):
        self.fallar(veces=seguridad.BLOQUEO_INTENTOS - 1)
        self.assertFalse(seguridad.bloqueo(self.db, "ana@test.cl")["bloqueada"])

    def test_los_fallos_repetidos_bloquean(self):
        self.fallar()

        estado = seguridad.bloqueo(self.db, "ana@test.cl")
        self.assertTrue(estado["bloqueada"])
        self.assertGreater(estado["faltan_minutos"], 0)
        self.assertIsNotNone(estado["hasta"])

    def test_la_contraseña_correcta_no_saltea_el_bloqueo(self):
        self.fallar()

        with self.assertRaises(auth.ErrorBloqueado):
            auth.autenticar(self.db, "ana@test.cl", CLAVE)
        # Y sin sesión creada: falla cerrado, no a medias.
        self.assertEqual(self.db.todos("SELECT * FROM sesiones"), [])

    def test_un_intento_bloqueado_no_extiende_el_bloqueo(self):
        self.fallar()
        hasta = seguridad.bloqueo(self.db, "ana@test.cl")["hasta"]

        for _ in range(3):
            with self.assertRaises(auth.ErrorBloqueado):
                auth.autenticar(self.db, "ana@test.cl", CLAVE)

        self.assertEqual(seguridad.bloqueo(self.db, "ana@test.cl")["hasta"], hasta)
        anotados = self.db.todos("SELECT * FROM auditoria WHERE accion = 'login.bloqueado'")
        self.assertEqual(len(anotados), 3)   # los intentos bloqueados quedan registrados

    def test_se_destraba_solo_pasado_el_tiempo(self):
        self.fallar()
        ahora = dt.datetime.now(dt.timezone.utc)

        self.assertTrue(seguridad.bloqueo(self.db, "ana@test.cl", ahora=ahora)["bloqueada"])
        despues = ahora + dt.timedelta(minutes=seguridad.BLOQUEO_MINUTOS + 1)
        self.assertFalse(seguridad.bloqueo(self.db, "ana@test.cl", ahora=despues)["bloqueada"])

    def test_los_fallos_viejos_no_cuentan(self):
        self.fallar()
        viejo = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.db.ejecutar("UPDATE auditoria SET creado_en = ? WHERE accion = 'login.fallido'", (viejo,))

        self.assertFalse(seguridad.bloqueo(self.db, "ana@test.cl")["bloqueada"])

    def test_otra_cuenta_no_queda_bloqueada(self):
        self.fallar("ana@test.cl")

        self.assertFalse(seguridad.bloqueo(self.db, "socia@test.cl")["bloqueada"])
        self.assertIsNotNone(auth.autenticar(self.db, "socia@test.cl", CLAVE))

    def test_un_correo_parecido_no_se_pisa(self):
        # `_` es comodín en LIKE: sin escapar, el bloqueo de ana_perez alcanzaría a anaXperez.
        self._usuario("Ana Parecida", "ana_perez@test.cl", "abogado")
        self._usuario("Ana Comodin", "anaXperez@test.cl", "abogado")
        self.fallar("ana_perez@test.cl")

        self.assertTrue(seguridad.bloqueo(self.db, "ana_perez@test.cl")["bloqueada"])
        self.assertFalse(seguridad.bloqueo(self.db, "anaXperez@test.cl")["bloqueada"])

    def test_el_fallo_del_segundo_factor_tambien_cuenta(self):
        self.bloquear_por_2fa()
        self.assertTrue(seguridad.bloqueo(self.db, "ana@test.cl")["bloqueada"])

    def test_desbloquear_a_mano_lo_levanta(self):
        self.fallar()
        estado = auth.desbloquear(self.db, self.socio, "ana@test.cl")

        self.assertFalse(estado["bloqueada"])
        self.assertIsNotNone(auth.autenticar(self.db, "ana@test.cl", CLAVE))
        anotados = self.db.todos("SELECT * FROM auditoria WHERE accion = 'login.desbloqueado'")
        self.assertEqual(len(anotados), 1)
        self.assertEqual(anotados[0]["usuario_id"], self.socio["id"])   # quién lo destrabó

    def test_desbloquear_una_cuenta_inexistente_tambien_sirve(self):
        self.fallar("nadie@test.cl")
        self.assertTrue(seguridad.bloqueo(self.db, "nadie@test.cl")["bloqueada"])

        self.assertFalse(auth.desbloquear(self.db, self.socio, "nadie@test.cl")["bloqueada"])

    def test_desbloquear_exige_permiso(self):
        self.fallar()
        with self.assertRaises(auth.ErrorPermiso):
            auth.desbloquear(self.db, self.abogado, "ana@test.cl")
        self.assertTrue(seguridad.bloqueo(self.db, "ana@test.cl")["bloqueada"])

    def test_sin_correo_no_hay_bloqueo_que_calcular(self):
        self.assertFalse(seguridad.bloqueo(self.db, "")["bloqueada"])


if __name__ == "__main__":
    unittest.main()
