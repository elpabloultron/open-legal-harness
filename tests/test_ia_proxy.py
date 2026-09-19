"""Pruebas del proxy local de IA: registro, aviso, minimización y bloqueo en los dos dialectos.

Se levanta un **servidor aguas arriba de mentira** (el proveedor del modelo) en un puerto
libre del loopback, se configura el proxy contra él y se le habla como le hablaría el
harness: `POST /v1/chat/completions` (dialecto de OpenAI) o `POST /v1/messages` (dialecto de
Anthropic, el que usa `dsh` por defecto), con `stream` o sin él. Así se prueba el circuito
real —reenvío, trozos, decisión de autorización, registro— y no una función por separado.

Lo que estas pruebas cuidan, además de que funcione:

- que el contenido de los datos y la `api_key` NO aparezcan en ningún registro, archivo ni
  salida (se afirma explícitamente, columna por columna y línea por línea, en los dos dialectos);
- que un envío sin autorización no se reenvíe y quede anotado como bloqueado;
- que lo que se registra sea lo que salió (hash y caracteres del texto minimizado), con
  `via` = el dialecto por el que salió;
- que en el dialecto de Anthropic se minimicen el `system`, los bloques de `content` y las
  descripciones de las `tools`, y que lo que no se puede minimizar no salga.

Solo librería estándar:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import contextlib
import datetime as dt
import http.client
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from openlegal import auth, cli, ia, ia_proxy, service  # noqa: E402
from openlegal.db import DB  # noqa: E402

#: La clave del proveedor que usan las pruebas. Que aparezca una vez acá es a propósito:
#: si se filtra a un registro o a una salida, la prueba lo tiene que ver.
CLAVE_SECRETA = "clave-de-prueba-no-se-registra-123"


@contextlib.contextmanager
def capturar_salida():
    """Todo lo que el proxy (y la CLI) imprimen, para poder afirmar lo que NO dicen."""
    bufer = io.StringIO()
    with contextlib.redirect_stdout(bufer):
        yield bufer


class ServidorArriba(ThreadingHTTPServer):
    """El 'proveedor': guarda lo que recibió y puede quedarse a mitad del streaming."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, direccion: tuple[str, int]):
        super().__init__(direccion, ArribaFalso)
        self.pedidos: list[dict] = []
        self.esperando = threading.Event()

    @property
    def puerto(self) -> int:
        return int(self.server_address[1])

    def cuantos(self, ruta: str) -> int:
        return len([p for p in self.pedidos if p["ruta"] == ruta])


class ArribaFalso(BaseHTTPRequestHandler):
    """Un proveedor de mentira, pero sin salir de la máquina: habla los dos dialectos.

    En `/v1/messages` contesta como la API de Anthropic (con bloques de contenido y SSE con
    `event:`/`data:`); en `/v1/chat/completions`, como la de OpenAI.

    Habla HTTP/1.0 a propósito: sin `Content-Length` el cliente lee hasta que se cierra la
    conexión, que es exactamente el comportamiento de un stream SSE y no exige tramar a mano.
    """

    protocol_version = "HTTP/1.0"
    server: ServidorArriba

    def log_message(self, formato: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        self.server.pedidos.append({"ruta": self.path, "cabeceras": dict(self.headers), "payload": None})
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "modelo-falso", "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "no existe"}})

    def do_POST(self) -> None:  # noqa: N802
        largo = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(largo)
        payload = json.loads(cuerpo.decode("utf-8"))
        self.server.pedidos.append({"ruta": self.path, "cabeceras": dict(self.headers), "payload": payload})
        if self.path.rstrip("/").endswith("/messages"):
            self._anthropic(payload)
            return
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"pri"}}]}\n\n')
            self.wfile.flush()
            self.server.esperando.wait(timeout=5)
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"mera"}}]}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        # Se devuelve el modelo que vino en el pedido: así se ve que el proxy es un pasamanos.
        self._json(
            200,
            {
                "id": "respuesta-falsa",
                "object": "chat.completion",
                "model": payload.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "listo"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def _anthropic(self, payload: dict) -> None:
        """El dialecto de Anthropic: bloques de contenido y SSE con `event:` y `data:`."""
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(
                b'event: content_block_delta\n'
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"pri"}}\n\n'
            )
            self.wfile.flush()
            self.server.esperando.wait(timeout=5)
            self.wfile.write(
                b'event: content_block_delta\n'
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"mera"}}\n\n'
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            )
            self.wfile.flush()
            return
        self._json(
            200,
            {
                "id": "msg_falsa",
                "type": "message",
                "role": "assistant",
                "model": payload.get("model"),
                "content": [{"type": "text", "text": "listo"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    def _json(self, codigo: int, datos: dict) -> None:
        cuerpo = json.dumps(datos).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)
        self.wfile.flush()


class BaseProxy(unittest.TestCase):
    """Un CRM con causa, un proveedor de mentira y el proxy apuntado a él."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = pathlib.Path(self.tmp.name)
        self.base = self.raiz / "estudio.db"
        self.url_base = f"sqlite:///{self.base}"
        self._config_previa = os.environ.get("OPENLEGAL_IA_PROXY_CONFIG")
        os.environ["OPENLEGAL_IA_PROXY_CONFIG"] = str(self.raiz / "ia_proxy.json")

        db = DB(self.url_base)
        db.migrar()
        self.estudio = auth.crear_estudio(db, "Estudio Proxy", modo="oficina")
        self.socio_id = auth.crear_usuario(db, self.estudio, "Sofía Soto", "socia@proxy.cl", "socio", "clave-segura")
        self.socio = db.uno("SELECT * FROM usuarios WHERE id = ?", (self.socio_id,))
        assert self.socio is not None
        self.socio.pop("password_hash", None)
        db.cerrar()

        self.arriba = ServidorArriba(("127.0.0.1", 0))
        threading.Thread(target=self.arriba.serve_forever, daemon=True).start()

        self.salidas: list[str] = []
        self.notificaciones: list[tuple] = []
        self.servidores: list[ia_proxy.ServidorIA] = []
        self._bases: list[DB] = []

        # La causa con sus datos: los nombres que la minimización tiene que enmascarar.
        self.cliente_id = service.crear_cliente(
            self.db(), self.socio, "Constructora Andes SpA", "76.543.210-3",
            tipo_persona="juridica", representante_legal="Jorge Fuentes",
        )
        self.causa = service.crear_causa(
            self.db(), self.socio, "Pérez con Andes SpA", cliente_id=self.cliente_id,
            contraparte="Andes SpA", materia="laboral",
        )

        self.config = self.levantar_proxy()

    def tearDown(self):
        self.arriba.esperando.set()
        for servidor in self.servidores:
            servidor.shutdown()
            servidor.server_close()
            servidor.cerrar()
        # Cada conexión que abrió la prueba se cierra acá: una base abierta hace fallar la
        # limpieza del directorio temporal (y en Windows, directamente no se puede borrar).
        for db in self._bases:
            db.cerrar()
        self.arriba.shutdown()
        self.arriba.server_close()
        if self._config_previa is None:
            os.environ.pop("OPENLEGAL_IA_PROXY_CONFIG", None)
        else:
            os.environ["OPENLEGAL_IA_PROXY_CONFIG"] = self._config_previa
        self.tmp.cleanup()

    # ------------------------------------------------------------- utilidades
    def db(self) -> DB:
        """Una conexión a la base del CRM, que se cierra al terminar la prueba."""
        db = DB(self.url_base)
        self._bases.append(db)
        return db

    def levantar_proxy(self, **cambios) -> dict:
        """Escribe la configuración, levanta el proxy en un puerto libre y devuelve la config."""
        config = {
            "proveedor": "deepseek",
            "base_url": f"http://127.0.0.1:{self.arriba.puerto}/v1",
            "modelo_por_defecto": "modelo-por-defecto",
            "api_key": CLAVE_SECRETA,
            "destino_pais": "China",
            "base_de_datos": self.url_base,
            "avisar_escritorio": True,
            **cambios,
        }
        ia_proxy.guardar_config({k: v for k, v in config.items() if v is not None})
        cargada = ia_proxy.cargar_config()
        servidor = ia_proxy.crear_servidor(cargada, puerto=0)
        servidor.avisador = ia_proxy.Avisador(
            cargada, salida=self.salidas.append, notificar=lambda *a: self.notificaciones.append(a)
        )
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        self.servidores.append(servidor)
        self.puerto = int(servidor.server_address[1])
        return cargada

    def enviar(self, contenido="hola, ¿me ayudas con esto?", causa=None, stream=False, modelo="modelo-x", **extra):
        """Manda un pedido al proxy como lo haría el harness."""
        cabeceras = {"Content-Type": "application/json"}
        if causa is not None:
            cabeceras["X-OpenLegal-Causa"] = str(causa)
        cuerpo = {
            "model": modelo,
            "messages": [{"role": "user", "content": contenido}],
            **extra,
        }
        if stream:
            cuerpo["stream"] = True
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request("POST", "/v1/chat/completions", body=json.dumps(cuerpo).encode(), headers=cabeceras)
            respuesta = conexion.getresponse()
            return respuesta, respuesta.read()
        finally:
            conexion.close()

    def autorizar(self, causa: int | None = None, alcance: str = "analisis") -> int:
        return service.autorizar_ia(
            self.db(), self.socio, causa or self.causa, alcance=alcance, titular="Jorge Fuentes (Andes SpA)"
        )

    def envios(self) -> list[dict]:
        return self.db().todos("SELECT * FROM transferencias_ia ORDER BY id")

    def ultimo_pedido(self) -> dict:
        return self.arriba.pedidos[-1]


class TestReenvioYRegistro(BaseProxy):
    def test_registra_el_envio_y_lo_reenvia(self):
        self.autorizar()
        self.servidores[0].config["minimizar"] = False     # acá se prueba el pasamanos puro
        texto = "resumen del expediente"
        with capturar_salida() as salida:
            respuesta, cuerpo = self.enviar(texto, causa=self.causa)

        self.assertEqual(respuesta.status, 200)
        self.assertEqual(json.loads(cuerpo)["model"], "modelo-x", "el proxy tiene que ser un pasamanos fiel")
        self.assertEqual(self.ultimo_pedido()["ruta"], "/v1/chat/completions")
        self.assertEqual(self.ultimo_pedido()["payload"]["messages"][0]["content"], texto)

        filas = self.envios()
        self.assertEqual(len(filas), 1)
        fila = filas[0]
        self.assertEqual(fila["origen"], "proxy")
        self.assertEqual(fila["via"], "openai-compat")
        self.assertEqual(fila["proveedor"], "deepseek")
        self.assertEqual(fila["modelo"], "modelo-x")
        self.assertEqual(fila["destino_pais"], "China")
        self.assertEqual(fila["causa_id"], self.causa)
        self.assertEqual(fila["estudio_id"], self.estudio)
        self.assertEqual(fila["bloqueado"], 0)
        self.assertIsNotNone(fila["autorizacion_id"])
        # Lo que se registra es lo que salió: mismo texto, mismo largo, misma huella.
        self.assertEqual(fila["caracteres"], len(texto))
        self.assertEqual(fila["hash_payload"], ia.hash_payload(texto))
        # Y queda en la bitácora del estudio.
        db = self.db()
        eventos = db.todos("SELECT * FROM auditoria WHERE accion = 'ia.comunicar'")
        db.cerrar()
        self.assertEqual(len(eventos), 1)
        self.assertIn("origen=proxy", eventos[0]["detalle"])

        # El aviso de la terminal dice proveedor, modelo, país, caracteres y hash.
        self.assertTrue(self.salidas and self.salidas[0].startswith("IA ENVIADA"))
        self.assertIn("deepseek/modelo-x", self.salidas[0])
        self.assertIn(f"{len(texto)} caracteres", self.salidas[0])
        self.assertIn(ia.hash_payload(texto)[:16], self.salidas[0])
        self.assertIn("minimizado=no", self.salidas[0])
        self.assertIn("· POST /v1/chat/completions", salida.getvalue())

    def test_usa_el_modelo_por_defecto_cuando_el_pedido_no_lo_trae(self):
        self.autorizar()
        config = ia_proxy.cargar_config()
        config["modelo_por_defecto"] = "modelo-por-defecto"
        self.servidores[0].config["modelo_por_defecto"] = "modelo-por-defecto"
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request(
                "POST", "/v1/chat/completions",
                body=json.dumps({"messages": [{"role": "user", "content": "hola"}]}).encode(),
                headers={"Content-Type": "application/json", "X-OpenLegal-Causa": str(self.causa)},
            )
            respuesta = conexion.getresponse()
            self.assertEqual(respuesta.status, 200)
            respuesta.read()
        finally:
            conexion.close()
        self.assertEqual(self.envios()[0]["modelo"], "modelo-por-defecto")

    def test_models_se_reenvia(self):
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request("GET", "/v1/models", headers={"Accept": "application/json"})
            respuesta = conexion.getresponse()
            cuerpo = json.loads(respuesta.read())
        finally:
            conexion.close()
        self.assertEqual(respuesta.status, 200)
        self.assertEqual(cuerpo["data"][0]["id"], "modelo-falso")
        # Y la clave del proveedor es la que viaja aguas arriba: el harness manda una cualquiera.
        self.assertEqual(self.ultimo_pedido()["cabeceras"]["Authorization"], f"Bearer {CLAVE_SECRETA}")
        self.assertEqual(self.envios(), [], "consultar el catálogo no es mandar datos de ninguna causa")

    def test_streaming_se_reenvia_por_trozos_sin_esperar_el_final(self):
        self.autorizar()
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request(
                "POST", "/v1/chat/completions",
                body=json.dumps({"model": "modelo-x", "stream": True,
                                 "messages": [{"role": "user", "content": "hola"}]}).encode(),
                headers={"Content-Type": "application/json", "X-OpenLegal-Causa": str(self.causa)},
            )
            respuesta = conexion.getresponse()
            self.assertEqual(respuesta.status, 200)
            self.assertIn("text/event-stream", respuesta.headers.get("Content-Type", ""))
            # El proveedor tiene el segundo trozo retenido: si el proxy juntara todo antes de
            # reenviar, esta línea no llegaría hasta que se libere (y la prueba lo notaría).
            inicio = time.monotonic()
            primera = respuesta.readline()
            demora = time.monotonic() - inicio
            self.assertIn(b"pri", primera)
            self.assertLess(demora, 2, "el proxy está juntando todo antes de reenviar (no es un pasamanos)")
            self.arriba.esperando.set()
            resto = respuesta.read()
        finally:
            conexion.close()
        self.assertIn(b"mera", resto)
        self.assertIn(b"data: [DONE]", resto)
        # El envío quedó registrado una sola vez.
        filas = self.envios()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["causa_id"], self.causa)


class TestAutorizacion(BaseProxy):
    def test_sin_autorizacion_no_reenvia_y_lo_registra_bloqueado(self):
        antes = self.arriba.cuantos("/v1/chat/completions")
        with capturar_salida():
            respuesta, cuerpo = self.enviar("esto no puede salir", causa=self.causa)

        self.assertEqual(respuesta.status, 403)
        mensaje = json.loads(cuerpo)["error"]["message"]
        self.assertIn("no tiene autorización", mensaje)
        self.assertIn("openlegal ia autorizar", mensaje)
        self.assertIn(str(self.causa), mensaje)
        self.assertEqual(self.arriba.cuantos("/v1/chat/completions"), antes, "no se puede reenviar nada")
        self.assertIn("IA BLOQUEADA", "".join(self.salidas))

        fila = self.envios()[0]
        self.assertEqual(fila["bloqueado"], 1)
        self.assertEqual(fila["causa_id"], self.causa)
        self.assertIn("no tiene autorización", fila["motivo_bloqueo"])
        self.assertEqual(fila["caracteres"], len("esto no puede salir"))
        db = self.db()
        eventos = db.todos("SELECT * FROM auditoria WHERE accion = 'ia.sin_autorizacion'")
        db.cerrar()
        self.assertEqual(len(eventos), 1)
        self.assertIn("bloqueado", eventos[0]["detalle"])

    def test_revocar_la_autorizacion_vuelve_a_bloquear(self):
        self.autorizar()
        self.assertEqual(self.enviar("va", causa=self.causa)[0].status, 200)
        service.revocar_ia(self.db(), self.socio, self.causa)

        respuesta, _ = self.enviar("no va más", causa=self.causa)

        self.assertEqual(respuesta.status, 403)
        self.assertEqual([f["bloqueado"] for f in self.envios()], [0, 1])

    def test_permitir_sin_autorizacion_lo_deja_pasar_y_lo_registra_sin_autorizacion(self):
        self.servidores[0].config["permitir_sin_autorizacion"] = True

        with capturar_salida() as salida:
            respuesta, _ = self.enviar("sin autorización", causa=self.causa)

        self.assertEqual(respuesta.status, 200)
        fila = self.envios()[0]
        self.assertEqual(fila["bloqueado"], 0)
        self.assertIsNone(fila["autorizacion_id"])
        self.assertEqual(fila["causa_id"], self.causa)
        # Que salga no significa que se calle: el aviso dice que fue sin autorización.
        self.assertIn("SIN autorización", salida.getvalue())

    def test_causa_inexistente_no_sale(self):
        antes = self.arriba.cuantos("/v1/chat/completions")
        respuesta, cuerpo = self.enviar("hola", causa=9999)

        self.assertEqual(respuesta.status, 400)
        self.assertIn("no existe en esta base", json.loads(cuerpo)["error"]["message"])
        self.assertEqual(self.arriba.cuantos("/v1/chat/completions"), antes)
        self.assertEqual(self.envios(), [])

    def test_la_cabecera_tiene_que_ser_un_numero(self):
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request(
                "POST", "/v1/chat/completions",
                body=json.dumps({"messages": [{"role": "user", "content": "x"}]}).encode(),
                headers={"Content-Type": "application/json", "X-OpenLegal-Causa": "la causa de Pérez"},
            )
            respuesta = conexion.getresponse()
            cuerpo = json.loads(respuesta.read())
        finally:
            conexion.close()
        self.assertEqual(respuesta.status, 400)
        self.assertIn("X-OpenLegal-Causa", cuerpo["error"]["message"])


class TestLaCausa(BaseProxy):
    def test_la_causa_sale_de_la_cabecera(self):
        self.autorizar()
        self.enviar("con cabecera", causa=self.causa)
        self.assertEqual(self.envios()[0]["causa_id"], self.causa)

    def test_la_causa_por_defecto_se_usa_si_no_hay_cabecera(self):
        self.autorizar()
        self.servidores[0].config["causa_por_defecto"] = self.causa

        self.enviar("sin cabecera")

        self.assertEqual(self.envios()[0]["causa_id"], self.causa)

    def test_sin_causa_se_registra_con_causa_nula(self):
        with capturar_salida() as salida:
            respuesta, _ = self.enviar("una consulta suelta, sin expediente")

        self.assertEqual(respuesta.status, 200)
        fila = self.envios()[0]
        self.assertIsNone(fila["causa_id"])
        self.assertEqual(fila["origen"], "proxy")
        self.assertEqual(fila["bloqueado"], 0)
        # Se dice que no se puede comprobar autorización: no se calla la diferencia.
        self.assertIn("SIN CAUSA", "".join(self.salidas))
        self.assertIn("no se puede comprobar autorización", salida.getvalue())

    def test_sin_causa_el_estudio_se_hereda_solo_si_hay_uno(self):
        self.enviar("consulta suelta")
        self.assertEqual(self.envios()[0]["estudio_id"], self.estudio)

        # Con dos estudios en la misma base no se puede saber de quién es el envío: queda nulo
        # (y el panel lo dice así) en vez de adjudicarlo al primero de la lista.
        db = self.db()
        auth.crear_estudio(db, "Otro Estudio", modo="oficina")
        db.cerrar()
        self.enviar("otra consulta suelta")

        self.assertIsNone(self.envios()[1]["estudio_id"])


class TestMinimizacion(BaseProxy):
    def test_minimiza_antes_de_mandar_y_lo_anota(self):
        self.autorizar()
        texto = "La actora es Constructora Andes SpA, RUT 76.543.210-3, y la contraparte es Andes SpA."
        with capturar_salida():
            respuesta, _ = self.enviar(texto, causa=self.causa)

        self.assertEqual(respuesta.status, 200)
        salio = self.ultimo_pedido()["payload"]["messages"][0]["content"]
        self.assertNotIn("76.543.210-3", salio, "el RUT no puede salir a la vista")
        self.assertNotIn("Constructora Andes SpA", salio, "el nombre del cliente no puede salir a la vista")
        self.assertIn("[RUT", salio)
        fila = self.envios()[0]
        self.assertEqual(fila["redactado"], 1)
        self.assertEqual(fila["caracteres"], len(salio), "se registra lo minimizado, no el original")
        self.assertEqual(fila["hash_payload"], ia.hash_payload(salio))
        self.assertIn("minimizado=sí", "".join(self.salidas))

    def test_con_minimizar_apagado_sale_tal_cual_y_queda_dicho(self):
        self.autorizar()
        self.servidores[0].config["minimizar"] = False

        self.enviar("RUT 76.543.210-3", causa=self.causa)

        self.assertEqual(self.ultimo_pedido()["payload"]["messages"][0]["content"], "RUT 76.543.210-3")
        self.assertEqual(self.envios()[0]["redactado"], 0)

    def test_un_pedido_que_no_se_puede_minimizar_no_sale(self):
        self.autorizar()
        antes = self.arriba.cuantos("/v1/chat/completions")
        imagen = "data:image/png;base64,AAAA"
        with capturar_salida():
            conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
            try:
                conexion.request(
                    "POST", "/v1/chat/completions",
                    body=json.dumps({"messages": [{"role": "user", "content": [
                        {"type": "text", "text": "mira esto"},
                        {"type": "image_url", "image_url": {"url": imagen}},
                    ]}]}).encode(),
                    headers={"Content-Type": "application/json", "X-OpenLegal-Causa": str(self.causa)},
                )
                respuesta = conexion.getresponse()
                cuerpo = json.loads(respuesta.read())
            finally:
                conexion.close()

        self.assertEqual(respuesta.status, 422)
        self.assertIn("no se puede minimizar", cuerpo["error"]["message"])
        self.assertEqual(self.arriba.cuantos("/v1/chat/completions"), antes, "no puede salir sin minimizar")
        fila = self.envios()[0]
        self.assertEqual(fila["bloqueado"], 1)
        self.assertIn("no pude minimizar", fila["motivo_bloqueo"])
        self.assertIn("IA BLOQUEADA", "".join(self.salidas))


class TestSecretos(BaseProxy):
    def test_la_api_key_no_aparece_en_ningun_registro_ni_salida(self):
        self.autorizar()
        with capturar_salida() as salida:
            respuesta, cuerpo = self.enviar("datos de la causa", causa=self.causa)
            estado = ia_proxy.resumen()
        self.assertEqual(respuesta.status, 200)

        # 1. Viaja aguas arriba (que es para lo único que sirve)...
        self.assertEqual(self.ultimo_pedido()["cabeceras"]["Authorization"], f"Bearer {CLAVE_SECRETA}")
        # 2. ...y no aparece en NINGUNA columna de ningún registro.
        db = self.db()
        for tabla in ("transferencias_ia", "auditoria", "notificaciones", "autorizaciones_ia"):
            for fila in db.todos(f"SELECT * FROM {tabla}"):
                for columna, valor in fila.items():
                    self.assertNotIn(CLAVE_SECRETA, str(valor), f"la clave se filtró a {tabla}.{columna}")
        db.cerrar()
        # 3. Ni en la salida del proxy, ni en la respuesta que va al cliente.
        self.assertNotIn(CLAVE_SECRETA, salida.getvalue())
        self.assertNotIn(CLAVE_SECRETA, cuerpo.decode("utf-8"))
        # 4. Ni en el resumen de estado: se dice si está configurada, no cuál es.
        self.assertEqual(estado["api_key"], "configurada")
        self.assertNotIn(CLAVE_SECRETA, json.dumps(estado, ensure_ascii=False))
        # 5. Ni en el archivo de configuración más allá de su propia clave (permisos 600).
        self.assertEqual(ia_proxy.permisos_del_archivo(), "600")
        # 6. Ni en la salida de `openlegal ia-proxy --estado`, que es lo que se muestra en pantalla.
        with capturar_salida() as pantalla:
            codigo = cli.main(["--db", self.url_base, "ia-proxy", "--estado"])
        self.assertEqual(codigo, 0)
        self.assertNotIn(CLAVE_SECRETA, pantalla.getvalue())
        self.assertIn("api_key: configurada", pantalla.getvalue())
        self.assertIn("127.0.0.1", pantalla.getvalue())

    def test_un_archivo_de_configuracion_abierto_se_avisa(self):
        ruta = pathlib.Path(os.environ["OPENLEGAL_IA_PROXY_CONFIG"])
        os.chmod(ruta, 0o644)

        estado = ia_proxy.resumen()

        self.assertEqual(estado["permisos"], "644")
        self.assertTrue(any("600" in aviso for aviso in estado["avisos"]))

    def test_lo_que_se_manda_no_se_guarda_en_ninguna_parte(self):
        self.autorizar()
        secreto_del_expediente = "el actor confesó el 3 de marzo"
        self.enviar(secreto_del_expediente, causa=self.causa)

        db = self.db()
        archivo = self.base.read_bytes()
        filas = db.todos("SELECT * FROM transferencias_ia")
        auditoria = db.todos("SELECT * FROM auditoria")
        db.cerrar()

        self.assertTrue(filas and auditoria)
        self.assertNotIn(secreto_del_expediente.encode(), archivo, "el contenido quedó escrito en la base")
        for fila in (*filas, *auditoria):
            for valor in fila.values():
                self.assertNotIn(secreto_del_expediente, str(valor))


class TestAvisos(BaseProxy):
    def test_avisar_al_escritorio_tiene_tope_pero_los_bloqueos_no(self):
        ahora = dt.datetime(2026, 9, 19, 12, 0, 0)
        avisos: list[tuple] = []
        avisador = ia_proxy.Avisador(
            {"avisar_escritorio": True}, salida=lambda _: None, notificar=lambda *a: avisos.append(a)
        )

        self.assertTrue(avisador.envio("un envío", ahora=ahora))
        self.assertFalse(avisador.envio("otro envío", ahora=ahora + dt.timedelta(minutes=1)), "el tope no funcionó")
        self.assertTrue(avisador.envio("pasados los 5 minutos", ahora=ahora + dt.timedelta(minutes=6)))
        self.assertFalse(avisador.envio("un tercero, de nuevo dentro del tope", ahora=ahora + dt.timedelta(minutes=7)))
        # Un envío bloqueado se avisa siempre: es el que hay que mirar.
        self.assertTrue(avisador.envio("bloqueado", bloqueado=True, ahora=ahora + dt.timedelta(minutes=8)))
        self.assertTrue(avisador.envio("bloqueado otra vez", bloqueado=True, ahora=ahora + dt.timedelta(minutes=8)))
        self.assertEqual(len(avisos), 4)
        self.assertTrue(all(a[0] == "Open Legal Harness: uso de IA" for a in avisos))

    def test_sin_notify_send_lo_dice_y_sigue(self):
        salidas: list[str] = []
        avisador = ia_proxy.Avisador(
            {"avisar_escritorio": True},
            salida=salidas.append,
            notificar=lambda *a: (_ for _ in ()).throw(FileNotFoundError("no hay notify-send")),
        )

        self.assertFalse(avisador.envio("un envío"))
        self.assertTrue(any("notify-send" in linea for linea in salidas))
        self.assertIn("IA ENVIADA", salidas[0])

    def test_con_los_avisos_de_escritorio_apagados_no_manda_nada(self):
        avisos: list[tuple] = []
        avisador = ia_proxy.Avisador(
            {"avisar_escritorio": False}, salida=lambda _: None, notificar=lambda *a: avisos.append(a)
        )
        self.assertFalse(avisador.envio("un envío"))
        self.assertEqual(avisos, [])


class TestConfiguracionYArranque(BaseProxy):
    def test_escucha_solo_en_loopback(self):
        self.assertEqual(self.servidores[0].server_address[0], ia_proxy.DIRECCION)
        self.assertEqual(ia_proxy.DIRECCION, "127.0.0.1")

    def test_el_banner_dice_la_direccion_y_el_proveedor_sin_claves(self):
        lineas = ia_proxy.banner(self.servidores[0])
        texto = "\n".join(lineas)
        self.assertIn(f"http://127.0.0.1:{self.puerto}/v1", texto)
        self.assertIn("deepseek", texto)
        self.assertIn("base_url", texto)
        self.assertNotIn(CLAVE_SECRETA, texto)

    def test_la_configuracion_se_guarda_con_permisos_600(self):
        ia_proxy.guardar_config({"puerto": 8795, "proveedor": "openai"})
        ruta = pathlib.Path(os.environ["OPENLEGAL_IA_PROXY_CONFIG"])
        if os.name != "nt":  # en Windows los permisos son otra cosa (no hay 0o600 real)
            self.assertEqual(ruta.stat().st_mode & 0o777, 0o600)
        cargada = ia_proxy.cargar_config()
        self.assertEqual(cargada["puerto"], 8795)
        self.assertEqual(cargada["proveedor"], "openai")
        # Un valor vacío no borra lo que ya estaba (la clave sigue donde tiene que estar).
        ia_proxy.guardar_config({"modelo_por_defecto": ""})
        self.assertEqual(ia_proxy.cargar_config()["api_key"], CLAVE_SECRETA)

    def test_una_configuracion_ilegible_sale_como_frase(self):
        pathlib.Path(os.environ["OPENLEGAL_IA_PROXY_CONFIG"]).write_text("{ no es json", encoding="utf-8")
        with self.assertRaises(ValueError) as caso:
            ia_proxy.cargar_config()
        self.assertIn("no es JSON válido", str(caso.exception))

    def test_el_proveedor_se_infiere_si_no_se_declara(self):
        self.assertEqual(ia_proxy.proveedor_de({"proveedor": "", "base_url": "https://api.deepseek.com/v1"}), "deepseek")
        self.assertEqual(ia_proxy.proveedor_de({"proveedor": "", "base_url": "https://api.openai.com/v1"}), "openai")
        self.assertEqual(ia_proxy.proveedor_de({"proveedor": "", "base_url": "http://127.0.0.1:11434/v1"}), "local")
        self.assertEqual(ia_proxy.proveedor_de({"proveedor": "", "base_url": "https://miapi.cl/v1"}), "otro")
        # Lo declarado manda sobre lo inferido.
        self.assertEqual(ia_proxy.proveedor_de({"proveedor": "anthropic", "base_url": "https://miapi.cl/v1"}), "anthropic")

    def test_el_pais_nunca_se_inventa(self):
        self.assertEqual(ia_proxy.pais_de({"destino_pais": "", "proveedor": "deepseek"}, "deepseek"), "China")
        self.assertEqual(ia_proxy.pais_de({"destino_pais": "Alemania"}, "otro"), "Alemania")
        self.assertEqual(ia_proxy.pais_de({"destino_pais": "", "proveedor": "otro"}, "otro"), "sin verificar")

    def test_si_no_puede_registrar_no_reenvia(self):
        """Sin registro no hay aviso: el proxy no manda datos a ciegas."""
        self.autorizar()
        # Una base que no se puede abrir de verdad (la "ruta" es un directorio): el proxy
        # prefiere negarse a mandar antes que mandar sin dejar registro.
        self.servidores[0].db_url = f"sqlite:///{self.raiz}"
        self.servidores[0]._db = None
        antes = self.arriba.cuantos("/v1/chat/completions")

        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request(
                "POST", "/v1/chat/completions",
                body=json.dumps({"messages": [{"role": "user", "content": "x"}]}).encode(),
                headers={"Content-Type": "application/json", "X-OpenLegal-Causa": str(self.causa)},
            )
            respuesta = conexion.getresponse()
        finally:
            conexion.close()

        self.assertEqual(respuesta.status, 503)
        self.assertEqual(self.arriba.cuantos("/v1/chat/completions"), antes)

    def test_si_el_puerto_esta_tomado_lo_dice_como_frase(self):
        """El caso real: ya hay un proxy corriendo (o el CRM) y el estudio no sabe por qué."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupado:
            ocupado.bind(("127.0.0.1", 0))
            ocupado.listen(1)
            puerto = ocupado.getsockname()[1]

            with self.assertRaises(ValueError) as caso:
                ia_proxy.crear_servidor(ia_proxy.cargar_config(), puerto=puerto)

        self.assertIn("no pude escuchar", str(caso.exception))
        self.assertIn("--estado", str(caso.exception))

    def test_envios_recientes_filtra_por_bloqueados(self):
        self.enviar("sin autorización", causa=self.causa)
        self.autorizar()
        self.enviar("con autorización", causa=self.causa)
        db = self.db()
        todos = ia_proxy.envios_recientes(db, limite=10)
        bloqueados = ia_proxy.envios_recientes(db, solo_bloqueados=True, limite=10)
        de_la_causa = ia_proxy.envios_recientes(db, causa_id=self.causa, limite=10)
        db.cerrar()

        self.assertEqual(len(todos), 2)
        self.assertEqual([f["bloqueado"] for f in bloqueados], [1])
        self.assertEqual(len(de_la_causa), 2)
        # Son metadatos: ni el contenido ni la clave tienen por dónde aparecer.
        self.assertNotIn("contenido", json.dumps(todos))


class BaseAnthropic(BaseProxy):
    """El mismo CRM y el mismo proveedor de mentira, pero el proxy hablado en el dialecto de Anthropic.

    Se levanta un segundo proxy (el del dialecto de Anthropic) para no mezclar el puerto de las
    pruebas del dialecto de OpenAI. El `base_url_anthropic` es el del proveedor de mentira, que
    publica su `/v1/messages` en una dirección distinta de la de OpenAI —como hace DeepSeek—.
    """

    #: Dónde contesta el proveedor falso el dialecto de Anthropic (su `/v1/messages` cuelga de acá).
    SUFIJO = "/anthropic"

    def setUp(self):
        super().setUp()
        self.levantar_proxy(base_url_anthropic=f"http://127.0.0.1:{self.arriba.puerto}{self.SUFIJO}")
        self.servidor = self.servidores[-1]

    @property
    def ruta_arriba(self) -> str:
        """La ruta que tiene que pedir el proxy aguas arriba en este dialecto."""
        return f"{self.SUFIJO}/v1/messages"

    # ------------------------------------------------------------- utilidades
    def enviar_anthropic(self, contenido="hola, ¿me ayudas con esto?", causa=None, stream=False, **extra):
        """Manda un pedido al proxy como lo haría el harness: dialecto de Anthropic."""
        cuerpo = {
            "model": "modelo-x",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": contenido}],
            **extra,
        }
        if stream:
            cuerpo["stream"] = True
        return self.enviar_anthropic_payload(cuerpo, causa=causa)

    def enviar_anthropic_payload(self, cuerpo: dict, causa=None, cabeceras_extra: dict | None = None):
        """Manda un cuerpo tal cual al `/v1/messages` del proxy (para armar pedidos raros)."""
        cabeceras = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "una-clave-cualquiera-del-cliente",
            **(cabeceras_extra or {}),
        }
        if causa is not None:
            cabeceras["X-OpenLegal-Causa"] = str(causa)
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request("POST", "/v1/messages", body=json.dumps(cuerpo).encode(), headers=cabeceras)
            respuesta = conexion.getresponse()
            return respuesta, respuesta.read()
        finally:
            conexion.close()

    def pedir_a_mano(self, ruta: str, cuerpo: bytes = b"{}", cabeceras: dict | None = None):
        """Un pedido crudo, para probar rutas y cuerpos que no salen de un cliente bien portado."""
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request("POST", ruta, body=cuerpo, headers={"Content-Type": "application/json", **(cabeceras or {})})
            respuesta = conexion.getresponse()
            return respuesta, respuesta.read()
        finally:
            conexion.close()


class TestDialectoAnthropic(BaseAnthropic):
    """El dialecto de Anthropic: registro propio, minimización de `system` y de bloques, bloqueo."""

    def test_registra_el_envio_y_lo_reenvia(self):
        self.autorizar()
        self.servidor.config["minimizar"] = False          # acá se prueba el pasamanos puro
        texto = "resumen del expediente"
        with capturar_salida() as salida:
            respuesta, cuerpo = self.enviar_anthropic(texto, causa=self.causa)

        self.assertEqual(respuesta.status, 200)
        self.assertEqual(json.loads(cuerpo)["model"], "modelo-x", "el proxy tiene que ser un pasamanos fiel")
        pedido = self.ultimo_pedido()
        self.assertEqual(pedido["ruta"], self.ruta_arriba)
        self.assertEqual(pedido["payload"]["messages"][0]["content"], texto)
        self.assertEqual(pedido["payload"]["max_tokens"], 16, "los parámetros del dialecto pasan tal cual")
        # La clave real viaja aguas arriba en la cabecera del dialecto, no en la del cliente.
        self.assertEqual(pedido["cabeceras"]["x-api-key"], CLAVE_SECRETA)
        self.assertEqual(pedido["cabeceras"]["Authorization"], f"Bearer {CLAVE_SECRETA}")
        self.assertNotIn("una-clave-cualquiera-del-cliente", json.dumps(pedido["cabeceras"]))
        self.assertEqual(pedido["cabeceras"]["anthropic-version"], "2023-06-01")

        filas = self.envios()
        self.assertEqual(len(filas), 1)
        fila = filas[0]
        self.assertEqual(fila["via"], "anthropic-compat")
        self.assertEqual(fila["origen"], "proxy")
        self.assertEqual(fila["proveedor"], "deepseek")
        self.assertEqual(fila["modelo"], "modelo-x")
        self.assertEqual(fila["destino_pais"], "China")
        self.assertEqual(fila["causa_id"], self.causa)
        self.assertEqual(fila["estudio_id"], self.estudio)
        self.assertEqual(fila["bloqueado"], 0)
        self.assertIsNotNone(fila["autorizacion_id"])
        # Lo que se registra es lo que salió: mismo texto, mismo largo, misma huella.
        self.assertEqual(fila["caracteres"], len(texto))
        self.assertEqual(fila["hash_payload"], ia.hash_payload(texto))

        db = self.db()
        eventos = db.todos("SELECT * FROM auditoria WHERE accion = 'ia.comunicar'")
        db.cerrar()
        self.assertEqual(len(eventos), 1)
        self.assertIn("via=anthropic-compat", eventos[0]["detalle"])

        # El aviso de la terminal dice lo mismo que en el otro dialecto, sin contenido.
        self.assertTrue(self.salidas and self.salidas[0].startswith("IA ENVIADA"))
        self.assertIn("deepseek/modelo-x", self.salidas[0])
        self.assertIn(f"{len(texto)} caracteres", self.salidas[0])
        self.assertIn("· POST /v1/messages", salida.getvalue())

    def test_minimiza_el_system_y_los_bloques_de_content(self):
        self.autorizar()
        respuesta, _ = self.enviar_anthropic_payload(
            {
                "model": "modelo-x",
                "max_tokens": 16,
                "system": [{"type": "text", "text": "Trabajas para el estudio de Constructora Andes SpA"}],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "El RUT del cliente es 76.543.210-3"},
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": [{"type": "text", "text": "Representante legal: Jorge Fuentes"}],
                            },
                        ],
                    }
                ],
            },
            causa=self.causa,
        )

        self.assertEqual(respuesta.status, 200)
        salio = self.ultimo_pedido()["payload"]
        system = json.dumps(salio["system"], ensure_ascii=False)
        contenido = json.dumps(salio["messages"], ensure_ascii=False)
        self.assertNotIn("Constructora Andes SpA", system, "el nombre del cliente no puede salir")
        self.assertIn("[NOMBRE", system)
        self.assertNotIn("76.543.210-3", contenido, "el RUT no puede salir")
        self.assertNotIn("Jorge Fuentes", contenido, "el del resultado de la herramienta tampoco")
        self.assertIn("[RUT", contenido)
        # La estructura del pedido se conserva: el proxy minimiza texto, no rearma el pedido.
        self.assertEqual(salio["messages"][0]["content"][1]["type"], "tool_result")
        self.assertEqual(salio["messages"][0]["content"][1]["tool_use_id"], "toolu_1")

        fila = self.envios()[0]
        self.assertEqual(fila["redactado"], 1)
        self.assertEqual(fila["via"], "anthropic-compat")
        # Se registra lo minimizado —no el original—: mismo largo y misma huella que lo que salió.
        minimizado = ia_proxy.texto_del_payload_anthropic(salio)
        self.assertEqual(fila["caracteres"], len(minimizado))
        self.assertEqual(fila["hash_payload"], ia.hash_payload(minimizado))
        self.assertIn("minimizado=sí", "".join(self.salidas))

    def test_minimiza_los_input_de_los_tool_use_y_el_system_es_texto_plano(self):
        self.autorizar()
        respuesta, _ = self.enviar_anthropic_payload(
            {
                "model": "modelo-x",
                "max_tokens": 16,
                "system": "Consulta la causa de Andes SpA",
                "messages": [
                    {"role": "user", "content": "¿qué plazo corre?"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_2",
                                "name": "crm_plazo",
                                "input": {"caratula": "Pérez con Andes SpA", "dias": 5, "urgente": True},
                            }
                        ],
                    },
                ],
            },
            causa=self.causa,
        )

        self.assertEqual(respuesta.status, 200)
        salio = self.ultimo_pedido()["payload"]
        self.assertNotIn("Andes SpA", json.dumps(salio, ensure_ascii=False))
        entrada = salio["messages"][1]["content"][0]["input"]
        self.assertNotIn("Andes SpA", entrada["caratula"])
        # Lo que no es texto no se toca: los números y los booleanos quedan como estaban.
        self.assertEqual(entrada["dias"], 5)
        self.assertIs(entrada["urgente"], True)
        self.assertEqual(self.envios()[0]["redactado"], 1)

    def test_las_descripciones_de_las_tools_se_minimizan_y_el_esquema_no(self):
        """Decisión escrita: la descripción de una herramienta es texto del estudio y se minimiza.

        El `input_schema` no se toca a propósito: es la estructura de la herramienta y enmascarar
        un valor de ahí (el `enum` de un campo) aguas arriba la rompería. Si en un esquema van
        datos de personas, el error está en la herramienta, no en la minimización.
        """
        self.autorizar()
        esquema = {"type": "object", "properties": {"campo": {"type": "string", "enum": ["Constructora Andes SpA"]}}}
        respuesta, _ = self.enviar_anthropic_payload(
            {
                "model": "modelo-x",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "consulta"}],
                "tools": [
                    {
                        "name": "crm_causa",
                        "description": "Lee la ficha de Constructora Andes SpA, RUT 76.543.210-3",
                        "input_schema": esquema,
                    }
                ],
            },
            causa=self.causa,
        )

        self.assertEqual(respuesta.status, 200)
        herramienta = self.ultimo_pedido()["payload"]["tools"][0]
        self.assertNotIn("Constructora Andes SpA", herramienta["description"])
        self.assertNotIn("76.543.210-3", herramienta["description"])
        self.assertIn("[RUT", herramienta["description"])
        self.assertEqual(herramienta["input_schema"], esquema, "el esquema no se minimiza (decisión escrita)")
        self.assertEqual(herramienta["name"], "crm_causa")

    def test_un_payload_que_no_se_puede_minimizar_no_sale(self):
        self.autorizar()
        antes = self.arriba.cuantos(self.ruta_arriba)
        with capturar_salida():
            respuesta, cuerpo = self.enviar_anthropic_payload(
                {
                    "model": "modelo-x",
                    "max_tokens": 16,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "mira esto"},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                            ],
                        }
                    ],
                },
                causa=self.causa,
            )

        self.assertEqual(respuesta.status, 422)
        error = json.loads(cuerpo)["error"]
        self.assertIn("no se puede minimizar", error["message"])
        self.assertEqual(error["type"], "invalid_request_error")
        self.assertEqual(self.arriba.cuantos(self.ruta_arriba), antes, "no puede salir sin minimizar")
        fila = self.envios()[0]
        self.assertEqual(fila["bloqueado"], 1)
        self.assertEqual(fila["via"], "anthropic-compat")
        self.assertIn("no pude minimizar", fila["motivo_bloqueo"])
        self.assertIn("IA BLOQUEADA", "".join(self.salidas))

    def test_sin_autorizacion_no_reenvia_y_lo_registra_bloqueado(self):
        antes = self.arriba.cuantos(self.ruta_arriba)
        with capturar_salida():
            respuesta, cuerpo = self.enviar_anthropic("esto no puede salir", causa=self.causa)

        self.assertEqual(respuesta.status, 403)
        # El error sale en el sobre del dialecto: es lo que mira un cliente de Anthropic.
        datos = json.loads(cuerpo)
        self.assertEqual(datos["type"], "error")
        error = datos["error"]
        self.assertEqual(error["type"], "permission_error")
        self.assertIn("no tiene autorización", error["message"])
        self.assertIn("openlegal ia autorizar", error["message"])
        self.assertIn(str(self.causa), error["message"])
        self.assertEqual(self.arriba.cuantos(self.ruta_arriba), antes, "no se puede reenviar nada")

        fila = self.envios()[0]
        self.assertEqual(fila["bloqueado"], 1)
        self.assertEqual(fila["via"], "anthropic-compat")
        self.assertEqual(fila["causa_id"], self.causa)
        self.assertIn("no tiene autorización", fila["motivo_bloqueo"])
        db = self.db()
        eventos = db.todos("SELECT * FROM auditoria WHERE accion = 'ia.sin_autorizacion'")
        db.cerrar()
        self.assertEqual(len(eventos), 1)
        self.assertIn("via=anthropic-compat", eventos[0]["detalle"])
        self.assertIn("IA BLOQUEADA", "".join(self.salidas))

    def test_streaming_se_reenvia_por_trozos_sin_esperar_el_final(self):
        self.autorizar()
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request(
                "POST", "/v1/messages",
                body=json.dumps({"model": "modelo-x", "max_tokens": 16, "stream": True,
                                 "messages": [{"role": "user", "content": "hola"}]}).encode(),
                headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                         "X-OpenLegal-Causa": str(self.causa)},
            )
            respuesta = conexion.getresponse()
            self.assertEqual(respuesta.status, 200)
            self.assertIn("text/event-stream", respuesta.headers.get("Content-Type", ""))
            # El proveedor tiene el segundo trozo retenido: si el proxy juntara todo antes de
            # reenviar, esta línea no llegaría hasta que se libere (y la prueba lo notaría).
            inicio = time.monotonic()
            lineas = b""
            while b"pri" not in lineas and len(lineas.splitlines()) < 6:
                lineas += respuesta.readline()
            demora = time.monotonic() - inicio
            self.assertIn(b"event: content_block_delta", lineas)
            self.assertLess(demora, 2, "el proxy está juntando todo antes de reenviar (no es un pasamanos)")
            self.arriba.esperando.set()
            resto = respuesta.read()
        finally:
            conexion.close()
        self.assertIn(b"mera", resto)
        self.assertIn(b"event: message_stop", resto)
        filas = self.envios()
        self.assertEqual(len(filas), 1, "el envío se registra una sola vez")
        self.assertEqual(filas[0]["via"], "anthropic-compat")
        self.assertEqual(filas[0]["causa_id"], self.causa)

    def test_la_api_key_y_el_contenido_no_aparecen_en_ningun_registro_ni_salida(self):
        self.autorizar()
        secreto_del_expediente = "el actor confesó el 3 de marzo"
        with capturar_salida() as salida:
            respuesta, cuerpo = self.enviar_anthropic(secreto_del_expediente, causa=self.causa)
            estado = ia_proxy.resumen()
        self.assertEqual(respuesta.status, 200)

        # 1. Viaja aguas arriba (que es para lo único que sirve)...
        self.assertEqual(self.ultimo_pedido()["cabeceras"]["x-api-key"], CLAVE_SECRETA)
        # 2. ...y no aparece en NINGUNA columna de ningún registro, ni tampoco el contenido.
        db = self.db()
        for tabla in ("transferencias_ia", "auditoria", "notificaciones", "autorizaciones_ia"):
            for fila in db.todos(f"SELECT * FROM {tabla}"):
                for columna, valor in fila.items():
                    self.assertNotIn(CLAVE_SECRETA, str(valor), f"la clave se filtró a {tabla}.{columna}")
                    self.assertNotIn(secreto_del_expediente, str(valor), f"el contenido se filtró a {tabla}.{columna}")
        db.cerrar()
        archivo = self.base.read_bytes()
        self.assertNotIn(secreto_del_expediente.encode(), archivo, "el contenido quedó escrito en la base")
        # 3. Ni en la salida del proxy, ni en la respuesta que va al cliente, ni en el estado.
        self.assertNotIn(CLAVE_SECRETA, salida.getvalue())
        self.assertNotIn(secreto_del_expediente, salida.getvalue())
        self.assertNotIn(CLAVE_SECRETA, cuerpo.decode("utf-8"))
        self.assertEqual(estado["api_key"], "configurada")
        self.assertNotIn(CLAVE_SECRETA, json.dumps(estado, ensure_ascii=False))
        self.assertNotIn(secreto_del_expediente, json.dumps(estado, ensure_ascii=False))

    def test_el_estado_dice_las_dos_direcciones_y_cual_es_la_del_harness(self):
        estado = ia_proxy.resumen()
        puerto = estado["puerto"]
        # La del dialecto de OpenAI lleva /v1; la del harness NO: el adaptador agrega /v1/messages.
        self.assertEqual(estado["url"], f"http://127.0.0.1:{puerto}/v1")
        self.assertEqual(estado["url_anthropic"], f"http://127.0.0.1:{puerto}")
        self.assertNotIn("/v1", estado["url_anthropic"])
        self.assertNotIn("/anthropic", estado["url_anthropic"])
        self.assertEqual(estado["base_url_anthropic"], f"http://127.0.0.1:{self.arriba.puerto}{self.SUFIJO}")
        self.assertEqual(estado["via"], "openai-compat")
        self.assertEqual(estado["via_anthropic"], "anthropic-compat")
        self.assertNotIn(CLAVE_SECRETA, json.dumps(estado, ensure_ascii=False))

    def test_sin_base_url_anthropic_lo_dice_y_no_reenvia(self):
        self.autorizar()
        self.servidor.config["base_url_anthropic"] = ""
        antes = self.arriba.cuantos(self.ruta_arriba)

        respuesta, cuerpo = self.enviar_anthropic("hola", causa=self.causa)

        self.assertEqual(respuesta.status, 500)
        mensaje = json.loads(cuerpo)["error"]["message"]
        self.assertIn("base_url_anthropic", mensaje)
        self.assertIn("openlegal ia-proxy --estado", mensaje)
        self.assertEqual(self.arriba.cuantos(self.ruta_arriba), antes, "sin dirección aguas arriba no sale nada")
        fila = self.envios()[0]
        self.assertEqual(fila["bloqueado"], 1)
        self.assertEqual(fila["via"], "anthropic-compat")
        self.assertIn("base_url_anthropic", fila["motivo_bloqueo"])
        # Y el estado lo avisa antes de que nadie lo intente (con esa configuración, no con la
        # del archivo: `resumen` mira la que le pasen).
        avisos = ia_proxy.resumen({**self.config, "base_url_anthropic": ""})["avisos"]
        self.assertTrue(any("base_url_anthropic" in aviso for aviso in avisos))

    def test_un_payload_mal_formado_lo_dice(self):
        # Un cuerpo que no es JSON.
        respuesta, cuerpo = self.pedir_a_mano("/v1/messages", b"{ no es json")
        self.assertEqual(respuesta.status, 400)
        self.assertIn("no pude leer el pedido", json.loads(cuerpo)["error"]["message"])
        # Un cuerpo que es JSON pero no es un objeto.
        respuesta, cuerpo = self.pedir_a_mano("/v1/messages", b'["mensajes"]')
        self.assertEqual(respuesta.status, 400)
        self.assertIn("objeto JSON", json.loads(cuerpo)["error"]["message"])
        # Sin `messages` y con la minimización encendida: no se reenvía y queda anotado el intento.
        self.autorizar()
        antes = self.arriba.cuantos(self.ruta_arriba)
        with capturar_salida():
            respuesta, cuerpo = self.enviar_anthropic_payload({"model": "modelo-x", "max_tokens": 16}, causa=self.causa)
        self.assertEqual(respuesta.status, 422)
        self.assertIn("messages", json.loads(cuerpo)["error"]["message"])
        self.assertEqual(self.arriba.cuantos(self.ruta_arriba), antes)
        self.assertEqual(self.envios()[0]["bloqueado"], 1)
        self.assertEqual(self.envios(), [self.envios()[0]], "un solo intento anotado")

    def test_un_dialecto_desconocido_contesta_claro(self):
        """Un protocolo que este proxy no habla no se reenvía a ciegas: se dice qué sí atiende."""
        for ruta in ("/v1/complete", "/v1/responses"):
            respuesta, cuerpo = self.pedir_a_mano(ruta)
            self.assertEqual(respuesta.status, 404, ruta)
            error = json.loads(cuerpo)["error"]
            self.assertIn("/v1/chat/completions", error["message"])
            self.assertIn("/v1/messages", error["message"])
            self.assertIn("code", error, "sin ruta de mensajes, el error va en el sobre de OpenAI")
        # Lo que sí es del dialecto de Anthropic contesta en su sobre.
        respuesta, cuerpo = self.pedir_a_mano("/v1/messages/stream")
        self.assertEqual(respuesta.status, 404)
        datos = json.loads(cuerpo)
        self.assertEqual(datos["type"], "error")
        self.assertEqual(datos["error"]["type"], "not_found_error")
        self.assertIn("/v1/messages", datos["error"]["message"])
        # Y nada de eso salió aguas arriba ni quedó en el registro.
        self.assertEqual(self.arriba.cuantos("/v1/complete"), 0)
        self.assertEqual(self.arriba.cuantos("/v1/messages/stream"), 0)
        self.assertEqual(self.envios(), [])

    def test_el_error_sale_en_el_sobre_del_dialecto(self):
        anthropic = ia_proxy.error_api(403, ia_proxy.ANTHROPIC, "no va")
        self.assertEqual(anthropic["type"], "error")
        self.assertEqual(anthropic["error"]["type"], "permission_error")
        self.assertEqual(anthropic["error"]["message"], "no va")
        # El mismo mensaje, en el sobre del dialecto de OpenAI (que es el de siempre).
        openai = ia_proxy.error_api(403, ia_proxy.OPENAI, "no va", "sin_autorizacion")
        self.assertEqual(openai["error"]["type"], "sin_autorizacion")
        self.assertEqual(openai["error"]["code"], 403)

    def test_el_catalogo_va_al_dialecto_que_lo_pide(self):
        """`GET /v1/models` lo usan los dos dialectos: se reenvía al del cliente que lo pide."""
        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request("GET", "/v1/models", headers={"Accept": "application/json"})
            respuesta = conexion.getresponse()
            cuerpo = json.loads(respuesta.read())
        finally:
            conexion.close()
        self.assertEqual(respuesta.status, 200)
        self.assertEqual(cuerpo["data"][0]["id"], "modelo-falso")
        self.assertEqual(self.ultimo_pedido()["ruta"], "/v1/models")

        conexion = http.client.HTTPConnection(ia_proxy.DIRECCION, self.puerto, timeout=15)
        try:
            conexion.request(
                "GET", "/v1/models",
                headers={"Accept": "application/json", "anthropic-version": "2023-06-01"},
            )
            respuesta = conexion.getresponse()
            respuesta.read()
        finally:
            conexion.close()
        self.assertEqual(self.ultimo_pedido()["ruta"], f"{self.SUFIJO}/v1/models")
        self.assertEqual(self.ultimo_pedido()["cabeceras"]["x-api-key"], CLAVE_SECRETA)
        self.assertEqual(self.envios(), [], "consultar el catálogo no es mandar datos de ninguna causa")


if __name__ == "__main__":
    unittest.main()
