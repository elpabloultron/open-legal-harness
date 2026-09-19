"""Pruebas del módulo de honorarios, gastos, pagos y cuenta de dividendos.

Solo libreria estandar:  python3 -m unittest discover -s tests

Lo que estas pruebas cuidan, además de la aritmética:

- que la retención **no** se invente: si el estudio no la declara, el líquido es el bruto y
  la cuenta lo advierte (el CRM no calcula la tasa ni la supone);
- que un pago no pueda imputarse al honorario de otra causa;
- que el alcance por causa y los permisos valgan igual que en el resto del CRM (un abogado
  lee los honorarios de sus causas pero no los registra; un paralegal no los ve);
- que la cuenta imprimible salga completa, sin nada externo y con el saldo.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, honorarios, service  # noqa: E402
from openlegal.db import DB  # noqa: E402

# La suite del núcleo corre sin los extras de la interfaz: si falta FastAPI, las pruebas de
# la API se saltean en vez de romper el resto.
try:
    from starlette.testclient import TestClient

    from openlegal.web import crear_app
except ImportError:  # pragma: no cover - depende del entorno
    crear_app = None          # type: ignore[assignment]
    TestClient = None         # type: ignore[assignment]

SIN_INTERFAZ = "falta el extra de la interfaz (pip install -e '.[ui]')"

CLAVE = "clave-segura"


class BaseHonorarios(unittest.TestCase):
    """Un estudio con socio, abogado, paralegal, secretaría, cliente y dos causas."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.url = f"sqlite:///{self.raiz / 'test.db'}"
        self.db = DB(self.url)
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Muñoz", rut="76.111.222-3", modo="oficina")
        self.socio = self._usuario("Sofía Soto", "socia@test.cl", "socio")
        self.abogado = self._usuario("Ana Pérez", "ana@test.cl", "abogado")
        self.paralegal = self._usuario("Carla Díaz", "carla@test.cl", "paralegal")
        self.finanzas = self._usuario("Pedro Fin", "pedro@test.cl", "administrativo")
        self.cliente_id = service.crear_cliente(
            self.db, self.socio, "Lucía Herrera", "11.111.111-1", direccion="Providencia 1234"
        )
        self.causa = service.crear_causa(
            self.db, self.socio, "Herrera con Fondo del Norte", cliente_id=self.cliente_id,
            rol_rit="C-1234-2026", tribunal="1° Juzgado Civil de Santiago",
        )
        service.asignar(self.db, self.socio, self.causa, self.abogado["id"], "responsable")
        service.asignar(self.db, self.socio, self.causa, self.paralegal["id"], "apoyo")
        # Una causa del mismo cliente que el abogado NO lleva: sirve para probar el alcance.
        self.causa_ajena = service.crear_causa(
            self.db, self.socio, "Herrera con Otro Banco", cliente_id=self.cliente_id
        )

    def tearDown(self):
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre: str, email: str, rol: str) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, CLAVE)
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def texto_bitacora(self, accion: str) -> list[str]:
        return [
            f"{f['accion']}: {f['detalle']}"
            for f in self.db.todos("SELECT * FROM auditoria WHERE accion = ? ORDER BY id", (accion,))
        ]


class TestAltaDeLosTres(BaseHonorarios):
    """Honorario, gasto y pago: el alta de cada uno, con su bitácora."""

    def test_registrar_honorario_calcula_el_liquido_con_la_retencion_declarada(self):
        honorario_id = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "fijo", monto_pactado=350000,
            descripcion="Demanda civil, primera instancia", fecha="2026-09-15",
            monto_bruto=350000, retencion_sii=35000,
        )

        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
        assert fila is not None
        self.assertEqual(fila["monto_pactado"], 350000)
        self.assertEqual(fila["monto_bruto"], 350000)
        self.assertEqual(fila["retencion_sii"], 35000)
        self.assertEqual(fila["monto_liquido"], 315000)     # 350.000 - 35.000
        self.assertEqual(fila["monto_pagado"], 0)
        self.assertEqual(fila["estado_pago"], "pendiente")
        self.assertEqual(fila["fecha"], "2026-09-15")
        self.assertEqual(fila["descripcion"], "Demanda civil, primera instancia")
        # La bitácora dice de dónde salió el líquido: sin eso no se puede explicar después.
        creado = self.texto_bitacora("honorario.crear")
        self.assertEqual(len(creado), 1)
        self.assertIn("retencion=35000", creado[0])
        self.assertIn("liquido=315000", creado[0])

    def test_sin_retencion_declarada_el_liquido_es_el_bruto(self):
        """El CRM no calcula la tasa: si no viene, el líquido es el bruto y se advierte."""
        honorario_id = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "hora", monto_pactado=120000, monto_bruto=120000
        )
        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
        assert fila is not None
        self.assertEqual(fila["monto_liquido"], 120000)
        self.assertIsNone(fila["retencion_sii"])
        self.assertIn("retencion=no declarada", self.texto_bitacora("honorario.crear")[0])

        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        self.assertTrue(
            any("no se declaró retención" in a for a in datos["advertencias"]),
            datos["advertencias"],
        )
        # La advertencia nombra el honorario que quedó sin declarar.
        self.assertTrue(any(str(honorario_id) in a for a in datos["advertencias"]), datos["advertencias"])

    def test_la_retencion_no_puede_superar_el_bruto(self):
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_honorario(
                self.db, self.socio, self.causa, "fijo", monto_bruto=100000, retencion_sii=150000
            )
        self.assertIn("mayor que el bruto", str(exc.exception))
        self.assertEqual(self.db.todos("SELECT * FROM honorarios"), [])

    def test_la_retencion_necesita_el_bruto_del_que_sale(self):
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_honorario(
                self.db, self.socio, self.causa, "fijo", monto_pactado=100000, retencion_sii=10000
            )
        self.assertIn("monto bruto", str(exc.exception))

    def test_modalidad_y_montos_invalidos_se_rechazan(self):
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_honorario(self.db, self.socio, self.causa, "por-las-ganancias")
        self.assertIn("modalidad inválida", str(exc.exception))
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_honorario(self.db, self.socio, self.causa, "fijo", monto_pactado=-1)
        self.assertIn("mayor o igual a 0", str(exc.exception))
        monto_escrito_mal: object = "350.000"
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_honorario(
                self.db, self.socio, self.causa, "fijo", monto_pactado=monto_escrito_mal  # type: ignore[arg-type]
            )
        self.assertIn("entero en CLP", str(exc.exception))
        self.assertEqual(self.db.todos("SELECT * FROM honorarios"), [], "nada se guardó a medias")

    def test_registrar_gasto_guarda_el_comprobante_y_su_bitacora(self):
        gasto_id = honorarios.registrar_gasto(
            self.db, self.socio, self.causa, "Notaría 45, autorización de firma", 25000,
            fecha="2026-09-16", comprobante="boleta 45",
        )
        fila = self.db.uno("SELECT * FROM gastos WHERE id = ?", (gasto_id,))
        assert fila is not None
        self.assertEqual(fila["concepto"], "Notaría 45, autorización de firma")
        self.assertEqual(fila["monto"], 25000)
        self.assertEqual(fila["comprobante"], "boleta 45")
        self.assertEqual(fila["pagado_por_estudio"], 1)
        self.assertEqual(fila["reembolsado"], 0)
        self.assertEqual(fila["fecha"], "2026-09-16")
        self.assertIn("Notaría 45", self.texto_bitacora("gasto.crear")[0])
        self.assertIn("boleta 45", self.texto_bitacora("gasto.crear")[0])

    def test_gasto_sin_concepto_ni_monto_no_se_guarda(self):
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_gasto(self.db, self.socio, self.causa, "   ", 25000)
        self.assertIn("concepto", str(exc.exception))
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_gasto(self.db, self.socio, self.causa, "Notaría", 0)
        self.assertIn("mayor o igual a 1", str(exc.exception))
        self.assertEqual(self.db.todos("SELECT * FROM gastos"), [])

    def test_gasto_que_pago_el_cliente_queda_registrado_pero_no_se_le_cuenta(self):
        gasto_id = honorarios.registrar_gasto(
            self.db, self.socio, self.causa, "Receptor", 10000, pagado_por_estudio=False
        )
        fila = self.db.uno("SELECT pagado_por_estudio FROM gastos WHERE id = ?", (gasto_id,))
        assert fila is not None
        self.assertEqual(fila["pagado_por_estudio"], 0)

        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        self.assertEqual(datos["totales"]["gastos"], 10000)
        self.assertEqual(datos["totales"]["gastos_por_cuenta_del_cliente"], 0)
        self.assertEqual(datos["totales"]["saldo"], 0)

    def test_registrar_pago_sin_imputar_queda_en_la_cuenta(self):
        pago_id = honorarios.registrar_pago(
            self.db, self.socio, self.causa, 200000, fecha="2026-09-18", medio="transferencia",
            referencia="transferencia 8812",
        )
        fila = self.db.uno("SELECT * FROM pagos WHERE id = ?", (pago_id,))
        assert fila is not None
        self.assertEqual(fila["monto"], 200000)
        self.assertEqual(fila["medio"], "transferencia")
        self.assertEqual(fila["referencia"], "transferencia 8812")
        self.assertEqual(fila["registrado_por"], self.socio["id"])
        self.assertIsNone(fila["honorario_id"])
        self.assertEqual(fila["fecha"], "2026-09-18")
        self.assertIn("transferencia 8812", self.texto_bitacora("pago.crear")[0])

    def test_pago_con_monto_o_medio_invalido_no_se_guarda(self):
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_pago(self.db, self.socio, self.causa, 0)
        self.assertIn("mayor o igual a 1", str(exc.exception))
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_pago(self.db, self.socio, self.causa, -5000)
        self.assertIn("mayor o igual a 1", str(exc.exception))
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_pago(self.db, self.socio, self.causa, 5000, medio="bitcoin")
        self.assertIn("medio de pago desconocido", str(exc.exception))
        self.assertEqual(self.db.todos("SELECT * FROM pagos"), [])


class TestPagosYEstadoDelHonorario(BaseHonorarios):
    """El estado lo mueven los pagos, no una suposición: parcial primero, pagado después."""

    def test_pago_que_deja_el_honorario_en_parcial_y_despues_en_pagado(self):
        honorario_id = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "fijo", monto_pactado=350000,
            monto_bruto=350000, retencion_sii=35000,
        )

        honorarios.registrar_pago(self.db, self.socio, self.causa, 200000, honorario_id=honorario_id)
        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
        assert fila is not None
        self.assertEqual(fila["monto_pagado"], 200000)
        self.assertEqual(fila["estado_pago"], "parcial")

        honorarios.registrar_pago(self.db, self.socio, self.causa, 115000, honorario_id=honorario_id)
        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
        assert fila is not None
        self.assertEqual(fila["monto_pagado"], 315000)
        self.assertEqual(fila["estado_pago"], "pagado")

        # Un pago de más no cambia «pagado» por otra cosa: el estado es sobre el honorario.
        honorarios.registrar_pago(self.db, self.socio, self.causa, 1000, honorario_id=honorario_id)
        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
        assert fila is not None
        self.assertEqual(fila["estado_pago"], "pagado")

    def test_pago_parcial_sobre_el_pactado_cuando_no_hay_liquido(self):
        """Sin líquido declarado la referencia es el pactado; si tampoco hay, no se afirma «pagado»."""
        con_pactado = honorarios.registrar_honorario(self.db, self.socio, self.causa, "hora", monto_pactado=100000)
        honorarios.registrar_pago(self.db, self.socio, self.causa, 100000, honorario_id=con_pactado)
        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (con_pactado,))
        assert fila is not None
        self.assertEqual(fila["estado_pago"], "pagado")

        sin_referencia = honorarios.registrar_honorario(self.db, self.socio, self.causa, "cuota_litis")
        honorarios.registrar_pago(self.db, self.socio, self.causa, 50000, honorario_id=sin_referencia)
        fila = self.db.uno("SELECT * FROM honorarios WHERE id = ?", (sin_referencia,))
        assert fila is not None
        self.assertEqual(fila["monto_pagado"], 50000)
        self.assertEqual(fila["estado_pago"], "parcial")

    def test_un_pago_no_puede_imputarse_al_honorario_de_otra_causa(self):
        de_esta_causa = honorarios.registrar_honorario(self.db, self.socio, self.causa, "fijo", monto_pactado=100000)

        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_pago(
                self.db, self.socio, self.causa_ajena, 50000, honorario_id=de_esta_causa
            )
        self.assertIn("no de la causa", str(exc.exception))
        # No quedó ni el pago ni una imputación cruzada.
        self.assertEqual(self.db.todos("SELECT * FROM pagos"), [])
        fila = self.db.uno("SELECT monto_pagado, estado_pago FROM honorarios WHERE id = ?", (de_esta_causa,))
        assert fila is not None
        self.assertEqual((fila["monto_pagado"], fila["estado_pago"]), (0, "pendiente"))

    def test_honorario_inexistente_se_rechaza_con_el_motivo(self):
        with self.assertRaises(ValueError) as exc:
            honorarios.registrar_pago(self.db, self.socio, self.causa, 50000, honorario_id=99)
        self.assertIn("no existe el honorario 99", str(exc.exception))
        self.assertEqual(self.db.todos("SELECT * FROM pagos"), [])


class TestCuentaDeDividendos(BaseHonorarios):
    """Los totales y el saldo, que es lo que el estudio le entrega al cliente."""

    def _armar(self) -> dict:
        honorario = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "fijo", monto_pactado=350000,
            descripcion="Demanda civil", monto_bruto=350000, retencion_sii=35000,
        )
        honorarios.registrar_gasto(self.db, self.socio, self.causa, "Notaría", 25000, comprobante="boleta 45")
        honorarios.registrar_gasto(
            self.db, self.socio, self.causa, "Receptor", 10000, pagado_por_estudio=False
        )
        honorarios.registrar_pago(
            self.db, self.socio, self.causa, 200000, honorario_id=honorario, referencia="transferencia 8812"
        )
        return {"honorario": honorario}

    def test_el_saldo_es_liquidos_mas_gastos_del_cliente_menos_pagos(self):
        self._armar()

        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        totales = datos["totales"]

        self.assertEqual(totales["honorarios_pactado"], 350000)
        self.assertEqual(totales["honorarios_liquido"], 315000)          # 350.000 - 35.000 de retención
        self.assertEqual(totales["gastos"], 35000)                       # los dos gastos
        self.assertEqual(totales["gastos_por_cuenta_del_cliente"], 25000)  # sólo el que pagó el estudio
        self.assertEqual(totales["pagos"], 200000)
        # 315.000 + 25.000 - 200.000
        self.assertEqual(totales["saldo"], 140000)

    def test_la_cuenta_trae_estudio_cliente_causa_y_el_detalle(self):
        self._armar()

        datos = honorarios.cuenta(self.db, self.socio, self.causa)

        self.assertEqual(datos["estudio"]["nombre"], "Estudio Muñoz")
        self.assertEqual(datos["estudio"]["rut"], "76.111.222-3")
        self.assertEqual(datos["cliente"]["nombre"], "Lucía Herrera")
        self.assertEqual(datos["cliente"]["rut"], "11.111.111-1")
        self.assertEqual(datos["cliente"]["direccion"], "Providencia 1234")
        self.assertEqual(datos["causa"]["caratula"], "Herrera con Fondo del Norte")
        self.assertEqual(datos["causa"]["rol_rit"], "C-1234-2026")
        self.assertEqual(datos["causa"]["tribunal"], "1° Juzgado Civil de Santiago")
        self.assertEqual(len(datos["honorarios"]), 1)
        self.assertEqual(len(datos["gastos"]), 2)
        self.assertEqual(len(datos["pagos"]), 1)
        # El pago dice quién lo registró: es lo que permite responder por él.
        self.assertEqual(datos["pagos"][0]["registrado_por_nombre"], "Sofía Soto")

    def test_la_cuenta_dice_que_no_es_un_documento_tributario(self):
        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        self.assertTrue(
            any("no es un documento tributario" in a for a in datos["advertencias"]),
            datos["advertencias"],
        )
        self.assertTrue(datos["advertencias"], "la cuenta nunca sale sin sus advertencias")

    def test_un_saldo_negativo_avisa_en_vez_de_quedar_callado(self):
        honorario = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "fijo", monto_pactado=100000, monto_bruto=100000
        )
        honorarios.registrar_pago(self.db, self.socio, self.causa, 150000, honorario_id=honorario)

        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        self.assertEqual(datos["totales"]["saldo"], -50000)
        self.assertTrue(any("a favor" in a for a in datos["advertencias"]), datos["advertencias"])
        # El saldo a favor se muestra con su signo, en la cuenta y en el HTML.
        documento = honorarios.html_cuenta(datos)
        self.assertIn("-$50.000", documento)
        self.assertIn("a favor del cliente", documento)

    def test_un_honorario_sin_liquido_no_entra_al_saldo_y_lo_advierte(self):
        """Sin bruto no hay líquido: sumarlo como si fuera el pactado sería inventar."""
        honorario = honorarios.registrar_honorario(self.db, self.socio, self.causa, "fijo", monto_pactado=100000)

        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        self.assertEqual(datos["totales"]["honorarios_pactado"], 100000)
        self.assertEqual(datos["totales"]["honorarios_liquido"], 0)
        self.assertEqual(datos["totales"]["saldo"], 0)
        self.assertTrue(
            any("no tienen monto líquido declarado" in a and str(honorario) in a for a in datos["advertencias"]),
            datos["advertencias"],
        )

    def test_pago_sin_imputar_mientras_hay_honorario_con_saldo_avisa(self):
        honorario = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "fijo", monto_pactado=100000, monto_bruto=100000
        )
        honorarios.registrar_pago(self.db, self.socio, self.causa, 30000)   # sin imputar

        datos = honorarios.cuenta(self.db, self.socio, self.causa)
        self.assertTrue(any("sin imputar" in a for a in datos["advertencias"]), datos["advertencias"])
        self.assertEqual(datos["totales"]["saldo"], 70000)
        fila = self.db.uno("SELECT monto_pagado FROM honorarios WHERE id = ?", (honorario,))
        assert fila is not None
        self.assertEqual(fila["monto_pagado"], 0, "un pago sin imputar no toca el honorario")

    def test_los_listados_respetan_el_alcance_por_causa(self):
        honorarios.registrar_honorario(self.db, self.socio, self.causa, "fijo", monto_pactado=350000)
        honorarios.registrar_gasto(self.db, self.socio, self.causa, "Notaría", 25000)
        honorarios.registrar_honorario(self.db, self.socio, self.causa_ajena, "fijo", monto_pactado=99000)

        del_socio = honorarios.listar_honorarios(self.db, self.socio)
        self.assertEqual(len(del_socio), 2, "el socio ve todas las causas del estudio")

        del_abogado = honorarios.listar_honorarios(self.db, self.abogado)
        self.assertEqual(len(del_abogado), 1, "el abogado sólo ve las causas que lleva")
        self.assertEqual(del_abogado[0]["caratula"], "Herrera con Fondo del Norte")

        self.assertEqual(len(honorarios.listar_gastos(self.db, self.abogado)), 1)
        # La causa que no lleva no está a su alcance ni pidiéndola por número.
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.listar_honorarios(self.db, self.abogado, self.causa_ajena)

    def test_la_cuenta_de_una_causa_ajena_se_deniega(self):
        with self.assertRaises(auth.ErrorPermiso) as exc:
            honorarios.cuenta(self.db, self.abogado, self.causa_ajena)
        self.assertIn("honorario.leer", str(exc.exception))


class TestPermisos(BaseHonorarios):
    """Registrar la plata es de finanzas; leerla, de quien lleva la causa."""

    def test_un_abogado_no_registra_honorarios_pero_si_los_lee(self):
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.registrar_honorario(self.db, self.abogado, self.causa, "fijo", monto_pactado=100000)
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.registrar_gasto(self.db, self.abogado, self.causa, "Notaría", 25000)
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.registrar_pago(self.db, self.abogado, self.causa, 50000)
        self.assertEqual(self.db.todos("SELECT * FROM honorarios"), [])
        self.assertEqual(self.db.todos("SELECT * FROM pagos"), [])

        # Leer sí puede: es su causa y necesita saber en qué va la plata.
        honorarios.registrar_honorario(self.db, self.socio, self.causa, "fijo", monto_pactado=100000)
        self.assertEqual(len(honorarios.listar_honorarios(self.db, self.abogado, self.causa)), 1)
        datos = honorarios.cuenta(self.db, self.abogado, self.causa)
        self.assertEqual(datos["totales"]["honorarios_pactado"], 100000)

    def test_un_paralegal_no_ve_honorarios_ni_pagos_pero_si_gastos(self):
        honorarios.registrar_honorario(self.db, self.socio, self.causa, "fijo", monto_pactado=100000)
        honorarios.registrar_gasto(self.db, self.socio, self.causa, "Notaría", 25000)
        honorarios.registrar_pago(self.db, self.socio, self.causa, 10000)

        with self.assertRaises(auth.ErrorPermiso):
            honorarios.listar_honorarios(self.db, self.paralegal, self.causa)
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.cuenta(self.db, self.paralegal, self.causa)
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.listar_pagos(self.db, self.paralegal, self.causa)
        # Los gastos sí: su rol los carga y los consulta.
        self.assertEqual(len(honorarios.listar_gastos(self.db, self.paralegal, self.causa)), 1)

    def test_una_cliente_no_ve_lo_interno_de_su_causa(self):
        cliente_usuario_id = auth.crear_usuario(
            self.db, self.estudio, "Lucía Herrera", "lucia@clientes.cl", "cliente", CLAVE
        )
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (cliente_usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)

        self.assertFalse(auth.puede(self.db, fila, "honorario.leer", self.causa))
        with self.assertRaises(auth.ErrorPermiso):
            honorarios.cuenta(self.db, fila, self.causa)

    def test_la_secretaria_registra_y_el_socio_tambien(self):
        for usuario in (self.finanzas, self.socio):
            honorario_id = honorarios.registrar_honorario(
                self.db, usuario, self.causa, "fijo", monto_pactado=50000
            )
            self.assertGreater(honorario_id, 0)
            honorarios.registrar_gasto(self.db, usuario, self.causa, "Tasas", 5000)
            honorarios.registrar_pago(self.db, usuario, self.causa, 10000, honorario_id=honorario_id)
        self.assertEqual(len(self.db.todos("SELECT * FROM honorarios")), 2)
        self.assertEqual(len(self.db.todos("SELECT * FROM pagos")), 2)


class TestCuentaImprimible(BaseHonorarios):
    """El HTML que el estudio le entrega al cliente: completo, imprimible y sin nada externo."""

    def _datos(self) -> dict:
        honorario = honorarios.registrar_honorario(
            self.db, self.socio, self.causa, "fijo", monto_pactado=350000,
            descripcion="Demanda civil", monto_bruto=350000, retencion_sii=35000,
        )
        honorarios.registrar_gasto(self.db, self.socio, self.causa, "Notaría", 25000, comprobante="boleta 45")
        honorarios.registrar_pago(self.db, self.socio, self.causa, 200000, honorario_id=honorario)
        return honorarios.cuenta(self.db, self.socio, self.causa)

    def test_el_html_trae_el_estudio_el_cliente_y_el_saldo(self):
        datos = self._datos()
        documento = honorarios.html_cuenta(datos)

        self.assertIn("Estudio Muñoz", documento)
        self.assertIn("76.111.222-3", documento)
        self.assertIn("Lucía Herrera", documento)
        self.assertIn("Herrera con Fondo del Norte", documento)
        self.assertIn("C-1234-2026", documento)
        self.assertIn("Demanda civil", documento)
        self.assertIn("Notaría", documento)
        self.assertIn("boleta 45", documento)
        # El saldo (315.000 + 25.000 - 200.000) va escrito como se escribe acá.
        self.assertEqual(datos["totales"]["saldo"], 140000)
        self.assertIn("$140.000", documento)
        self.assertIn("$315.000", documento)

    def test_el_html_es_imprimible_y_no_depende_de_nada_externo(self):
        documento = honorarios.html_cuenta(self._datos())

        self.assertIn("<!DOCTYPE html>", documento)
        self.assertIn("@page", documento)
        self.assertIn("size: A4", documento)
        self.assertIn("@media print", documento)
        self.assertIn('<html lang="es-CL">', documento)
        self.assertNotIn("<script", documento.lower())
        self.assertNotIn("http://", documento)
        self.assertNotIn("https://", documento)
        self.assertNotIn("<img", documento.lower())
        self.assertNotIn("<link", documento.lower())
        # Y dice que no es un documento tributario, que es lo que no se puede omitir.
        self.assertIn("no es un documento tributario", documento)
        self.assertIn("saldo", documento.lower())

    def test_el_html_escapa_lo_que_cargo_la_persona(self):
        cliente_id = service.crear_cliente(self.db, self.socio, 'Fondo "El Roble" <b>SpA</b>')
        causa = service.crear_causa(self.db, self.socio, "Prueba con <script>alert(1)</script>", cliente_id=cliente_id)
        documento = honorarios.html_cuenta(honorarios.cuenta(self.db, self.socio, causa))

        self.assertNotIn("<script>alert(1)</script>", documento)
        self.assertIn("&lt;script&gt;", documento)
        self.assertNotIn("<b>SpA</b>", documento)

    def test_una_causa_sin_cliente_no_revienta(self):
        causa = service.crear_causa(self.db, self.socio, "Causa sin cliente")
        datos = honorarios.cuenta(self.db, self.socio, causa)
        self.assertIsNone(datos["cliente"])

        documento = honorarios.html_cuenta(datos)
        self.assertIn("sin cliente asignado", documento)
        self.assertIn("(sin registros)", documento)

    def test_la_cuenta_guarda_se_puede_escribir_como_archivo(self):
        ruta = self.raiz / "cuenta.html"
        ruta.write_text(honorarios.html_cuenta(self._datos()), encoding="utf-8")
        self.assertGreater(ruta.stat().st_size, 2000)
        self.assertIn("Cuenta de dividendos", ruta.read_text(encoding="utf-8"))


@unittest.skipIf(TestClient is None, SIN_INTERFAZ)
class TestApiDeLaCuenta(BaseHonorarios):
    """La API que usa el panel: cuenta (json y HTML), alta, y los permisos de siempre."""

    def setUp(self):
        super().setUp()
        self.app = crear_app(db_url=self.url, token="token-de-prueba")
        self.http = TestClient(self.app)
        respuesta = self.http.post("/api/login", json={"email": "socia@test.cl", "password": CLAVE})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)

    def tearDown(self):
        self.http.close()
        super().tearDown()

    def _entrar(self, email: str) -> TestClient:
        otro = TestClient(self.app)
        respuesta = otro.post("/api/login", json={"email": email, "password": CLAVE})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return otro

    def test_alta_y_lectura_de_la_cuenta_por_la_api(self):
        honorario = self.http.post("/api/honorarios", json={
            "causa_id": self.causa, "modalidad": "fijo", "monto_pactado": 350000,
            "descripcion": "Demanda civil", "monto_bruto": 350000, "retencion_sii": 35000,
        })
        self.assertEqual(honorario.status_code, 200, honorario.text)
        honorario_id = honorario.json()["id"]
        self.assertIsNone(honorario.json()["aviso"], "la retención vino declarada")

        gasto = self.http.post("/api/gastos", json={
            "causa_id": self.causa, "concepto": "Notaría", "monto": 25000, "comprobante": "boleta 45",
        })
        self.assertEqual(gasto.status_code, 200, gasto.text)

        pago = self.http.post("/api/pagos", json={
            "causa_id": self.causa, "monto": 200000, "honorario_id": honorario_id,
            "medio": "transferencia", "referencia": "8812",
        })
        self.assertEqual(pago.status_code, 200, pago.text)
        self.assertEqual(pago.json()["honorario"]["estado_pago"], "parcial")

        cuenta = self.http.get(f"/api/cuenta?causa={self.causa}")
        self.assertEqual(cuenta.status_code, 200, cuenta.text)
        self.assertEqual(cuenta.json()["totales"]["saldo"], 140000)

        imprimible = self.http.get(f"/api/cuenta?causa={self.causa}&formato=html")
        self.assertEqual(imprimible.status_code, 200)
        self.assertIn("text/html", imprimible.headers["content-type"])
        self.assertIn("Estudio Muñoz", imprimible.text)
        self.assertIn("Lucía Herrera", imprimible.text)
        self.assertIn("$140.000", imprimible.text)

    def test_sin_retencion_declarada_la_api_devuelve_el_aviso(self):
        respuesta = self.http.post("/api/honorarios", json={
            "causa_id": self.causa, "modalidad": "hora", "monto_bruto": 120000,
        })
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertIn("no se declaró retención", respuesta.json()["aviso"])

    def test_el_abogado_no_registra_por_la_api_pero_si_lee(self):
        self.http.post("/api/honorarios", json={"causa_id": self.causa, "modalidad": "fijo", "monto_pactado": 100000})
        abogado = self._entrar("ana@test.cl")
        try:
            prohibido = abogado.post("/api/honorarios", json={
                "causa_id": self.causa, "modalidad": "fijo", "monto_pactado": 999000,
            })
            self.assertEqual(prohibido.status_code, 403)
            self.assertEqual(len(self.db.todos("SELECT * FROM honorarios")), 1)
            self.assertEqual(abogado.get(f"/api/cuenta?causa={self.causa}").status_code, 200)
        finally:
            abogado.close()

    def test_el_paralegal_no_ve_la_cuenta(self):
        paralegal = self._entrar("carla@test.cl")
        try:
            self.assertEqual(paralegal.get(f"/api/cuenta?causa={self.causa}").status_code, 403)
        finally:
            paralegal.close()

    def test_sin_sesion_no_hay_cuenta(self):
        limpio = TestClient(self.app)
        try:
            self.assertEqual(limpio.get(f"/api/cuenta?causa={self.causa}").status_code, 401)
        finally:
            limpio.close()


if __name__ == "__main__":
    unittest.main()
