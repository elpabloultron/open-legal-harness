"""Pruebas de los avisos del estudio: cola, correo, SMS y recordatorios.

Solo libreria estandar:  python3 -m unittest discover -s tests

Lo que estas pruebas cuidan, en una frase cada cosa: que un aviso no se repita, que un
envío que falla no se pierda, que la clave del correo nunca salga a la luz, y que un plazo
por vencer avise a alguien aunque nadie se haya hecho responsable de él.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, notificaciones, service  # noqa: E402
from openlegal.db import DB, MIGRACIONES  # noqa: E402

HOY = dt.date(2026, 9, 18)


class BaseConEstudio(unittest.TestCase):
    """Un estudio de oficina con dos abogados, una causa y un aviso por configurar."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.db = DB(f"sqlite:///{self.raiz / 'test.db'}")
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio Test", modo="oficina")
        self.socio = self._usuario("Sofia Soto", "socia@test.cl", "socio")
        self.abogado = self._usuario("Ana Perez", "ana@test.cl", "abogado")
        self.paralegal = self._usuario("Carla Diaz", "carla@test.cl", "paralegal")
        self.cliente_usuario = self._usuario("Rosa Vera", "rosa@test.cl", "cliente")
        self.db.ejecutar("UPDATE usuarios SET telefono = ? WHERE id = ?", ("+56900000001", self.abogado["id"]))

        self.cliente_id = service.crear_cliente(
            self.db, self.socio, "Lucía Herrera", "11.111.111-1", email="lucia@ejemplo.cl",
        )
        self.causa_id = service.crear_causa(
            self.db, self.socio, "Herrera con Fondo del Norte", cliente_id=self.cliente_id,
            rol_rit="C-1234-2026", tribunal="1° Juzgado Civil de Santiago", materia="civil",
        )

        # Configuración y bitácora de avisos en rutas temporales: las pruebas nunca leen
        # (ni escriben) la configuración real del estudio.
        self.config_archivo = self.raiz / "notificaciones.json"
        self.avisos_log = self.raiz / "avisos.log"
        self.errores_log = self.raiz / "errores.log"
        self._previos = {
            k: os.environ.get(k) for k in ("OPENLEGAL_CONFIG", "OPENLEGAL_AVISOS_LOG", "OPENLEGAL_ERRORES_LOG")
        }
        os.environ["OPENLEGAL_CONFIG"] = str(self.config_archivo)
        os.environ["OPENLEGAL_AVISOS_LOG"] = str(self.avisos_log)
        os.environ["OPENLEGAL_ERRORES_LOG"] = str(self.errores_log)

    def tearDown(self):
        for clave, valor in self._previos.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        self.db.cerrar()
        self.tmp.cleanup()

    def _usuario(self, nombre, email, rol) -> dict:
        usuario_id = auth.crear_usuario(self.db, self.estudio, nombre, email, rol, "clave-segura")
        fila = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))
        assert fila is not None
        fila.pop("password_hash", None)
        return fila

    def _plazo(self, vence: dt.date, *, responsable: dict | None = None, fatal: bool = False) -> int:
        creado = service.crear_plazo(
            self.db, self.socio, self.causa_id, "Contestar traslado", dias=10, fecha_notificacion="2026-09-01",
            es_fatal=fatal, responsable_id=(responsable or self.abogado)["id"],
        )
        # La fecha se fija a mano: el cálculo del CRM es correcto, pero estas pruebas son
        # sobre los avisos, no sobre el calendario de feriados.
        self.db.ejecutar("UPDATE plazos SET fecha_vencimiento = ? WHERE id = ?", (vence.isoformat(), creado["id"]))
        self.limpiar_cola()   # el aviso de asignación no es lo que esta prueba mide
        return creado["id"]

    def _audiencia(self, fecha: dt.date, responsable: dict | None = None, limpiar: bool = True) -> int:
        audiencia_id = service.crear_audiencia(
            self.db, self.socio, self.causa_id, "preparatoria", fecha.isoformat(), "09:00",
            responsable_id=(responsable or self.abogado)["id"],
        )
        if limpiar:
            self.limpiar_cola()
        return audiencia_id

    def limpiar_cola(self) -> None:
        self.db.ejecutar("DELETE FROM notificaciones")

    def _encolar_de_prueba(self, canal: str, destino: str, clave: str, **extra) -> int | None:
        return notificaciones.encolar(
            self.db, self.estudio, canal, destino, "Cuerpo del aviso", clave=clave, asunto="Asunto", **extra,
        )

    def filas(self, sql: str = "SELECT * FROM notificaciones ORDER BY id") -> list[dict]:
        return self.db.todos(sql)


class TestMigracion(BaseConEstudio):
    def test_la_migracion_trae_la_cola_y_el_telefono(self):
        self.assertIn("notificaciones", self.db.tablas())
        self.assertIn("telefono", self.db.columnas("usuarios"))
        # Las migraciones se numeran y se aplican en orden: eso es lo que se fija acá, no
        # cuántas hay (agregar la 6 no puede romper esta prueba).
        versiones = [n for n, _, _ in MIGRACIONES]
        self.assertEqual(versiones, list(range(1, len(versiones) + 1)))
        self.assertIn(5, versiones)
        self.assertIn(5, self.db.migraciones_aplicadas())


class TestCola(BaseConEstudio):
    def test_encola_un_aviso_pendiente(self):
        identificador = self._encolar_de_prueba("email", "ana@test.cl", "prueba:1", usuario_id=self.abogado["id"])
        self.assertIsNotNone(identificador)

        fila = self.filas()[0]
        self.assertEqual(fila["estado"], "pendiente")
        self.assertEqual(fila["canal"], "email")
        self.assertEqual(fila["destino"], "ana@test.cl")
        self.assertEqual(fila["intentos"], 0)
        self.assertIsNone(fila["enviada_en"])

    def test_no_repite_la_misma_clave(self):
        self.assertIsNotNone(self._encolar_de_prueba("email", "ana@test.cl", "plazo:12:email:2026-09-18"))
        self.assertIsNone(self._encolar_de_prueba("email", "ana@test.cl", "plazo:12:email:2026-09-18"))
        self.assertEqual(len(self.filas()), 1)

    def test_rechaza_canal_desconocido(self):
        with self.assertRaises(ValueError):
            self._encolar_de_prueba("paloma", "ana@test.cl", "x:1")

    def test_rechaza_aviso_sin_destino(self):
        with self.assertRaises(ValueError):
            self._encolar_de_prueba("email", "", "x:2")

    def test_rechaza_aviso_sin_cuerpo(self):
        with self.assertRaises(ValueError):
            notificaciones.encolar(self.db, self.estudio, "email", "ana@test.cl", "   ", clave="x:3")

    def test_pendientes_no_trae_los_programados_para_despues(self):
        ayer = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        manana = (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        self._encolar_de_prueba("email", "ana@test.cl", "ya", programada_para=ayer)
        self._encolar_de_prueba("email", "ana@test.cl", "despues", programada_para=manana)

        listas = notificaciones.pendientes(self.db)
        self.assertEqual([f["clave"] for f in listas], ["ya"])


class TestDespacho(BaseConEstudio):
    def test_envia_por_consola_y_deja_registro(self):
        self._encolar_de_prueba("consola", "ana@test.cl", "c:1")
        resultado = notificaciones.enviar_pendientes(self.db, self.socio, config={})

        self.assertEqual(resultado["enviadas"], 1)
        self.assertEqual(resultado["fallidas"], 0)
        self.assertEqual(self.filas()[0]["estado"], "enviada")
        self.assertIsNotNone(self.filas()[0]["enviada_en"])
        self.assertIn("Cuerpo del aviso", self.avisos_log.read_text(encoding="utf-8"))

    def test_un_fallo_suma_intento_y_guarda_el_error(self):
        self._encolar_de_prueba("email", "ana@test.cl", "e:1")   # sin SMTP configurado
        resultado = notificaciones.enviar_pendientes(self.db, self.socio, config={})

        self.assertEqual(resultado["fallidas"], 1)
        fila = self.filas()[0]
        self.assertEqual(fila["estado"], "pendiente")       # sigue en la cola: no se pierde
        self.assertEqual(fila["intentos"], 1)
        self.assertIn("no está configurado", fila["ultimo_error"])
        self.assertEqual(resultado["errores"][0]["id"], fila["id"])

    def test_tras_los_intentos_maximos_queda_fallida(self):
        self._encolar_de_prueba("email", "ana@test.cl", "e:2")
        for _ in range(notificaciones.MAX_INTENTOS):
            notificaciones.enviar_pendientes(self.db, self.socio, config={})

        fila = self.filas()[0]
        self.assertEqual(fila["estado"], "fallida")
        self.assertEqual(fila["intentos"], notificaciones.MAX_INTENTOS)
        self.assertIsNotNone(fila["ultimo_error"])
        self.assertEqual(notificaciones.pendientes(self.db), [])   # ya no se reintenta

    def test_el_estado_resume_la_cola(self):
        self._encolar_de_prueba("consola", "ana@test.cl", "c:2")
        self._encolar_de_prueba("email", "ana@test.cl", "e:3")
        notificaciones.enviar_pendientes(self.db, self.socio, config={})

        datos = notificaciones.estado(self.db)
        self.assertEqual(datos["conteos"]["enviada"], 1)
        self.assertEqual(datos["conteos"]["pendiente"], 1)
        self.assertEqual(datos["ultimas_fallas"][0]["canal"], "email")

    def test_deja_rastro_en_la_bitacora(self):
        self._encolar_de_prueba("consola", "ana@test.cl", "c:3")
        notificaciones.enviar_pendientes(self.db, self.socio, config={})

        rastro = self.db.todos("SELECT * FROM auditoria WHERE accion = 'notificacion.despachar'")
        self.assertEqual(len(rastro), 1)
        self.assertIn("enviadas=1", rastro[0]["detalle"])

    def test_exige_permiso_al_despachar(self):
        self._encolar_de_prueba("consola", "ana@test.cl", "c:4")
        with self.assertRaises(auth.ErrorPermiso):
            notificaciones.enviar_pendientes(self.db, self.cliente_usuario, config={})
        self.assertEqual(self.filas()[0]["estado"], "pendiente")


class TestConfiguracion(BaseConEstudio):
    def test_el_resumen_no_muestra_la_clave(self):
        config = {
            "email": {"host": "smtp.estudio.cl", "puerto": 587, "usuario": "avisos@estudio.cl",
                      "clave": "clave-secretisima", "de": "Estudio <avisos@estudio.cl>"},
            "sms": {"proveedor": "twilio", "cuenta": "ACxxx", "token": "token-secretisimo", "de": "+56900000000"},
        }
        texto = json.dumps(notificaciones.resumen_config(config), ensure_ascii=False)
        self.assertNotIn("clave-secretisima", texto)
        self.assertNotIn("token-secretisimo", texto)
        self.assertIn("smtp.estudio.cl:587", texto)
        self.assertTrue(all(fila["listo"] for fila in notificaciones.resumen_config(config) if fila["canal"] in ("email", "sms")))

    def test_dice_qué_falta_si_no_hay_nada_configurado(self):
        filas = {fila["canal"]: fila for fila in notificaciones.resumen_config({})}
        self.assertFalse(filas["email"]["listo"])
        self.assertIn("host", filas["email"]["detalle"])
        self.assertFalse(filas["sms"]["listo"])
        self.assertTrue(filas["consola"]["listo"])

    def test_la_configuracion_sale_del_archivo(self):
        self.config_archivo.write_text(json.dumps({
            "email": {"host": "smtp.estudio.cl", "puerto": 2525, "usuario": "avisos@estudio.cl",
                      "clave": "x", "de": "avisos@estudio.cl"},
            "dias_de_aviso": 7,
        }), encoding="utf-8")

        config = notificaciones.cargar_config()
        self.assertEqual(config["email"]["host"], "smtp.estudio.cl")
        self.assertEqual(config["email"]["puerto"], 2525)
        self.assertEqual(config["dias_de_aviso"], 7)

    def test_la_variable_de_entorno_manda_sobre_el_archivo(self):
        self.config_archivo.write_text(json.dumps({"email": {"host": "viejo.cl", "puerto": 25}}), encoding="utf-8")
        os.environ["OPENLEGAL_SMTP_HOST"] = "nuevo.cl"
        self.addCleanup(os.environ.pop, "OPENLEGAL_SMTP_HOST", None)

        self.assertEqual(notificaciones.cargar_config()["email"]["host"], "nuevo.cl")

    def test_un_archivo_ilegible_se_avisa(self):
        self.config_archivo.write_text("{no es json", encoding="utf-8")
        with self.assertRaises(ValueError):
            notificaciones.cargar_config()


class TestTransportes(BaseConEstudio):
    def test_arma_el_correo_con_remitente_y_asunto(self):
        config = {"email": {"de": "Estudio <avisos@estudio.cl>"}}
        mensaje = notificaciones.armar_correo(config, "ana@test.cl", "Vence mañana", "Contestar traslado")

        self.assertEqual(mensaje["To"], "ana@test.cl")
        self.assertEqual(mensaje["From"], "Estudio <avisos@estudio.cl>")
        self.assertEqual(mensaje["Subject"], "Vence mañana")
        self.assertIn("Contestar traslado", mensaje.get_content())

    def test_la_peticion_de_twilio_lleva_el_token_en_la_cabecera_y_no_en_la_url(self):
        config = {"sms": {"proveedor": "twilio", "cuenta": "AC123", "token": "secreto", "de": "+56900000000"}}
        url, cabeceras, datos = notificaciones.armar_peticion_sms(config, "+56911111111", "Vence un plazo")

        self.assertIn("AC123", url)
        self.assertNotIn("secreto", url)
        esperado = base64.b64encode(b"AC123:secreto").decode()
        self.assertEqual(cabeceras["Authorization"], f"Basic {esperado}")
        self.assertIn(b"To=%2B56911111111", datos)
        self.assertIn(b"Body=Vence+un+plazo", datos)

    def test_la_peticion_del_webhook_va_en_json_y_con_su_token(self):
        config = {"sms": {"proveedor": "webhook", "webhook": "https://sms.ejemplo.cl/enviar", "token": "abc", "de": "Estudio"}}
        url, cabeceras, datos = notificaciones.armar_peticion_sms(config, "+56911111111", "Vence un plazo")

        self.assertEqual(url, "https://sms.ejemplo.cl/enviar")
        self.assertEqual(cabeceras["Authorization"], "Bearer abc")
        self.assertEqual(json.loads(datos)["to"], "+56911111111")

    def test_un_proveedor_desconocido_no_se_inventa(self):
        with self.assertRaises(ValueError):
            notificaciones.armar_peticion_sms({"sms": {"proveedor": "carrier-paloma"}}, "+56911111111", "x")

    def test_el_sms_de_prueba_no_sale_a_la_red_y_queda_en_el_archivo(self):
        self._encolar_de_prueba("sms", "+56900000001", "s:1")
        resultado = notificaciones.enviar_pendientes(
            self.db, self.socio, config={"sms": {"proveedor": "consola"}}
        )

        self.assertEqual(resultado["enviadas"], 1)
        self.assertIn("Cuerpo del aviso", self.avisos_log.read_text(encoding="utf-8"))

    def test_el_sms_va_corto_y_el_correo_completo(self):
        completo = (
            "FATAL · vence en 2 día(s): Presentar escrito\n\nCausa: Herrera con Fondo del Norte\n"
            "Vencimiento: 2026-09-20\nEstado: pendiente\n\n" + "detalle largo " * 40
        )
        correo = notificaciones.cuerpo_del_canal("email", "FATAL · vence en 2 día(s): Presentar escrito", completo)
        sms = notificaciones.cuerpo_del_canal("sms", "FATAL · vence en 2 día(s): Presentar escrito", completo)

        self.assertEqual(correo, completo)          # el correo lleva todo
        self.assertLessEqual(len(sms), notificaciones.LIMITE_SMS)
        self.assertLess(len(sms), len(completo))
        self.assertTrue(sms.startswith("FATAL"))
        self.assertIn("Causa: Herrera", sms)

    def test_el_correo_cifra_por_defecto(self):
        visitas = []

        class SMTPSimulado:
            def __init__(self, host, puerto, timeout=None):
                visitas.append(("conectar", host, puerto))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                visitas.append(("ehlo",))

            def starttls(self):
                visitas.append(("starttls",))

            def login(self, usuario, clave):
                visitas.append(("login", usuario))

            def send_message(self, mensaje):
                visitas.append(("enviar", mensaje["To"]))

        original = notificaciones.smtplib.SMTP
        notificaciones.smtplib.SMTP = SMTPSimulado
        self.addCleanup(setattr, notificaciones.smtplib, "SMTP", original)

        config = {"email": {"host": "smtp.estudio.cl", "puerto": 587, "usuario": "avisos@estudio.cl",
                            "clave": "secreta", "de": "avisos@estudio.cl"}}
        notificaciones._enviar_correo(config, "ana@test.cl", "Asunto", "Cuerpo")

        self.assertIn(("starttls",), visitas)
        self.assertIn(("enviar", "ana@test.cl"), visitas)

    def test_el_relay_local_puede_ir_sin_cifrado(self):
        visitas = []

        class SMTPSimulado:
            def __init__(self, host, puerto, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                pass

            def starttls(self):
                visitas.append("starttls")

            def login(self, usuario, clave):
                pass

            def send_message(self, mensaje):
                visitas.append("enviar")

        original = notificaciones.smtplib.SMTP
        notificaciones.smtplib.SMTP = SMTPSimulado
        self.addCleanup(setattr, notificaciones.smtplib, "SMTP", original)

        config = {"email": {"host": "127.0.0.1", "puerto": 25, "usuario": "avisos", "clave": "x",
                            "de": "avisos@estudio.cl", "seguridad": "ninguna"}}
        notificaciones._enviar_correo(config, "ana@test.cl", "Asunto", "Cuerpo")

        self.assertEqual(visitas, ["enviar"])


class TestRecordatorios(BaseConEstudio):
    def test_encola_el_plazo_por_vencer_al_responsable(self):
        plazo_id = self._plazo(HOY + dt.timedelta(days=2), responsable=self.abogado)
        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        self.assertEqual(informe["plazos_revisados"], 1)
        self.assertEqual(informe["encoladas"], 1)
        fila = self.filas()[0]
        self.assertEqual(fila["canal"], "email")
        self.assertEqual(fila["destino"], "ana@test.cl")
        self.assertEqual(fila["estado"], "pendiente")
        self.assertIn("vence en 2 día(s)", fila["asunto"])
        self.assertEqual(fila["usuario_id"], self.abogado["id"])
        self.assertEqual(fila["plazo_id"], plazo_id)
        self.assertEqual(fila["causa_id"], self.causa_id)
        self.assertIn("Herrera con Fondo del Norte", fila["cuerpo"])

    def test_no_avisa_del_plazo_lejano(self):
        self._plazo(HOY + dt.timedelta(days=30))
        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        self.assertEqual(informe["plazos_revisados"], 0)
        self.assertEqual(self.filas(), [])

    def test_no_repite_el_aviso_del_mismo_dia(self):
        self._plazo(HOY + dt.timedelta(days=2))
        primera = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)
        segunda = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        self.assertEqual(primera["encoladas"], 1)
        self.assertEqual(segunda["encoladas"], 0)
        self.assertEqual(segunda["repetidas"], 1)
        self.assertEqual(len(self.filas()), 1)

    def test_vuelve_a_avisar_al_dia_siguiente(self):
        self._plazo(HOY + dt.timedelta(days=3))
        notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)
        otro_dia = notificaciones.generar_recordatorios(
            self.db, self.socio, dias=3, canales=["email"], hoy=HOY + dt.timedelta(days=1)
        )

        self.assertEqual(otro_dia["encoladas"], 1)
        self.assertEqual(len(self.filas()), 2)

    def test_el_vencido_avisa_con_prioridad_alta(self):
        self._plazo(HOY - dt.timedelta(days=4), fatal=True)
        notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        fila = self.filas()[0]
        self.assertEqual(fila["prioridad"], "alta")
        self.assertIn("FATAL", fila["asunto"])
        self.assertIn("vencido hace 4 día(s)", fila["asunto"])

    def test_sin_responsable_avisa_a_los_abogados(self):
        plazo_id = self._plazo(HOY + dt.timedelta(days=1))
        self.db.ejecutar("UPDATE plazos SET responsable_id = NULL WHERE id = ?", (plazo_id,))

        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        self.assertEqual(informe["encoladas"], 2)   # la socia y la abogada
        self.assertEqual({f["destino"] for f in self.filas()}, {"socia@test.cl", "ana@test.cl"})

    def test_avisa_de_la_audiencia_proxima_a_su_responsable(self):
        self._audiencia(HOY + dt.timedelta(days=2), responsable=self.abogado)
        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        self.assertEqual(informe["audiencias_revisadas"], 1)
        self.assertEqual(informe["encoladas"], 1)
        fila = self.filas()[0]
        self.assertEqual(fila["destino"], "ana@test.cl")
        self.assertEqual(fila["causa_id"], self.causa_id)
        self.assertIn("Audiencia en 2 día(s)", fila["asunto"])
        self.assertIn("presencial", fila["cuerpo"])

    def test_la_audiencia_sin_responsable_avisa_al_estudio(self):
        self._audiencia(HOY + dt.timedelta(days=2))
        self.db.ejecutar("UPDATE audiencias SET responsable_id = NULL")

        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["email"], hoy=HOY)

        self.assertEqual(informe["encoladas"], 2)   # la socia y la abogada

    def test_el_sms_va_al_telefono_del_responsable(self):
        self._plazo(HOY + dt.timedelta(days=1), responsable=self.abogado)
        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["sms"], hoy=HOY)

        self.assertEqual(informe["encoladas"], 1)
        self.assertEqual(self.filas()[0]["canal"], "sms")
        self.assertEqual(self.filas()[0]["destino"], "+56900000001")

    def test_si_falta_el_telefono_lo_dice_en_vez_de_inventarlo(self):
        self._plazo(HOY + dt.timedelta(days=1), responsable=self.socio)   # la socia no tiene teléfono
        informe = notificaciones.generar_recordatorios(self.db, self.socio, dias=3, canales=["sms"], hoy=HOY)

        self.assertEqual(informe["encoladas"], 0)
        self.assertEqual(informe["sin_destino"], 1)
        self.assertEqual(self.filas(), [])

    def test_exige_permiso_al_generar(self):
        with self.assertRaises(auth.ErrorPermiso):
            notificaciones.generar_recordatorios(self.db, self.cliente_usuario, canales=["email"], hoy=HOY)


class TestAvisoDeAsignacion(BaseConEstudio):
    def test_crear_un_plazo_para_otro_lo_avisa_al_crearlo(self):
        service.crear_plazo(
            self.db, self.socio, self.causa_id, "Contestar traslado", dias=5, fecha_notificacion="2026-09-01",
            responsable_id=self.abogado["id"],
        )

        fila = self.filas()[0]
        self.assertEqual(fila["canal"], "email")
        self.assertEqual(fila["destino"], "ana@test.cl")
        self.assertEqual(fila["usuario_id"], self.abogado["id"])
        self.assertIn("Te asignaron un plazo", fila["asunto"])
        self.assertIn("Contestar traslado", fila["cuerpo"])
        self.assertIn("Vence el", fila["cuerpo"])

    def test_crear_un_plazo_propio_no_se_avisa_a_uno_mismo(self):
        service.asignar(self.db, self.socio, self.causa_id, self.abogado["id"], "responsable")
        creado = service.crear_plazo(
            self.db, self.abogado, self.causa_id, "Escrito de prueba", dias=5, fecha_notificacion="2026-09-01",
        )

        self.assertIsNotNone(creado["id"])
        self.assertEqual(self.filas(), [])   # nadie necesita un correo de lo que acaba de escribir

    def test_la_asignacion_de_audiencia_tambien_avisa(self):
        self._audiencia(HOY + dt.timedelta(days=5), responsable=self.abogado, limpiar=False)

        fila = self.filas()[0]
        self.assertIn("Te asignaron una audiencia", fila["asunto"])
        self.assertEqual(fila["destino"], "ana@test.cl")
        self.assertEqual(fila["audiencia_id"], self.db.uno("SELECT MAX(id) AS i FROM audiencias")["i"])

    def test_se_puede_apagar_en_la_configuracion(self):
        self.config_archivo.write_text(json.dumps({"avisar_asignaciones": False}), encoding="utf-8")
        plazo_id = self.db.insertar("plazos", {"causa_id": self.causa_id, "descripcion": "Contestar traslado"})

        self.assertIsNone(notificaciones.avisar_asignacion(
            self.db, self.socio, "plazo", plazo_id, "Contestar traslado",
            causa_id=self.causa_id, responsable_id=self.abogado["id"], plazo_id=plazo_id,
        ))
        self.assertEqual(self.filas(), [])

    def test_un_fallo_del_aviso_no_impide_crear_el_plazo(self):
        original = notificaciones.cargar_config
        notificaciones.cargar_config = lambda: (_ for _ in ()).throw(RuntimeError("configuración ilegible"))
        self.addCleanup(setattr, notificaciones, "cargar_config", original)

        creado = service.crear_plazo(
            self.db, self.socio, self.causa_id, "Contestar traslado", dias=5, fecha_notificacion="2026-09-01",
            responsable_id=self.abogado["id"],
        )

        # Un correo no puede impedir que el expediente avance: el plazo queda creado.
        self.assertIsNotNone(creado["id"])
        self.assertEqual(len(self.db.todos("SELECT * FROM plazos")), 1)
        self.assertEqual(self.filas(), [])
        self.assertIn("configuración ilegible", self.errores_log.read_text(encoding="utf-8"))

    def test_un_aviso_imposible_queda_anotado(self):
        # Clave foránea inexistente: el aviso no se puede guardar, y eso no puede pasar
        # en silencio (costó una hora de diagnóstico encontrarlo).
        self.assertIsNone(notificaciones.avisar_asignacion(
            self.db, self.socio, "plazo", 999999, "Plazo fantasma",
            causa_id=self.causa_id, responsable_id=self.abogado["id"], plazo_id=999999,
        ))

        self.assertTrue(self.errores_log.is_file())
        anotado = self.errores_log.read_text(encoding="utf-8")
        self.assertIn("aviso no encolado", anotado)
        self.assertIn("FOREIGN KEY", anotado)


if __name__ == "__main__":
    unittest.main()
