"""Pruebas del panel: cada módulo por interfaz, con los permisos y las reglas del CRM.

Solo libreria estandar + starlette (la interfaz usa FastAPI):
    python3 -m unittest discover -s tests

Lo que estas pruebas cuidan: que la interfaz **no** sea un atajo para saltear las reglas de
la terminal (permisos, último socio, confirmación de lo que no se puede deshacer), y que las
claves de correo y SMS nunca salgan por la API.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, seguridad, service  # noqa: E402
from openlegal.db import DB  # noqa: E402

# El núcleo del CRM es sólo librería estándar: la suite tiene que correr igual sin los extras
# de la interfaz (así corre en CI, en las ocho combinaciones de sistema y Python). Si falta
# FastAPI, estas pruebas se saltean en vez de romper la suite del núcleo.
try:
    from starlette.testclient import TestClient

    from openlegal.web import crear_app
except ImportError:  # pragma: no cover - depende del entorno
    crear_app = None            # type: ignore[assignment]
    TestClient = None           # type: ignore[assignment]

SIN_INTERFAZ = "falta el extra de la interfaz (pip install -e '.[ui]')"


@unittest.skipIf(TestClient is None, SIN_INTERFAZ)
class BasePanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.base = self.raiz / "estudio.db"
        self.config = self.raiz / "notificaciones.json"
        self._previos = {
            k: os.environ.get(k)
            for k in ("OPENLEGAL_CONFIG", "OPENLEGAL_AVISOS_LOG", "OPENLEGAL_ERRORES_LOG")
        }
        os.environ["OPENLEGAL_CONFIG"] = str(self.config)
        os.environ["OPENLEGAL_AVISOS_LOG"] = str(self.raiz / "avisos.log")
        os.environ["OPENLEGAL_ERRORES_LOG"] = str(self.raiz / "errores.log")

        db = DB(f"sqlite:///{self.base}")
        db.migrar()
        self.estudio = auth.crear_estudio(db, "Estudio Test", modo="oficina")
        self.socio = auth.crear_usuario(db, self.estudio, "Sofia Soto", "socia@test.cl", "socio", "clave-larga-1")
        auth.crear_usuario(db, self.estudio, "Ana Perez", "ana@test.cl", "abogado", "clave-larga-2")
        auth.crear_usuario(db, self.estudio, "Carla Diaz", "carla@test.cl", "paralegal", "clave-larga-3")
        self.socio_fila = db.uno("SELECT * FROM usuarios WHERE id = ?", (self.socio,))
        self.socio_fila.pop("password_hash", None)
        self.cliente = service.crear_cliente(
            db, self.socio_fila, "Lucía Herrera", "11.111.111-1", email="lucia@ejemplo.cl",
        )
        self.causa = service.crear_causa(
            db, self.socio_fila, "Herrera con Fondo del Norte", cliente_id=self.cliente,
            rol_rit="C-1234-2026", tribunal="1° Juzgado Civil", materia="civil",
        )
        db.cerrar()

        self.app = crear_app(db_url=f"sqlite:///{self.base}", token="token-de-prueba")
        self.cliente_http = self.entrar("socia@test.cl", "clave-larga-1")

    def tearDown(self):
        self.cliente_http.close()
        for clave, valor in self._previos.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        self.tmp.cleanup()

    def entrar(self, email: str, clave: str, cliente=None):
        """Entra al CRM con esa cuenta. Sin `cliente`, crea uno nuevo (como otro equipo)."""
        otro = cliente or TestClient(self.app)
        respuesta = otro.post("/api/login", json={"email": email, "password": clave})
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return otro

    def db(self) -> DB:
        return DB(f"sqlite:///{self.base}")

    def fallar_login(self, email: str, veces: int = 0) -> None:
        """Provoca intentos fallidos de verdad (los que la API cuenta y bloquean)."""
        db = DB(f"sqlite:///{self.base}")
        try:
            for _ in range(veces or seguridad.BLOQUEO_INTENTOS):
                auth.autenticar(db, email, "clave-equivocada")
        finally:
            db.cerrar()


class TestLaConexionYLosHilos(BasePanel):
    def test_la_base_se_puede_usar_y_cerrar_desde_otro_hilo(self):
        """El panel atiende en hilos distintos: la conexión tiene que aguantarlo.

        Sin `check_same_thread=False`, FastAPI abre la conexión en un hilo del grupo y la
        cierra en otro, y el cierre revienta con `sqlite3.ProgrammingError` — un módulo del
        panel devolvía error sólo a veces, según qué hilo tocara. Esta prueba lo fija: el
        mismo objeto se usa y se cierra desde un hilo que no lo creó.
        """
        import threading

        db = DB(f"sqlite:///{self.base}")
        resultado: dict = {}

        def en_otro_hilo():
            try:
                resultado["filas"] = len(db.todos("SELECT * FROM usuarios"))
            except Exception as exc:  # noqa: BLE001 - lo que se prueba es que no falle
                resultado["error"] = repr(exc)
            finally:
                db.cerrar()

        hilo = threading.Thread(target=en_otro_hilo)
        hilo.start()
        hilo.join(timeout=10)

        self.assertNotIn("error", resultado, resultado.get("error"))
        self.assertEqual(resultado.get("filas"), 3)


class TestPanelSeSirve(BasePanel):
    def test_el_html_trae_los_modulos(self):
        respuesta = self.cliente_http.get("/")
        self.assertEqual(respuesta.status_code, 200)
        for vista in ("avisos", "usuarios", "seguridad", "retencion", "datos"):
            self.assertIn(f'data-vista="{vista}"', respuesta.text)

    def test_el_js_y_el_css_se_sirven(self):
        self.assertIn("application/javascript", self.cliente_http.get("/panel.js").headers["content-type"])
        self.assertIn("text/css", self.cliente_http.get("/panel.css").headers["content-type"])

    def test_sin_sesion_no_hay_datos(self):
        limpio = TestClient(self.app)
        try:
            self.assertEqual(limpio.get("/api/avisos").status_code, 401)
            self.assertEqual(limpio.get("/api/usuarios").status_code, 401)
        finally:
            limpio.close()

    def test_la_sesion_dice_que_modulos_le_tocan(self):
        datos = self.cliente_http.get("/api/sesion").json()
        self.assertIn("usuario.gestionar", datos["permisos"])
        self.assertIn("aviso.gestionar", datos["permisos"])

        paralegal = self.entrar("carla@test.cl", "clave-larga-3")
        try:
            permisos = paralegal.get("/api/sesion").json()["permisos"]
            self.assertNotIn("usuario.gestionar", permisos)
            self.assertIn("aviso.gestionar", permisos)
        finally:
            paralegal.close()


class TestModuloAvisos(BasePanel):
    def test_la_configuracion_no_devuelve_la_clave(self):
        self.cliente_http.post("/api/avisos/config", json={"email": {
            "host": "smtp.estudio.cl", "puerto": 587, "usuario": "avisos@estudio.cl",
            "clave": "clave-secretisima", "de": "avisos@estudio.cl",
        }})

        respuesta = self.cliente_http.get("/api/avisos")
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("clave-secretisima", respuesta.text)
        self.assertEqual(respuesta.json()["formulario"]["email"]["host"], "smtp.estudio.cl")

    def test_guardar_la_configuracion_deja_el_archivo_en_600(self):
        self.cliente_http.post("/api/avisos/config", json={"sms": {"proveedor": "consola"}})

        self.assertTrue(self.config.is_file())
        self.assertEqual(oct(self.config.stat().st_mode)[-3:], "600")
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["sms"]["proveedor"], "consola")

    def test_una_clave_vacia_conserva_la_guardada(self):
        self.cliente_http.post("/api/avisos/config", json={"email": {
            "host": "smtp.estudio.cl", "puerto": 587, "usuario": "avisos@estudio.cl",
            "clave": "la-buena", "de": "avisos@estudio.cl",
        }})
        self.cliente_http.post("/api/avisos/config", json={"email": {"host": "smtp.otro.cl", "clave": ""}})

        guardado = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(guardado["email"]["host"], "smtp.otro.cl")
        self.assertEqual(guardado["email"]["clave"], "la-buena")   # no se borró por dejarla vacía

    def test_la_configuracion_queda_en_la_bitacora_sin_los_valores(self):
        self.cliente_http.post("/api/avisos/config", json={"email": {"host": "x.cl", "clave": "otra-secreta"}})

        db = self.db()
        fila = db.uno("SELECT * FROM auditoria WHERE accion = 'avisos.configurar'")
        db.cerrar()
        self.assertIsNotNone(fila)
        self.assertIn("email", fila["detalle"])
        self.assertNotIn("otra-secreta", fila["detalle"])

    def test_probar_sin_enviar_deja_el_aviso_en_el_archivo(self):
        respuesta = self.cliente_http.post("/api/avisos/probar", json={"canal": "consola"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["enviado"])
        self.assertIn("Prueba de avisos", (self.raiz / "avisos.log").read_text(encoding="utf-8"))

    def test_probar_sin_configuracion_lo_dice(self):
        respuesta = self.cliente_http.post("/api/avisos/probar", json={"canal": "email"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.json()["enviado"])
        self.assertIn("no está configurado", respuesta.json()["error"])

    def test_generar_recordatorios_desde_la_interfaz(self):
        db = self.db()
        service.crear_plazo(db, self.socio_fila, self.causa, "Contestar traslado", dias=2,
                            fecha_notificacion="2026-09-16")
        db.cerrar()

        respuesta = self.cliente_http.post("/api/avisos/generar")
        self.assertEqual(respuesta.status_code, 200)
        filas = respuesta.json()
        self.assertGreaterEqual(filas["encoladas"], 1)

    def test_quien_no_gestiona_avisos_no_entra(self):
        db = self.db()
        auth.crear_usuario(db, self.estudio, "Rosa Cliente", "cliente@test.cl", "cliente", "clave-larga-9")
        db.cerrar()
        cliente = self.entrar("cliente@test.cl", "clave-larga-9")
        try:
            self.assertEqual(cliente.get("/api/avisos").status_code, 403)
        finally:
            cliente.close()


class TestModuloUsuarios(BasePanel):
    def test_crear_y_editar_una_persona(self):
        respuesta = self.cliente_http.post("/api/usuarios", json={
            "nombre": "Pedro Rojas", "email": "pedro@test.cl", "rol": "abogado",
            "password": "clave-larga-4", "telefono": "+56912345678",
        })
        self.assertEqual(respuesta.status_code, 200)
        identificador = respuesta.json()["id"]

        edicion = self.cliente_http.post(f"/api/usuarios/{identificador}", json={
            "rol": "paralegal", "telefono": "+56987654321", "activo": False,
        })
        self.assertEqual(edicion.status_code, 200)
        self.assertEqual(edicion.json()["rol"], "paralegal")
        self.assertEqual(edicion.json()["activo"], 0)

    def test_una_clave_corta_se_rechaza(self):
        respuesta = self.cliente_http.post("/api/usuarios", json={
            "nombre": "Corto", "email": "corto@test.cl", "rol": "abogado", "password": "corta",
        })
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("10 caracteres", respuesta.json()["detail"])

    def test_no_deja_al_estudio_sin_su_ultimo_socio(self):
        db = self.db()
        socios = db.todos("SELECT id, email FROM usuarios WHERE rol = 'socio' AND activo = 1")
        db.cerrar()
        self.assertEqual(len(socios), 1)

        respuesta = self.cliente_http.post(f"/api/usuarios/{socios[0]['id']}", json={"activo": False})
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("último socio", respuesta.json()["detail"])

    def test_cambiar_la_clave_corta_las_sesiones_de_esa_cuenta(self):
        db = self.db()
        ana = db.uno("SELECT id FROM usuarios WHERE email = 'ana@test.cl'")
        db.cerrar()
        sesion_ana = self.entrar("ana@test.cl", "clave-larga-2")
        try:
            self.assertEqual(sesion_ana.get("/api/plazos?dias=5").status_code, 200)
            self.cliente_http.post(f"/api/usuarios/{ana['id']}", json={"password": "clave-nueva-larga"})
            self.assertEqual(sesion_ana.get("/api/plazos?dias=5").status_code, 401)
        finally:
            sesion_ana.close()

    def test_un_paralegal_no_administra_usuarios(self):
        paralegal = self.entrar("carla@test.cl", "clave-larga-3")
        try:
            self.assertEqual(paralegal.get("/api/usuarios").status_code, 403)
            self.assertEqual(paralegal.post("/api/usuarios", json={
                "nombre": "X", "email": "x@test.cl", "rol": "socio", "password": "clave-larga-8",
            }).status_code, 403)
        finally:
            paralegal.close()


class TestModuloSeguridad(BasePanel):
    def test_el_alta_del_segundo_factor_necesita_el_codigo(self):
        alta = self.cliente_http.post("/api/seguridad/2fa/preparar", json={"email": "ana@test.cl"})
        self.assertEqual(alta.status_code, 200)
        secreto = alta.json()["secreto"]
        self.assertTrue(alta.json()["uri"].startswith("otpauth://"))

        equivocado = self.cliente_http.post("/api/seguridad/2fa/confirmar",
                                           json={"email": "ana@test.cl", "codigo": "000000"})
        self.assertEqual(equivocado.status_code, 401)

        db = self.db()
        pendiente = db.uno("SELECT totp_activo FROM usuarios WHERE email = 'ana@test.cl'")
        db.cerrar()
        self.assertEqual(pendiente["totp_activo"], 0)   # un código malo no activa nada

        correcto = self.cliente_http.post("/api/seguridad/2fa/confirmar",
                                          json={"email": "ana@test.cl", "codigo": seguridad.codigo(secreto)})
        self.assertEqual(correcto.status_code, 200)

        db = self.db()
        activo = db.uno("SELECT totp_activo FROM usuarios WHERE email = 'ana@test.cl'")
        db.cerrar()
        self.assertEqual(activo["totp_activo"], 1)

    def test_muestra_quien_falta_y_deja_destrabar(self):
        self.fallar_login("ana@test.cl")

        informe = self.cliente_http.get("/api/seguridad").json()
        ana = [c for c in informe["cuentas"] if c["email"] == "ana@test.cl"][0]
        self.assertTrue(ana["bloqueo"]["bloqueada"])
        faltantes = [c["email"] for c in informe["cuentas"] if c["esperado"] and not c["totp_activo"]]
        self.assertIn("socia@test.cl", faltantes)

        respuesta = self.cliente_http.post("/api/seguridad/desbloquear", json={"email": "ana@test.cl"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.json()["bloqueada"])

    def test_la_cuenta_bloqueada_no_entra_ni_con_la_clave_buena(self):
        self.fallar_login("ana@test.cl")

        intento = TestClient(self.app)
        try:
            respuesta = intento.post("/api/login", json={"email": "ana@test.cl", "password": "clave-larga-2"})
            self.assertEqual(respuesta.status_code, 429)
            self.assertIn("bloqueada", respuesta.json()["detail"])
        finally:
            intento.close()


class TestModuloRetencion(BasePanel):
    def test_declara_el_plazo_y_muestra_el_informe(self):
        respuesta = self.cliente_http.post("/api/retencion", json={
            "tipo": "datos_de_persona", "meses": 60, "motivo": "criterio del estudio",
        })
        self.assertEqual(respuesta.status_code, 200)
        politica = respuesta.json()["politica"]["datos_de_persona"]
        self.assertEqual(politica["meses"], 60)
        self.assertEqual(politica["origen"], "declarada por el estudio")

    def test_ejecutar_simulando_no_cambia_nada(self):
        respuesta = self.cliente_http.post("/api/retencion/ejecutar", json={
            "motivo": "prueba", "simular": True,
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()["simulado"])

        db = self.db()
        cliente = db.uno("SELECT nombre FROM clientes WHERE id = ?", (self.cliente,))
        db.cerrar()
        self.assertEqual(cliente["nombre"], "Lucía Herrera")


class TestModuloDatos(BasePanel):
    def test_busca_el_titular_por_texto(self):
        encontrados = self.cliente_http.get("/api/titulares?texto=herrera").json()
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0]["nombre"], "Lucía Herrera")

    def test_exporta_el_expediente_con_su_hash(self):
        respuesta = self.cliente_http.post("/api/titulares/exportar", json={"rut": "11.111.111-1"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("attachment", respuesta.headers["content-disposition"])
        paquete = respuesta.json()
        self.assertEqual(paquete["titular"][0]["nombre"], "Lucía Herrera")
        self.assertEqual(len(respuesta.headers["X-OpenLegal-Sha256"]), 64)

    def test_anonimizar_pide_confirmacion_escrita(self):
        sin_confirmar = self.cliente_http.post("/api/titulares/anonimizar", json={
            "rut": "11.111.111-1", "motivo": "solicitud del titular", "simular": False,
        })
        self.assertEqual(sin_confirmar.status_code, 400)
        self.assertIn("ANONIMIZAR", sin_confirmar.json()["detail"])

        db = self.db()
        cliente = db.uno("SELECT nombre FROM clientes WHERE id = ?", (self.cliente,))
        db.cerrar()
        self.assertEqual(cliente["nombre"], "Lucía Herrera")   # no se tocó nada

        confirmado = self.cliente_http.post("/api/titulares/anonimizar", json={
            "rut": "11.111.111-1", "motivo": "solicitud del titular", "simular": False, "confirmar": "ANONIMIZAR",
        })
        self.assertEqual(confirmado.status_code, 200)

        db = self.db()
        cliente = db.uno("SELECT nombre, rut FROM clientes WHERE id = ?", (self.cliente,))
        db.cerrar()
        self.assertNotEqual(cliente["nombre"], "Lucía Herrera")
        self.assertIsNone(cliente["rut"])

    def test_sin_motivo_no_anonimiza(self):
        respuesta = self.cliente_http.post("/api/titulares/anonimizar", json={
            "rut": "11.111.111-1", "motivo": "   ", "simular": False, "confirmar": "ANONIMIZAR",
        })
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("motivo", respuesta.json()["detail"])


class TestModulosDelDia(BasePanel):
    def test_crear_cliente_causa_y_audiencia_desde_la_interfaz(self):
        nuevo_cliente = self.cliente_http.post("/api/clientes", json={
            "nombre": "Otro Cliente", "rut": "22.222.222-2", "email": "otro@ejemplo.cl",
        })
        self.assertEqual(nuevo_cliente.status_code, 200)

        nueva_causa = self.cliente_http.post("/api/causas", json={
            "caratula": "Otro con Banco del Sur", "cliente_id": nuevo_cliente.json()["id"], "rol_rit": "C-9-2026",
        })
        self.assertEqual(nueva_causa.status_code, 200)
        self.assertEqual(len(self.cliente_http.get("/api/causas").json()), 2)

        db = self.db()
        ana = db.uno("SELECT id FROM usuarios WHERE email = 'ana@test.cl'")
        db.cerrar()
        audiencia = self.cliente_http.post("/api/audiencias", json={
            "causa_id": self.causa, "tipo": "preparatoria", "fecha": "2026-10-05", "hora": "09:00",
            "responsable_id": ana["id"],
        })
        self.assertEqual(audiencia.status_code, 200)

        # Agendar para otra persona le deja su aviso encolado: eso también se ve en el panel.
        db = self.db()
        avisos = db.todos("SELECT * FROM notificaciones WHERE canal = 'email'")
        db.cerrar()
        self.assertEqual(len(avisos), 1)
        self.assertIn("Te asignaron una audiencia", avisos[0]["asunto"])

    def test_el_panel_inicial_resume_el_estudio(self):
        panel = self.cliente_http.get("/api/panel").json()
        self.assertIn("causas_por_estado", panel)
        self.assertIn("plazos_pendientes", panel)


if __name__ == "__main__":
    unittest.main()
