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

from openlegal import auth, ia, ia_proxy, service  # noqa: E402
from openlegal.db import (  # noqa: E402
    COLUMNAS_TRANSFERENCIAS,
    DB,
    MIGRACIONES,
    _causa_id_es_obligatoria,
    _migracion_1_esquema_base,
)
from openlegal.db import _migracion_7_envios_de_ia_de_cualquier_origen as _migracion_7  # noqa: E402


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
        # Y queda el registro, con su fecha: eso es lo que antes no existía. Se compara
        # contra el registro de migraciones, no contra una lista escrita a mano, para que
        # agregar una migración no obligue a tocar esta prueba.
        aplicadas_en_bd = self.db.migraciones_aplicadas()
        self.assertEqual(sorted(aplicadas_en_bd), [version for version, _, _ in MIGRACIONES])
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

    def test_la_migracion_6_trae_los_pagos_y_las_columnas_que_faltaban(self):
        self.db.migrar()

        self.assertIn("pagos", self.db.tablas())
        self.assertIn(6, self.db.migraciones_aplicadas())
        for columna in ("descripcion", "fecha"):
            self.assertIn(columna, self.db.columnas("honorarios"))
        self.assertIn("comprobante", self.db.columnas("gastos"))

    def test_una_base_sin_pagos_se_actualiza_sin_perder_nada(self):
        """El caso real: la base de un estudio que ya venía usándose antes de que hubiera pagos."""
        self._estudio_con_equipo()
        self.db.ejecutar("DROP TABLE pagos")
        self.db.ejecutar("DELETE FROM migraciones WHERE version = 6")

        aplicadas = self.db.migrar()

        self.assertIn("CREATE pagos", aplicadas)
        self.assertIn("pagos", self.db.tablas())
        self.assertNotIn(6, [version for version, _ in self.db.migraciones_pendientes()])
        # Los datos que ya estaban siguen ahí: una migración no toca expedientes.
        cliente = self.db.uno("SELECT nombre FROM clientes WHERE id = ?", (self.cliente_id,))
        assert cliente is not None
        self.assertEqual(cliente["nombre"], "Rosa Elena Muñoz")

    def test_migrar_otra_vez_no_repite_la_migracion_6(self):
        self.db.migrar()
        self.assertEqual(self.db.migrar(), [], "una base al día no se vuelve a tocar")


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


class TestEnviosDeIASinCausa(BaseMigrable):
    """La migración 7: el registro de IA deja de ser sólo del CRM.

    En SQLite, quitarle el NOT NULL a `causa_id` obliga a recrear la tabla. Eso es lo que
    estas pruebas cuidan: que la recreación no pierda ni una fila, que sea idempotente y que
    el esquema viejo (una base que ya venía en uso) se actualice sin tocar expedientes.
    """

    #: La tabla tal como era antes de la migración 7: `causa_id` obligatorio y sin las
    #: columnas del proxy. Se escribe acá a propósito, aunque se repita el esquema: es el
    #: único modo de probar de verdad la actualización de una base instalada.
    ESQUEMA_VIEJO = (
        "CREATE TABLE transferencias_ia ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " causa_id INTEGER NOT NULL REFERENCES causas(id) ON DELETE CASCADE,"
        " autorizacion_id INTEGER REFERENCES autorizaciones_ia(id) ON DELETE SET NULL,"
        " usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,"
        " proveedor TEXT NOT NULL,"
        " modelo TEXT,"
        " destino_pais TEXT,"
        " documentos TEXT,"
        " caracteres INTEGER NOT NULL DEFAULT 0,"
        " hash_payload TEXT NOT NULL,"
        " redactado INTEGER NOT NULL DEFAULT 0,"
        " creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )

    def test_base_nueva_permite_envios_sin_causa(self):
        self._estudio_con_equipo()

        for columna in ("origen", "via", "bloqueado", "motivo_bloqueo", "estudio_id"):
            self.assertIn(columna, self.db.columnas("transferencias_ia"))
        self.assertFalse(_causa_id_es_obligatoria(self.db), "un envío del harness puede no ser de una causa")

        enviado = ia_proxy.registrar(
            self.db, proveedor="deepseek", modelo="deepseek-chat", destino_pais="China",
            texto="consulta suelta, sin expediente", causa_id=None, estudio_id=self.estudio,
        )
        fila = self.db.uno("SELECT * FROM transferencias_ia WHERE id = ?", (enviado["id"],))
        assert fila is not None
        self.assertIsNone(fila["causa_id"])
        self.assertEqual(fila["origen"], "proxy")
        self.assertEqual(fila["via"], "openai-compat")
        self.assertEqual(fila["bloqueado"], 0)
        self.assertEqual(fila["estudio_id"], self.estudio)

    def test_una_base_vieja_se_actualiza_sin_perder_ninguna_fila(self):
        self._estudio_con_equipo()
        self.db.ejecutar("DROP TABLE transferencias_ia")
        self.db.ejecutar(self.ESQUEMA_VIEJO)
        self.db.ejecutar(
            "CREATE INDEX IF NOT EXISTS idx_transferencias_causa ON transferencias_ia (causa_id, creado_en)"
        )
        texto = "texto que ya había salido con el CRM"
        self.db.insertar(
            "transferencias_ia",
            {
                "causa_id": self.causa_id, "usuario_id": self.socio["id"], "proveedor": "anthropic",
                "modelo": "claude-opus-4.7", "destino_pais": "Estados Unidos",
                "caracteres": len(texto), "hash_payload": ia.hash_payload(texto), "redactado": 1,
            },
        )
        self.db.ejecutar("DELETE FROM migraciones WHERE version = 7")  # como si nunca hubiera corrido
        self.assertTrue(_causa_id_es_obligatoria(self.db))

        aplicadas = self.db.migrar()

        self.assertIn("transferencias_ia recreada: causa_id admite nulo", aplicadas)
        self.assertFalse(_causa_id_es_obligatoria(self.db))
        # La fila vieja sigue ahí, con sus metadatos exactos y ya atribuida a su estudio.
        filas = self.db.todos("SELECT * FROM transferencias_ia")
        self.assertEqual(len(filas), 1)
        fila = filas[0]
        self.assertEqual((fila["proveedor"], fila["modelo"], fila["destino_pais"]), ("anthropic", "claude-opus-4.7", "Estados Unidos"))
        self.assertEqual(fila["caracteres"], len(texto))
        self.assertEqual(fila["hash_payload"], ia.hash_payload(texto))
        self.assertEqual(fila["redactado"], 1)
        self.assertEqual(fila["causa_id"], self.causa_id)
        self.assertEqual(fila["origen"], "crm", "lo que ya estaba era del CRM")
        self.assertEqual(fila["estudio_id"], self.estudio)
        # El índice volvió con la tabla, y no quedó ninguna tabla a medio camino.
        indices = {f["name"] for f in self.db.todos(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'transferencias_ia'"
        )}
        self.assertIn("idx_transferencias_causa", indices)
        self.assertNotIn("transferencias_ia_nueva", self.db.tablas())
        # Y ahora la tabla admite un envío sin causa.
        transferencia_id = self.db.insertar(
            "transferencias_ia",
            {"causa_id": None, "proveedor": "local", "caracteres": 3, "hash_payload": ia.hash_payload("abc")},
        )
        self.assertTrue(transferencia_id)

    def test_la_recreacion_no_se_hace_dos_veces(self):
        self._estudio_con_equipo()
        self.db.ejecutar("DELETE FROM migraciones WHERE version = 7")
        self.db.migrar()
        self.assertEqual(self.db.migrar(), [], "una base al día no se vuelve a tocar")
        # Y con la migración 7 ya aplicada, volver a correrla no toca nada.
        self.assertEqual(_migracion_7(self.db), [])

    def test_migrar_no_toca_los_datos_de_los_expedientes(self):
        self._estudio_con_equipo()
        self.db.ejecutar("DELETE FROM migraciones WHERE version = 7")
        self.db.migrar()

        causa = self.db.uno("SELECT caratula FROM causas WHERE id = ?", (self.causa_id,))
        assert causa is not None
        self.assertEqual(causa["caratula"], "Muñoz con Banco del Sur")
        cliente = self.db.uno("SELECT nombre FROM clientes WHERE id = ?", (self.cliente_id,))
        assert cliente is not None
        self.assertEqual(cliente["nombre"], "Rosa Elena Muñoz")

    def test_las_columnas_que_se_copian_estan_todas(self):
        # Un `SELECT *` entre dos versiones de la misma tabla es cómo se pierden datos en
        # silencio: la lista de columnas de la recreación tiene que ser la tabla entera.
        self.db.migrar()
        self.assertEqual(set(COLUMNAS_TRANSFERENCIAS), self.db.columnas("transferencias_ia"))


if __name__ == "__main__":
    unittest.main()
