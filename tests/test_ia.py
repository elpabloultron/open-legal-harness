"""Pruebas del uso de IA con datos de causas: autorización, minimización y registro."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, ia, service  # noqa: E402
from openlegal.db import DB  # noqa: E402


class BaseIA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(f"sqlite:///{pathlib.Path(self.tmp.name) / 'ia.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio IA", modo="oficina")
        self.socio = self._usuario("Sofía Soto", "socia@ia.cl", "socio")
        self.abogado = self._usuario("Ana Pérez", "ana@ia.cl", "abogado")
        self.paralegal = self._usuario("Carla Díaz", "carla@ia.cl", "paralegal")
        self.cliente_id = service.crear_cliente(
            self.db, self.socio, "Constructora Andes SpA", "76.543.210-3",
            tipo_persona="juridica", representante_legal="Jorge Fuentes",
        )
        self.causa = service.crear_causa(
            self.db, self.socio, "Pérez con Andes SpA", cliente_id=self.cliente_id,
            contraparte="Andes SpA", materia="laboral",
        )
        service.asignar(self.db, self.socio, self.causa, self.abogado["id"], "responsable")

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, "clave-segura")
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        fila.pop("password_hash", None)
        return fila


class TestMinimizacion(BaseIA):
    def test_enmascara_todo_rut_aunque_el_digito_verificador_no_valide(self):
        # 76.543.210-3 es válido (módulo 11); 12.345.678-9 y 1.234.567-8 no lo son.
        # Un RUT inválido puede ser extranjero, inventado o mal escaneado: se enmascara
        # igual y se avisa. Dejar pasar datos personales por un dígito mal calculado
        # sería el peor fallo posible de un minimizador.
        texto = "El RUT del actor es 76.543.210-3 y el otro 12.345.678-9 y este 1.234.567-8."
        avisos: list[str] = []
        limpio, mapa = ia.redactar(texto, avisos=avisos)

        self.assertIn("[RUT·1]", limpio)
        self.assertIn("[RUT?·2]", limpio)
        self.assertIn("[RUT?·3]", limpio)
        self.assertNotIn("12.345.678-9", limpio, "un RUT con dígito verificador inválido también es dato personal")
        self.assertNotIn("1.234.567-8", limpio)
        self.assertEqual(mapa["[RUT·1]"], "76.543.210-3")
        self.assertEqual(mapa["[RUT?·2]"], "12.345.678-9")
        self.assertEqual(len(avisos), 2)
        self.assertIn("dígito verificador", avisos[0])

    def test_sin_avisos_no_estalla_ni_cambia_el_texto(self):
        limpio, mapa = ia.redactar("RUT 12.345.678-9")
        self.assertIn("[RUT?·1]", limpio)
        self.assertEqual(mapa["[RUT?·1]"], "12.345.678-9")

    def test_enmascara_los_nombres_tambien_en_caja_alta(self):
        # En el expediente los nombres van en mayúsculas; los términos vienen de la ficha
        # de la causa en caja normal. Comparar exacto dejaba el nombre a la vista.
        texto = "comparece MARÍA FERNANDA PÉREZ SOTO y también María Fernanda Pérez Soto"
        limpio, mapa = ia.redactar(texto, ["María Fernanda Pérez Soto"])
        self.assertNotIn("MARÍA FERNANDA PÉREZ SOTO", limpio)
        self.assertNotIn("María Fernanda Pérez Soto", limpio)
        self.assertEqual(limpio.count("[NOMBRE·1]"), 2, "todas las apariciones van al mismo marcador")
        self.assertEqual(mapa["[NOMBRE·1]"], "MARÍA FERNANDA PÉREZ SOTO")
        self.assertEqual(ia.reidentificar(limpio, mapa).count("[NOMBRE·1]"), 0)

    def test_un_rut_invalido_tambien_se_reidentifica(self):
        original = "Comparece 12.345.678-9 ante el tribunal"
        limpio, mapa = ia.redactar(original)
        self.assertNotIn("12.345.678-9", limpio)
        self.assertEqual(ia.reidentificar(limpio, mapa), original)

    def test_redacta_correo_telefono_y_nombres_declarados(self):
        texto = "Escribe a ana.perez@andes.cl o al +56 9 1234 5678. Constructora Andes SpA alega."
        limpio, mapa = ia.redactar(texto, ["Constructora Andes SpA"])
        self.assertNotIn("ana.perez@andes.cl", limpio)
        self.assertNotIn("+56 9 1234 5678", limpio)
        self.assertNotIn("Constructora Andes SpA", limpio)
        self.assertEqual(len(mapa), 3)

    def test_validador_de_rut_con_casos_conocidos(self):
        # cuerpos de 7 y 8 dígitos, y el caso del dígito verificador K
        for valido in ("76.543.210-3", "11.111.111-1", "12.345.678-5", "6.666.666-2", "99.999.999-9"):
            self.assertTrue(ia._rut_valido(valido), f"{valido} debería ser válido")
        for invalido in ("12.345.678-9", "1.234.567-8", "76.543.210-K", "6.666.666-6", "99.999.999-1"):
            self.assertFalse(ia._rut_valido(invalido), f"{invalido} no debería ser válido")

    def test_reidentifica_el_texto_en_el_estudio(self):
        original = "Constructora Andes SpA pagó a 76.543.210-3"
        limpio, mapa = ia.redactar(original, ["Constructora Andes SpA"])
        self.assertNotEqual(limpio, original)
        self.assertEqual(ia.reidentificar(limpio, mapa), original)

    def test_terminos_de_la_causa_juntan_cliente_contraparte_y_equipo(self):
        terminos = ia.terminos_de_causa(self.db, self.causa)
        self.assertIn("Constructora Andes SpA", terminos)
        self.assertIn("Andes SpA", terminos)
        self.assertIn("Ana Pérez", terminos)
        self.assertIn("Jorge Fuentes", terminos, "el representante legal también es dato personal")


class TestAutorizacionYRegistro(BaseIA):
    def test_sin_autorizacion_no_se_puede_enviar(self):
        with self.assertRaises(auth.ErrorPermiso) as contexto:
            service.registrar_transferencia(
                self.db, self.socio, self.causa, "openai", "texto del expediente"
            )
        self.assertIn("no tiene autorización vigente", str(contexto.exception))
        # el intento bloqueado queda registrado
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria")]
        self.assertIn("ia.sin_autorizacion", acciones)
        self.assertEqual(self.db.todos("SELECT * FROM transferencias_ia"), [])

    def test_flujo_completo_autorizar_redactar_registrar(self):
        autorizacion = service.autorizar_ia(
            self.db, self.socio, self.causa, alcance="analisis", titular="Representante de Andes SpA"
        )
        self.assertGreater(autorizacion, 0)
        estado = service.estado_ia(self.db, self.socio, self.causa)
        self.assertTrue(estado["autorizado"])
        self.assertEqual(estado["transferencias"], 0)

        expediente = "Demanda de 76.543.210-3 contra Constructora Andes SpA por despido."
        limpio, mapa = ia.redactar(expediente, estado["terminos_a_minimizar"])
        registro = service.registrar_transferencia(
            self.db, self.abogado, self.causa, "anthropic", limpio,
            modelo="claude-opus-4.7", documentos="demanda.pdf", redactado=True,
        )
        self.assertEqual(registro["proveedor"], "anthropic")
        self.assertEqual(registro["destino_pais"], "Estados Unidos")
        self.assertTrue(registro["redactado"])
        self.assertEqual(registro["hash_payload"], ia.hash_payload(limpio))
        self.assertNotIn("76.543.210-K", limpio)

        # se guarda el hash y el tamaño, no el contenido
        fila = self.db.uno("SELECT * FROM transferencias_ia WHERE id = ?", (registro["id"],))
        self.assertEqual(fila["caracteres"], len(limpio))
        self.assertNotIn("caracteres_payload", fila)

        # y queda firmado quién lo mandó y con qué autorización
        self.assertEqual(fila["usuario_id"], self.abogado["id"])
        self.assertEqual(fila["autorizacion_id"], autorizacion)
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria")]
        self.assertIn("ia.autorizar", acciones)
        self.assertIn("ia.comunicar", acciones)

    def test_revocar_bloquea_los_envios_siguientes(self):
        service.autorizar_ia(self.db, self.socio, self.causa)
        service.registrar_transferencia(self.db, self.socio, self.causa, "openai", "primero")
        self.assertEqual(service.revocar_ia(self.db, self.socio, self.causa), 1)
        self.assertFalse(service.estado_ia(self.db, self.socio, self.causa)["autorizado"])
        with self.assertRaises(auth.ErrorPermiso):
            service.registrar_transferencia(self.db, self.socio, self.causa, "openai", "segundo")

    def test_proveedor_desconocido_y_alcance_invalido(self):
        service.autorizar_ia(self.db, self.socio, self.causa)
        with self.assertRaises(ValueError):
            service.registrar_transferencia(self.db, self.socio, self.causa, "modelo-pirata", "texto")
        with self.assertRaises(ValueError):
            service.autorizar_ia(self.db, self.socio, self.causa, alcance="todo")

    def test_quien_no_puede_autorizar_ni_enviar(self):
        with self.assertRaises(auth.ErrorPermiso):
            service.autorizar_ia(self.db, self.paralegal, self.causa)
        with self.assertRaises(auth.ErrorPermiso):
            service.registrar_transferencia(self.db, self.paralegal, self.causa, "openai", "texto")

    def test_no_se_registra_una_causa_ajena(self):
        otro = self._usuario("Luis Rojas", "luis@ia.cl", "abogado")
        with self.assertRaises(auth.ErrorPermiso):
            service.autorizar_ia(self.db, otro, self.causa)
        with self.assertRaises(auth.ErrorPermiso):
            service.registrar_transferencia(self.db, otro, self.causa, "openai", "texto")

    def test_listado_de_transferencias(self):
        service.autorizar_ia(self.db, self.socio, self.causa)
        service.registrar_transferencia(self.db, self.socio, self.causa, "anthropic", "uno", modelo="x")
        filas = service.transferencias_ia(self.db, self.socio)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["caratula"], "Pérez con Andes SpA")


class TestMigracionDeBasesViejas(BaseIA):
    def test_agrega_columnas_nuevas_sin_perder_datos(self):
        """Una base creada antes del cambio tiene que actualizarse con `init`."""
        self.db.ejecutar("DROP TABLE clientes")
        self.db.ejecutar(
            "CREATE TABLE clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, estudio_id INTEGER, "
            "rut TEXT, nombre TEXT NOT NULL, tipo_persona TEXT DEFAULT 'natural', email TEXT, "
            "telefono TEXT, direccion TEXT, creado_en TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.ejecutar(
            "INSERT INTO clientes (estudio_id, nombre, tipo_persona) VALUES (?, ?, ?)",
            (self.estudio, "Cliente antiguo", "juridica"),
        )
        self.assertNotIn("representante_legal", self.db.columnas("clientes"))

        aplicadas = self.db.migrar()

        self.assertIn("ALTER clientes.representante_legal", aplicadas)
        self.assertIn("representante_legal", self.db.columnas("clientes"))
        fila = self.db.uno("SELECT nombre, representante_legal FROM clientes WHERE nombre = ?", ("Cliente antiguo",))
        self.assertEqual(fila["nombre"], "Cliente antiguo", "los datos existentes no se tocan")
        self.assertIsNone(fila["representante_legal"])
        # y correrlo de nuevo no hace nada
        self.assertNotIn("ALTER clientes.representante_legal", self.db.migrar())


class TestCatalogoDeProveedores(BaseIA):
    def test_deepseek_esta_marcado_como_pendiente(self):
        self.assertIsNone(
            ia.PROVEEDORES["deepseek"]["entrena_con_api"],
            "DeepSeek no está verificado: no debe afirmarse que no entrena",
        )

    def test_openai_y_anthropic_no_entrenan_con_datos_de_api(self):
        for proveedor in ("openai", "anthropic"):
            self.assertFalse(ia.PROVEEDORES[proveedor]["entrena_con_api"])

    def test_local_no_tiene_encargado(self):
        self.assertIn("no aplica", ia.PROVEEDORES["local"]["dpa"])


if __name__ == "__main__":
    unittest.main()
