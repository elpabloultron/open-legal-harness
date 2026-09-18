"""Pruebas de los derechos del titular: exportar (acceso y portabilidad) y anonimizar.

Solo libreria estandar:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, service, titulares  # noqa: E402
from openlegal.db import DB  # noqa: E402


class BaseConCaso(unittest.TestCase):
    """Un estudio con un cliente real de punta a punta: datos, textos y contabilidad."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.db = DB(f"sqlite:///{self.raiz / 'test.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socio = self._usuario("Sofia Soto", "socia@test.cl", "socio", "clave-segura")
        self.abogado = self._usuario("Ana Perez", "ana@test.cl", "abogado", "clave-segura")
        self.paralegal = self._usuario("Carla Diaz", "carla@test.cl", "paralegal", "clave-segura")

        self.cliente_id = service.crear_cliente(
            self.db, self.socio, "María José Fuenzalida", "11.111.111-1",
            email="mj.fuenzalida@ejemplo.cl", telefono="+56912345678", direccion="Av. Matta 1234, depto 8, Santiago",
        )
        self.causa_id = service.crear_causa(
            self.db, self.socio, "Fuenzalida con Banco del Sur", cliente_id=self.cliente_id,
            rol_rit="C-1234-2026", tribunal="1° Juzgado Civil de Santiago", materia="civil",
            observaciones="Se reúne con María José Fuenzalida para revisar el mutuo hipotecario.",
        )
        service.crear_plazo(
            self.db, self.socio, self.causa_id, "Contestar traslado notificado a María José Fuenzalida",
            dias=10, fecha_notificacion="2026-09-01",
        )
        service.crear_audiencia(self.db, self.socio, self.causa_id, "preparatoria", "2026-10-05", "09:00")
        self.db.ejecutar(
            "UPDATE audiencias SET minuta = ? WHERE causa_id = ?",
            ("Se cita a María José Fuenzalida con su cédula.", self.causa_id),
        )

        self.documento = self.raiz / "escritura.pdf"
        self.documento.write_bytes(b"%PDF-1.7 escritura de mutuo")
        self.db.insertar("documentos", {
            "causa_id": self.causa_id, "nombre": "Escritura de María José Fuenzalida",
            "ruta": str(self.documento), "tipo": "escritura", "subido_por": self.socio["id"],
        })
        self.db.insertar("honorarios", {
            "causa_id": self.causa_id, "modalidad": "fijo", "monto_pactado": 500_000, "monto_bruto": 500_000,
        })
        self.db.insertar("gastos", {"causa_id": self.causa_id, "concepto": "Notaría 45", "monto": 80_000})

        service.autorizar_ia(self.db, self.socio, self.causa_id, titular="María José Fuenzalida")
        service.registrar_transferencia(
            self.db, self.socio, self.causa_id, "deepseek", "texto del expediente", modelo="deepseek-flash",
        )

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol, password) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, password)
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def cliente(self) -> dict:
        fila = self.db.uno("SELECT * FROM clientes WHERE id = ?", (self.cliente_id,))
        assert fila is not None
        return fila

    def textos(self) -> str:
        """Todo el texto libre del expediente, junto, para buscar el nombre."""
        partes = []
        for tabla, campo in titulares.TEXTOS_LIBRES:
            for fila in self.db.todos(f"SELECT {campo} AS v FROM {tabla}"):
                partes.append(str(fila["v"] or ""))
        return " | ".join(partes)


class TestExportar(BaseConCaso):
    def test_reune_el_expediente_y_deja_el_hash(self):
        destino = self.raiz / "entrega.json"
        informe = titulares.exportar(self.db, self.socio, rut="11.111.111-1", destino=str(destino))

        self.assertTrue(destino.is_file())
        paquete = json.loads(destino.read_text(encoding="utf-8"))
        self.assertEqual(len(paquete["titular"]), 1)
        self.assertEqual(paquete["titular"][0]["nombre"], "María José Fuenzalida")
        self.assertEqual(len(paquete["causas"]), 1)
        causa = paquete["causas"][0]
        self.assertEqual(len(causa["plazos"]), 1)
        self.assertEqual(len(causa["audiencias"]), 1)
        self.assertEqual(len(causa["documentos"]), 1)
        self.assertEqual(len(causa["honorarios"]), 1)
        self.assertEqual(len(causa["gastos"]), 1)
        self.assertEqual(len(causa["transferencias_ia"]), 1)
        self.assertEqual(len(causa["autorizaciones_ia"]), 1)
        self.assertEqual(informe["causas"], 1)
        # El aviso tiene que estar: los PDF no viajan dentro del archivo.
        self.assertIn("NO van", paquete["aviso"])
        self.assertEqual(paquete["respaldos"][0]["existe"], True)
        self.assertEqual(paquete["respaldos"][0]["bytes"], len(self.documento.read_bytes()))

    def test_el_hash_corresponde_al_archivo_entregado(self):
        import hashlib

        destino = self.raiz / "entrega2.json"
        informe = titulares.exportar(self.db, self.socio, nombre="Fuenzalida", destino=str(destino))
        self.assertEqual(hashlib.sha256(destino.read_bytes()).hexdigest(), informe["sha256"])

    def test_encuentra_por_rut_con_o_sin_puntos(self):
        for rut in ("11.111.111-1", "11111111-1", "111111111"):
            with self.subTest(rut=rut):
                self.assertEqual(len(titulares._clientes(self.db, self.estudio, rut, None, None)), 1)

    def test_encuentra_por_nombre_sin_acentos(self):
        self.assertEqual(len(titulares._clientes(self.db, self.estudio, None, "Maria Jose", None)), 1)

    def test_exige_permiso(self):
        with self.assertRaises(auth.ErrorPermiso):
            titulares.exportar(self.db, self.paralegal, rut="11.111.111-1", destino=str(self.raiz / "x.json"))
        self.assertFalse((self.raiz / "x.json").exists())

    def test_sin_identificadores_avisa(self):
        with self.assertRaises(titulares.ErrorTitular):
            titulares.exportar(self.db, self.socio, destino=str(self.raiz / "y.json"))

    def test_deja_rastro_en_la_bitacora(self):
        titulares.exportar(self.db, self.socio, rut="11.111.111-1", destino=str(self.raiz / "z.json"))
        filas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("titular.exportar",))
        self.assertEqual(len(filas), 1)
        self.assertIn("sha256", filas[0]["detalle"])


class TestAnonimizar(BaseConCaso):
    def test_simulado_no_escribe_nada(self):
        informe = titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="solicitud del titular", simular=True)
        self.assertTrue(informe["simulado"])
        self.assertEqual(self.cliente()["nombre"], "María José Fuenzalida")
        self.assertIn("María José Fuenzalida", self.textos())
        self.assertGreaterEqual(len(informe["identificadores_borrados"]), 1)
        # Y no queda rastro de una operacion que no se hizo.
        self.assertEqual(len(self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("titular.anonimizar",))), 0)

    def test_borra_los_identificadores_directos(self):
        titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="solicitud del titular")
        cliente = self.cliente()
        self.assertTrue(cliente["nombre"].startswith(titulares.TOKEN))
        for campo in ("rut", "email", "telefono", "direccion"):
            self.assertIsNone(cliente[campo], f"{campo} quedó con valor")

    def test_redacta_el_nombre_en_los_textos(self):
        informe = titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="solicitud del titular")
        self.assertNotIn("María José Fuenzalida", self.textos())
        self.assertIn(titulares.TOKEN, self.textos())
        self.assertGreaterEqual(sum(t["reemplazos"] for t in informe["textos_redactados"]), 4)
        tablas = {t["tabla"] for t in informe["textos_redactados"]}
        self.assertIn("causas", tablas)
        self.assertIn("audiencias", tablas)
        self.assertIn("documentos", tablas)

    def test_conserva_contabilidad_y_pruebas_de_tratamiento(self):
        informe = titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="solicitud del titular")
        conservado = {c["tabla"]: c for c in informe["conservado"]}
        for tabla in ("honorarios", "gastos", "auditoria", "transferencias_ia", "autorizaciones_ia"):
            self.assertIn(tabla, conservado)
            self.assertTrue(conservado[tabla]["motivo"].strip(), f"{tabla} sin motivo escrito")
        # Y siguen ahí, con sus filas.
        self.assertEqual(len(self.db.todos("SELECT * FROM honorarios WHERE causa_id = ?", (self.causa_id,))), 1)
        self.assertEqual(len(self.db.todos("SELECT * FROM transferencias_ia WHERE causa_id = ?", (self.causa_id,))), 1)
        self.assertEqual(conservado["honorarios"]["filas"], 1)

    def test_no_borra_la_causa_ni_la_deja_sin_cliente(self):
        titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="solicitud del titular")
        causa = self.db.uno("SELECT * FROM causas WHERE id = ?", (self.causa_id,))
        assert causa is not None
        self.assertEqual(causa["cliente_id"], self.cliente_id)

    def test_exige_motivo(self):
        for vacio in ("", "   "):
            with self.subTest(motivo=vacio):
                with self.assertRaises(titulares.ErrorTitular):
                    titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo=vacio)
        self.assertEqual(self.cliente()["nombre"], "María José Fuenzalida")

    def test_exige_permiso(self):
        with self.assertRaises(auth.ErrorPermiso):
            titulares.anonimizar(self.db, self.paralegal, rut="11.111.111-1", motivo="solicitud del titular")
        self.assertEqual(self.cliente()["nombre"], "María José Fuenzalida")

    def test_deja_rastro_con_el_motivo(self):
        titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="art. 11: pide supresión")
        filas = self.db.todos("SELECT * FROM auditoria WHERE accion = ?", ("titular.anonimizar",))
        self.assertEqual(len(filas), 1)
        self.assertIn("art. 11: pide supresión", filas[0]["detalle"])

    def test_avisa_que_la_redaccion_es_por_coincidencia(self):
        informe = titulares.anonimizar(self.db, self.socio, rut="11.111.111-1", motivo="solicitud del titular")
        self.assertIn("no detecta iniciales", informe["aviso"])


class TestReemplazoDeNombre(unittest.TestCase):
    def test_exacto_e_ignore_mayusculas(self):
        nuevo, veces = titulares._reemplazar_nombre("Comparece doña MARÍA JOSÉ FUENZALIDA a fs. 3", "María José Fuenzalida")
        self.assertEqual(veces, 1)
        self.assertNotIn("MARÍA JOSÉ FUENZALIDA", nuevo)
        self.assertIn(titulares.TOKEN, nuevo)

    def test_sin_acentos_en_el_expediente(self):
        nuevo, veces = titulares._reemplazar_nombre("Se notifica a Maria Jose Fuenzalida del traslado", "María José Fuenzalida")
        self.assertEqual(veces, 1)
        self.assertNotIn("Maria Jose Fuenzalida", nuevo)

    def test_no_toca_otros_nombres(self):
        nuevo, veces = titulares._reemplazar_nombre("Comparece Camila Ignacia Riquelme", "María José Fuenzalida")
        self.assertEqual(veces, 0)
        self.assertEqual(nuevo, "Comparece Camila Ignacia Riquelme")

    def test_varias_apariciones(self):
        _, veces = titulares._reemplazar_nombre("María José Fuenzalida y otra vez María José Fuenzalida", "María José Fuenzalida")
        self.assertEqual(veces, 2)

    def test_nombre_vacio_no_rompe(self):
        self.assertEqual(titulares._reemplazar_nombre("texto", ""), ("texto", 0))


if __name__ == "__main__":
    unittest.main()
