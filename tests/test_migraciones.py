"""Pruebas de las migraciones versionadas y del hash de integridad de documentos.

Solo libreria estandar:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, service  # noqa: E402
from openlegal.db import DB, _migracion_1_esquema_base  # noqa: E402


class BaseMigrable(unittest.TestCase):
    """Una base recien creada, sin migrar: cada prueba decide cuando migrarla."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.url = f"sqlite:///{self.raiz / 'test.db'}"
        self.db = DB(self.url)

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, "clave-segura")
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def _estudio_con_equipo(self) -> None:
        """Migra y deja un estudio con socio, administrativo, cliente y causa."""
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socio = self._usuario("Sofía Soto", "socia@test.cl", "socio")
        self.administrativo = self._usuario("Pedro Fin", "pedro@test.cl", "administrativo")
        self.cliente_id = service.crear_cliente(self.db, self.socio, "Rosa Elena Muñoz", "11.111.111-1")
        self.causa_id = service.crear_causa(
            self.db, self.socio, "Muñoz con Banco del Sur", cliente_id=self.cliente_id, materia="civil"
        )

    def _archivo(self, nombre: str, contenido: bytes) -> pathlib.Path:
        ruta = self.raiz / nombre
        ruta.write_bytes(contenido)
        return ruta


class TestMigracionesVersionadas(BaseMigrable):
    def test_base_nueva_aplica_todas_y_las_registra(self):
        aplicadas = self.db.migrar()

        self.assertIn("migración 1: esquema_base", aplicadas)
        self.assertIn("migración 2: documentos_integridad", aplicadas)
        self.assertEqual(self.db.migraciones_pendientes(), [], "no puede quedar nada pendiente")
        # Y queda el registro, con su fecha: eso es lo que antes no existía.
        aplicadas_en_bd = self.db.migraciones_aplicadas()
        self.assertEqual(sorted(aplicadas_en_bd), [1, 2])
        self.assertTrue(aplicadas_en_bd[1]["aplicada_en"])
        # La migración 2 trae las columnas del hash.
        self.assertIn("hash_sha256", self.db.columnas("documentos"))
        self.assertIn("bytes", self.db.columnas("documentos"))

    def test_correr_de_nuevo_no_hace_nada(self):
        self.db.migrar()
        self.assertEqual(self.db.migrar(), [], "una base al día no se vuelve a tocar")

    def test_base_anterior_al_registro_se_marca_y_sigue(self):
        """El caso real: una base que ya estaba en uso antes de que hubiera registro."""
        _migracion_1_esquema_base(self.db)          # el esquema, sin registro
        self.assertNotIn("hash_sha256", self.db.columnas("documentos"))
        self.assertNotIn("migraciones", self.db.tablas())

        aplicadas = self.db.migrar()

        self.assertIn("marcada 1 (la base ya existía)", aplicadas)
        self.assertIn("migración 2: documentos_integridad", aplicadas)
        nombre_uno = self.db.migraciones_aplicadas()[1]["nombre"]
        self.assertIn("base anterior", nombre_uno)
        # Y no se volvió a correr el esquema base: sólo entró lo que faltaba.
        self.assertNotIn("migración 1: esquema_base", aplicadas)

    def test_migrar_no_toca_los_datos(self):
        self._estudio_con_equipo()
        self.db.ejecutar("DROP TABLE migraciones")   # como si nunca hubiera habido registro

        self.db.migrar()

        cliente = self.db.uno("SELECT nombre, rut FROM clientes WHERE id = ?", (self.cliente_id,))
        assert cliente is not None
        self.assertEqual(cliente["nombre"], "Rosa Elena Muñoz")
        self.assertEqual(cliente["rut"], "11.111.111-1")
        causa = self.db.uno("SELECT caratula FROM causas WHERE id = ?", (self.causa_id,))
        assert causa is not None
        self.assertEqual(causa["caratula"], "Muñoz con Banco del Sur")


class TestIntegridadDeDocumentos(BaseMigrable):
    def setUp(self):
        super().setUp()
        self._estudio_con_equipo()

    def test_registrar_guarda_el_hash_y_el_tamano(self):
        ruta = self._archivo("escritura.pdf", b"%PDF-1.7 escritura de mutuo")
        documento_id = service.registrar_documento(
            self.db, self.socio, self.causa_id, "Escritura de mutuo", ruta=str(ruta), tipo="escritura"
        )

        fila = self.db.uno("SELECT * FROM documentos WHERE id = ?", (documento_id,))
        assert fila is not None
        esperado = hashlib.sha256(ruta.read_bytes()).hexdigest()
        self.assertEqual(fila["hash_sha256"], esperado)
        self.assertEqual(fila["bytes"], len(ruta.read_bytes()))
        # Y queda dicho en la bitácora qué se incorporó.
        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("documento.registrar",))
        self.assertEqual(len(entradas), 1)
        self.assertIn(esperado, entradas[0]["detalle"])

    def test_verificar_dice_intacto_cuando_no_cambio(self):
        ruta = self._archivo("demanda.pdf", b"contenido original")
        documento_id = service.registrar_documento(self.db, self.socio, self.causa_id, "Demanda", ruta=str(ruta))

        informe = service.verificar_documento(self.db, self.socio, documento_id)

        self.assertEqual(informe["estado"], "intacto")
        self.assertEqual(informe["hash_registrado"], informe["hash_actual"])

    def test_verificar_detecta_que_el_archivo_cambio(self):
        ruta = self._archivo("contrato.pdf", b"contenido original")
        documento_id = service.registrar_documento(self.db, self.socio, self.causa_id, "Contrato", ruta=str(ruta))
        ruta.write_bytes(b"contenido cambiado despues de incorporarlo")

        informe = service.verificar_documento(self.db, self.socio, documento_id)

        self.assertEqual(informe["estado"], "cambio")
        self.assertNotEqual(informe["hash_registrado"], informe["hash_actual"])
        self.assertIn("cambió", informe["detalle"])
        # La verificación queda registrada: si alguien revisó, se sabe.
        entradas = self.db.todos(
            "SELECT * FROM auditoria WHERE accion = ?", ("documento.verificar.cambio",)
        )
        self.assertEqual(len(entradas), 1)

    def test_documento_sin_hash_lo_advierte_en_vez_de_inventar(self):
        ruta = self.raiz / "no-existe.pdf"
        documento_id = service.registrar_documento(
            self.db, self.socio, self.causa_id, "Escritura en papel", ruta=str(ruta)
        )
        fila = self.db.uno("SELECT hash_sha256 FROM documentos WHERE id = ?", (documento_id,))
        assert fila is not None
        self.assertIsNone(fila["hash_sha256"])

        informe = service.verificar_documento(self.db, self.socio, documento_id)

        self.assertEqual(informe["estado"], "sin_archivo")

    def test_archivo_que_desaparecio_despues(self):
        ruta = self._archivo("poder.pdf", b"poder notarial")
        documento_id = service.registrar_documento(self.db, self.socio, self.causa_id, "Poder", ruta=str(ruta))
        ruta.unlink()

        informe = service.verificar_documento(self.db, self.socio, documento_id)

        self.assertEqual(informe["estado"], "sin_archivo")
        self.assertIn("no está en esta máquina", informe["detalle"])

    def test_registrar_exige_permiso(self):
        ruta = self._archivo("privado.pdf", b"x")
        with self.assertRaises(auth.ErrorPermiso):
            # El rol administrativo factura pero no maneja documentos.
            service.registrar_documento(self.db, self.administrativo, self.causa_id, "Privado", ruta=str(ruta))
        self.assertEqual(len(self.db.todos("SELECT * FROM documentos")), 0)

    def test_verificar_documento_inexistente_avisa(self):
        with self.assertRaises(ValueError):
            service.verificar_documento(self.db, self.socio, 999)


if __name__ == "__main__":
    unittest.main()
