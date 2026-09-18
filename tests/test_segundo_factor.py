"""Pruebas del segundo factor (TOTP) y del registro de intentos fallidos.

Los vectores de la primera prueba son los del Apéndice B del RFC 6238: si el cálculo
estuviera mal, no coincidirían. Verificar contra el RFC es la única forma de saber que
esto sirve, porque un TOTP mal calculado se ve igual que uno bien calculado.

Solo librería estándar:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import base64
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, seguridad  # noqa: E402
from openlegal.db import DB  # noqa: E402

#: El secreto de los vectores del RFC: la cadena "12345678901234567890", en base32.
SECRETO_RFC = base64.b32encode(b"12345678901234567890").decode()

#: (segundos desde la época, código esperado de 8 dígitos), tal como están en el RFC.
VECTORES_RFC = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


class TestTOTPContraElRFC(unittest.TestCase):
    def test_los_vectores_del_rfc(self):
        for momento, esperado in VECTORES_RFC:
            with self.subTest(momento=momento):
                self.assertEqual(seguridad.codigo(SECRETO_RFC, momento, 8), esperado)

    def test_el_codigo_que_se_le_pide_a_una_persona_tiene_seis_digitos(self):
        codigo = seguridad.codigo(SECRETO_RFC, 1111111109)
        self.assertEqual(len(codigo), 6)
        self.assertTrue(codigo.isdigit())

    def test_el_secreto_nuevo_es_base32_de_20_bytes(self):
        secreto = seguridad.nuevo_secreto()
        self.assertEqual(len(base64.b32decode(secreto)), 20)
        self.assertNotEqual(secreto, seguridad.nuevo_secreto(), "dos secretos no pueden ser iguales")


class TestValidacionDeCodigos(unittest.TestCase):
    def setUp(self):
        self.secreto = seguridad.nuevo_secreto()
        self.momento = 1111111109.0

    def test_acepta_el_codigo_del_momento(self):
        codigo = seguridad.codigo(self.secreto, self.momento)
        self.assertTrue(seguridad.codigo_valido(self.secreto, codigo, self.momento))

    def test_acepta_el_anterior_y_el_siguiente(self):
        """Tolera el desfase de reloj del teléfono, pero sólo un período."""
        anterior = seguridad.codigo(self.secreto, self.momento - seguridad.PERIODO)
        siguiente = seguridad.codigo(self.secreto, self.momento + seguridad.PERIODO)
        self.assertTrue(seguridad.codigo_valido(self.secreto, anterior, self.momento))
        self.assertTrue(seguridad.codigo_valido(self.secreto, siguiente, self.momento))

    def test_rechaza_dos_periodos_atras_o_adelante(self):
        viejo = seguridad.codigo(self.secreto, self.momento - 2 * seguridad.PERIODO)
        futuro = seguridad.codigo(self.secreto, self.momento + 2 * seguridad.PERIODO)
        self.assertFalse(seguridad.codigo_valido(self.secreto, viejo, self.momento))
        self.assertFalse(seguridad.codigo_valido(self.secreto, futuro, self.momento))

    def test_rechaza_un_codigo_inventado(self):
        self.assertFalse(seguridad.codigo_valido(self.secreto, "000000", self.momento))

    def test_falla_cerrado_sin_secreto_o_sin_codigo(self):
        codigo = seguridad.codigo(self.secreto, self.momento)
        self.assertFalse(seguridad.codigo_valido(None, codigo, self.momento))
        self.assertFalse(seguridad.codigo_valido("", codigo, self.momento))
        self.assertFalse(seguridad.codigo_valido(self.secreto, None, self.momento))
        self.assertFalse(seguridad.codigo_valido(self.secreto, "", self.momento))

    def test_rechaza_algo_que_no_son_digitos(self):
        self.assertFalse(seguridad.codigo_valido(self.secreto, "abcdef", self.momento))

    def test_tolera_espacios(self):
        codigo = seguridad.codigo(self.secreto, self.momento)
        self.assertTrue(seguridad.codigo_valido(self.secreto, f" {codigo} ", self.momento))

    def test_la_uri_lleva_el_secreto_y_el_emisor(self):
        uri = seguridad.uri_otpauth(self.secreto, "socia@estudio.cl")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn(self.secreto, uri)
        self.assertIn("issuer=Open+Legal+Harness", uri)
        self.assertIn("socia%40estudio.cl", uri)


class BaseConEstudio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.db = DB(f"sqlite:///{self.raiz / 'test.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socia = self._usuario("Sofía Soto", "socia@test.cl", "socio")
        self.paralegal = self._usuario("Carla Díaz", "carla@test.cl", "paralegal")

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol, password="clave-segura") -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, password)
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila


class TestMigracionDelSegundoFactor(BaseConEstudio):
    def test_las_columnas_llegan_con_la_migracion(self):
        self.assertIn("totp_secret", self.db.columnas("usuarios"))
        self.assertIn("totp_activo", self.db.columnas("usuarios"))
        aplicadas = self.db.migraciones_aplicadas()
        self.assertIn(3, aplicadas)
        self.assertEqual(aplicadas[3]["nombre"], "segundo_factor")
        self.assertEqual(self.db.migraciones_pendientes(), [])


class TestEnrolamiento(BaseConEstudio):
    def test_enrolar_guarda_el_secreto_y_lo_audita(self):
        auth.crear_usuario(self.db, self.estudio, "Pedro Fin", "pedro@test.cl", "administrador", "clave")
        informe = auth.activar_segundo_factor(self.db, self.socia, "pedro@test.cl")

        fila = self.db.uno("SELECT totp_secret, totp_activo FROM usuarios WHERE email = ?", ("pedro@test.cl",))
        assert fila is not None
        self.assertEqual(fila["totp_secret"], informe["secreto"])
        self.assertEqual(fila["totp_activo"], 1)
        self.assertTrue(informe["uri"].startswith("otpauth://totp/"))

        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("usuario.2fa.activar",))
        self.assertEqual(len(entradas), 1)
        self.assertIn("socia@test.cl", entradas[0]["detalle"], "queda dicho quién lo enroló")

    def test_el_secreto_no_se_expone_al_consultar_el_estado(self):
        auth.activar_segundo_factor(self.db, self.socia, "socia@test.cl")
        estado = auth.estado_segundo_factor(self.db, self.socia)
        for fila in estado:
            self.assertNotIn("totp_secret", fila)
        propia = [f for f in estado if f["email"] == "socia@test.cl"][0]
        self.assertTrue(propia["totp_activo"])
        self.assertTrue(propia["esperado"], "un socio debería tener segundo factor")

    def test_enrolar_exige_poder_gestionar_usuarios(self):
        with self.assertRaises(auth.ErrorPermiso):
            auth.activar_segundo_factor(self.db, self.paralegal, "socia@test.cl")

    def test_apagar_exige_motivo(self):
        auth.activar_segundo_factor(self.db, self.socia, "socia@test.cl")
        with self.assertRaises(ValueError):
            auth.desactivar_segundo_factor(self.db, self.socia, "socia@test.cl", "   ")
        fila = self.db.uno("SELECT totp_activo FROM usuarios WHERE email = ?", ("socia@test.cl",))
        assert fila is not None
        self.assertEqual(fila["totp_activo"], 1, "sin motivo no se apaga")

    def test_apagar_deja_el_motivo_en_la_bitacora(self):
        auth.activar_segundo_factor(self.db, self.socia, "socia@test.cl")
        auth.desactivar_segundo_factor(self.db, self.socia, "socia@test.cl", "cambió de teléfono")
        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("usuario.2fa.desactivar",))
        self.assertEqual(len(entradas), 1)
        self.assertIn("cambió de teléfono", entradas[0]["detalle"])


class TestLoginConSegundoFactor(BaseConEstudio):
    def setUp(self):
        super().setUp()
        self.informe = auth.activar_segundo_factor(self.db, self.socia, "socia@test.cl")
        self.secreto = self.informe["secreto"]

    def _sesiones(self) -> int:
        return len(self.db.todos("SELECT * FROM sesiones"))

    def test_sin_codigo_no_entra_aunque_la_contrasena_este_bien(self):
        with self.assertRaises(auth.ErrorSegundoFactor):
            auth.autenticar(self.db, "socia@test.cl", "clave-segura")
        self.assertEqual(self._sesiones(), 0, "no puede quedar sesión abierta")
        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("login.2fa.fallido",))
        self.assertEqual(len(entradas), 1)
        self.assertIn("contraseña correcta", entradas[0]["detalle"])

    def test_con_el_codigo_correcto_entra(self):
        codigo = seguridad.codigo(self.secreto)
        usuario = auth.autenticar(self.db, "socia@test.cl", "clave-segura", codigo)
        assert usuario is not None
        self.assertEqual(usuario["email"], "socia@test.cl")
        self.assertIn("token", usuario)
        self.assertEqual(self._sesiones(), 1)

    def test_con_un_codigo_incorrecto_no_entra(self):
        with self.assertRaises(auth.ErrorSegundoFactor):
            auth.autenticar(self.db, "socia@test.cl", "clave-segura", "000000")
        self.assertEqual(self._sesiones(), 0)

    def test_sin_segundo_factor_activo_entra_como_siempre(self):
        usuario = auth.autenticar(self.db, "carla@test.cl", "clave-segura")
        assert usuario is not None
        self.assertEqual(usuario["email"], "carla@test.cl")

    def test_al_apagarlo_vuelve_a_entrar_sin_codigo(self):
        auth.desactivar_segundo_factor(self.db, self.socia, "socia@test.cl", "prueba")
        usuario = auth.autenticar(self.db, "socia@test.cl", "clave-segura")
        assert usuario is not None


class TestAccesosAnomalos(BaseConEstudio):
    def test_la_contrasena_mala_deja_rastro(self):
        for _ in range(3):
            self.assertIsNone(auth.autenticar(self.db, "socia@test.cl", "no-es-la-clave"))
        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("login.fallido",))
        self.assertEqual(len(entradas), 3)
        self.assertIn("contraseña incorrecta", entradas[0]["detalle"])

    def test_una_cuenta_que_no_existe_tambien_deja_rastro(self):
        self.assertIsNone(auth.autenticar(self.db, "nadie@test.cl", "lo-que-sea"))
        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("login.fallido",))
        self.assertEqual(len(entradas), 1)
        self.assertIn("nadie@test.cl", entradas[0]["detalle"])

    def test_el_informe_cuenta_por_cuenta_y_avisa_pasado_el_umbral(self):
        for _ in range(seguridad.UMBRAL_INTENTOS):
            auth.autenticar(self.db, "socia@test.cl", "no-es-la-clave")

        informe = seguridad.intentos_fallidos(self.db, minutos=15, estudio_id=self.estudio)

        self.assertEqual(informe["total"], seguridad.UMBRAL_INTENTOS)
        self.assertTrue(informe["sospechoso"])
        self.assertEqual(informe["por_cuenta"]["socia@test.cl"], seguridad.UMBRAL_INTENTOS)
        self.assertIsNotNone(informe["ultimo"])

    def test_sin_intentos_no_hay_alerta(self):
        informe = seguridad.intentos_fallidos(self.db, minutos=15, estudio_id=self.estudio)
        self.assertEqual(informe["total"], 0)
        self.assertFalse(informe["sospechoso"])

    def test_la_ventana_deja_fuera_lo_viejo(self):
        auth.autenticar(self.db, "socia@test.cl", "no-es-la-clave")
        self.db.ejecutar(
            "UPDATE auditoria SET creado_en = ? WHERE accion = ?",
            ("2020-01-01 00:00:00", "login.fallido"),
        )
        informe = seguridad.intentos_fallidos(self.db, minutos=15, estudio_id=self.estudio)
        self.assertEqual(informe["total"], 0)


if __name__ == "__main__":
    unittest.main()
