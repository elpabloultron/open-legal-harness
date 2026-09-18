"""Pruebas de la retención: la política, el corte por último movimiento y la aplicación.

Solo librería estándar:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, retencion, service, titulares  # noqa: E402
from openlegal.db import DB  # noqa: E402

#: Un día bien pasado: lo que se cree viejo tiene que quedar antes que esto.
MUY_VIEJO = "2019-01-15 10:00:00"
HOY = dt.date(2026, 9, 18)


class BaseConExpediente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.db = DB(f"sqlite:///{self.raiz / 'test.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socia = self._usuario("Sofía Soto", "socia@test.cl", "socio")
        self.paralegal = self._usuario("Carla Díaz", "carla@test.cl", "paralegal")
        self.cliente_id = service.crear_cliente(self.db, self.socia, "Rosa Elena Muñoz", "11.111.111-1")
        self.causa_id = service.crear_causa(
            self.db, self.socia, "Muñoz con Banco del Sur", cliente_id=self.cliente_id,
            materia="civil", observaciones="Se reúne con Rosa Elena Muñoz por el mutuo.",
        )
        self.db.insertar("honorarios", {"causa_id": self.causa_id, "modalidad": "fijo", "monto_pactado": 500_000})
        self.db.insertar("gastos", {"causa_id": self.causa_id, "concepto": "Notaría", "monto": 80_000})
        self.db.insertar(
            "transferencias_ia", {"causa_id": self.causa_id, "proveedor": "deepseek", "hash_payload": "abc"}
        )

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, "clave-segura")
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def _envejecer(self) -> None:
        """Deja el expediente sin movimiento desde 2019, como si estuviera dormido."""
        for tabla in ("causas", "plazos", "audiencias", "documentos"):
            try:
                self.db.ejecutar(f"UPDATE {tabla} SET creado_en = ? WHERE causa_id = ?", (MUY_VIEJO, self.causa_id))
            except Exception:  # la tabla puede no tener filas
                pass
        self.db.ejecutar("UPDATE causas SET creado_en = ? WHERE id = ?", (MUY_VIEJO, self.causa_id))
        self.db.ejecutar(
            "UPDATE auditoria SET creado_en = ? WHERE entidad = 'causas' AND entidad_id = ?",
            (MUY_VIEJO, self.causa_id),
        )


class TestPolitica(BaseConExpediente):
    def test_sin_declarar_se_informan_las_sugerencias(self):
        politica = retencion.politica(self.db)
        self.assertIn("datos_de_persona", politica)
        self.assertEqual(politica["datos_de_persona"]["origen"], "sugerencia del sistema")
        self.assertTrue(politica["datos_de_persona"]["motivo"], "una sugerencia sin motivo no sirve")
        self.assertIn("2515", politica["datos_de_persona"]["motivo"], "el anclaje tiene que estar dicho")

    def test_declarar_el_plazo_lo_guarda_y_lo_audita(self):
        retencion.definir(self.db, self.socia, "datos_de_persona", 24, "criterio del estudio")

        politica = retencion.politica(self.db)
        self.assertEqual(politica["datos_de_persona"]["meses"], 24)
        self.assertEqual(politica["datos_de_persona"]["origen"], "declarada por el estudio")
        self.assertIsNotNone(politica["datos_de_persona"]["actualizado_en"])
        entradas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("retencion.definir",))
        self.assertEqual(len(entradas), 1)

    def test_declarar_exige_motivo_y_plazo_sensato(self):
        with self.assertRaises(retencion.ErrorRetencion):
            retencion.definir(self.db, self.socia, "datos_de_persona", 24, "   ")
        with self.assertRaises(retencion.ErrorRetencion):
            retencion.definir(self.db, self.socia, "datos_de_persona", 0, "cero no tiene sentido")
        with self.assertRaises(retencion.ErrorRetencion):
            retencion.definir(self.db, self.socia, "lo_que_sea", 24, "no existe ese tipo")

    def test_declarar_exige_permiso(self):
        with self.assertRaises(auth.ErrorPermiso):
            retencion.definir(self.db, self.paralegal, "datos_de_persona", 24, "porque sí")


class TestInforme(BaseConExpediente):
    def test_un_expediente_vivo_no_esta_cumplido(self):
        datos = retencion.informe(self.db, hoy=HOY)
        self.assertEqual(datos["total"], 0, "un expediente recién creado no puede estar cumplido")

    def test_un_expediente_dormido_aparece_cumplido(self):
        self._envejecer()
        datos = retencion.informe(self.db, hoy=HOY)
        self.assertEqual(datos["total"], 1)
        cumplido = datos["cumplidos"][0]
        self.assertEqual(cumplido["nombre"], "Rosa Elena Muñoz")
        self.assertEqual(cumplido["ultimo_movimiento"], "2019-01-15")
        self.assertGreater(cumplido["dias_sin_movimiento"], 365 * 5)
        self.assertIn("anonimizar", cumplido["se_haria"])

    def test_el_informe_dice_lo_que_conserva_y_por_que(self):
        datos = retencion.informe(self.db, hoy=HOY)
        conservado = {f["tabla"]: f for f in datos["conservado"]}
        for tabla in ("honorarios", "gastos", "auditoria", "transferencias_ia", "autorizaciones_ia"):
            self.assertIn(tabla, conservado)
            self.assertTrue(conservado[tabla]["motivo"].strip())
        self.assertEqual(conservado["honorarios"]["filas"], 1)

    def test_el_informe_avisa_de_que_el_corte_no_es_una_fecha_de_cierre(self):
        datos = retencion.informe(self.db, hoy=HOY)
        self.assertIn("último movimiento", datos["aviso"])
        self.assertIn("menciones indirectas", datos["aviso"])

    def test_el_plazo_declarado_manda_sobre_la_sugerencia(self):
        self._envejecer()  # sin movimiento desde 2019
        retencion.definir(self.db, self.socia, "datos_de_persona", 240, "20 años: no se borra nada")

        datos = retencion.informe(self.db, hoy=HOY)

        self.assertEqual(datos["total"], 0, "un plazo de 20 años no está cumplido en 2026")
        self.assertEqual(datos["corte_meses"], 240)


class TestAplicar(BaseConExpediente):
    def setUp(self):
        super().setUp()
        self._envejecer()

    def test_simular_no_escribe_nada(self):
        resultado = retencion.aplicar(self.db, self.socia, "retención cumplida", hoy=HOY, simular=True)

        self.assertTrue(resultado["simulado"])
        self.assertEqual(len(resultado["anonimizados"]), 1)
        self.assertFalse(resultado["anonimizados"][0]["hecho"])
        cliente = self.db.uno("SELECT nombre FROM clientes WHERE id = ?", (self.cliente_id,))
        assert cliente is not None
        self.assertEqual(cliente["nombre"], "Rosa Elena Muñoz", "simular no puede tocar los datos")
        self.assertEqual(len(self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("retencion.aplicar",))), 0)

    def test_aplicar_anonimiza_y_conserva_lo_que_corresponde(self):
        resultado = retencion.aplicar(self.db, self.socia, "retención cumplida", hoy=HOY, simular=False)

        self.assertFalse(resultado["simulado"])
        self.assertTrue(resultado["anonimizados"][0]["hecho"])
        cliente = self.db.uno("SELECT nombre, rut FROM clientes WHERE id = ?", (self.cliente_id,))
        assert cliente is not None
        self.assertTrue(cliente["nombre"].startswith(titulares.TOKEN))
        self.assertIsNone(cliente["rut"])
        causa = self.db.uno("SELECT observaciones FROM causas WHERE id = ?", (self.causa_id,))
        assert causa is not None
        self.assertNotIn("Rosa Elena Muñoz", causa["observaciones"])
        # Y lo que hay que conservar sigue ahí:
        self.assertEqual(len(self.db.todos("SELECT * FROM honorarios WHERE causa_id = ?", (self.causa_id,))), 1)
        self.assertEqual(len(self.db.todos("SELECT * FROM transferencias_ia WHERE causa_id = ?", (self.causa_id,))), 1)
        self.assertEqual(len(self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("retencion.aplicar",))), 1)

    def test_aplicar_exige_motivo(self):
        with self.assertRaises(retencion.ErrorRetencion):
            retencion.aplicar(self.db, self.socia, "  ", hoy=HOY, simular=False)
        cliente = self.db.uno("SELECT nombre FROM clientes WHERE id = ?", (self.cliente_id,))
        assert cliente is not None
        self.assertEqual(cliente["nombre"], "Rosa Elena Muñoz")

    def test_aplicar_exige_permiso(self):
        with self.assertRaises(auth.ErrorPermiso):
            retencion.aplicar(self.db, self.paralegal, "retención cumplida", hoy=HOY, simular=False)

    def test_sin_cumplidos_no_hace_nada(self):
        self.db.ejecutar("UPDATE causas SET creado_en = ? WHERE id = ?", (dt.datetime.now().isoformat(timespec="seconds"), self.causa_id))
        resultado = retencion.aplicar(self.db, self.socia, "retención cumplida", hoy=HOY, simular=False)
        self.assertEqual(resultado["anonimizados"], [])
        self.assertNotIn("auditado", resultado, "sin nada que hacer no se inventa una entrada de bitácora")


if __name__ == "__main__":
    unittest.main()
