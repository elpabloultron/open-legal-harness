"""Pruebas del servidor MCP del CRM: protocolo y herramientas.

La última prueba arranca el servidor de verdad por stdio (subproceso), que es como
lo va a usar el agente: si el handshake falla, sirve de nada que las funciones
internas estén bien.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from openlegal import auth, ia_proxy, service  # noqa: E402
from openlegal.db import DB  # noqa: E402
from openlegal.mcp import HERRAMIENTAS, Contexto, responder, servir  # noqa: E402


class BaseMCP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f"sqlite:///{pathlib.Path(self.tmp.name) / 'mcp.db'}"
        self.db = DB(self.url)
        self.db.migrar()
        self.estudio = auth.crear_estudio(self.db, "Estudio MCP", modo="oficina")
        socio_id = auth.crear_usuario(self.db, self.estudio, "Sofía Soto", "socia@mcp.cl", "socio", "clave")
        abogado_id = auth.crear_usuario(self.db, self.estudio, "Ana Pérez", "ana@mcp.cl", "abogado", "clave")
        self.socio = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (socio_id,))
        self.socio.pop("password_hash", None)
        self.cliente_id = service.crear_cliente(
            self.db, self.socio, "Constructora Andes SpA", "76.543.210-3", tipo_persona="juridica"
        )
        self.causa = service.crear_causa(
            self.db, self.socio, "Pérez con Andes SpA", cliente_id=self.cliente_id,
            rol_rit="C-1234-2026", materia="laboral",
        )
        service.asignar(self.db, self.socio, self.causa, abogado_id, "responsable")
        self.ctx = Contexto(self.url, "socia@mcp.cl")
        self._contextos: list[Contexto] = []

    def contexto_extra(self, email: str) -> Contexto:
        """Un Contexto adicional (otro usuario del CRM) que se cierra al terminar.

        Ojo con el orden, que costó una corrida de CI en Windows: unittest corre los
        `addCleanup` DESPUÉS de `tearDown`, así que cerrar ahí no sirve — el temporal se
        limpia primero y en Windows eso falla con el archivo todavía abierto. Por eso los
        contextos extra se anotan acá y se cierran dentro de `tearDown`.
        """
        contexto = Contexto(self.url, email)
        self._contextos.append(contexto)
        return contexto

    def tearDown(self):
        # Primero TODO lo que tiene la base abierta (los contextos extra del MCP abren su
        # propia conexión), después se borra el temporal.
        for contexto in self._contextos:
            contexto.cerrar()
        self.ctx.cerrar()
        self.db.cerrar()
        assert self.db._cerrada, "la conexión principal quedó abierta"
        for contexto in self._contextos:
            assert contexto._db is None, "quedó un contexto del MCP con la base abierta"
        self.tmp.cleanup()

    def llamar(self, herramienta: str, **argumentos):
        solicitud = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": herramienta, "arguments": argumentos},
        }
        respuesta = responder(solicitud, self.ctx)
        resultado = respuesta["result"]
        texto = resultado["content"][0]["text"]
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            datos = texto  # los errores vuelven como texto legible, no como JSON
        return datos, resultado["isError"]


class TestProtocolo(BaseMCP):
    def test_initialize_declara_herramientas(self):
        respuesta = responder({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, self.ctx)
        resultado = respuesta["result"]
        self.assertIn("tools", resultado["capabilities"])
        self.assertEqual(resultado["serverInfo"]["name"], "open-legal-harness")
        self.assertIn("Art. 66", resultado["instructions"])

    def test_tools_list_expone_esquemas(self):
        respuesta = responder({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.ctx)
        herramientas = respuesta["result"]["tools"]
        self.assertEqual(len(herramientas), len(HERRAMIENTAS))
        nombres = {h["name"] for h in herramientas}
        for esperada in ("crm_causa_leer", "crm_plazo_crear", "crm_audiencia_crear",
                         "crm_ia_redactar", "crm_ia_registrar"):
            self.assertIn(esperada, nombres)
        for herramienta in herramientas:
            self.assertEqual(herramienta["inputSchema"]["type"], "object")
            self.assertTrue(herramienta["description"])

    def test_notificaciones_no_responden(self):
        self.assertIsNone(responder({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.ctx))
        self.assertIsNone(responder({"jsonrpc": "2.0", "method": "notifications/cancelled"}, self.ctx))

    def test_metodo_desconocido(self):
        respuesta = responder({"jsonrpc": "2.0", "id": 9, "method": "resources/list"}, self.ctx)
        self.assertEqual(respuesta["error"]["code"], -32601)


class TestHerramientas(BaseMCP):
    def test_estudio_y_busqueda(self):
        estudio, error = self.llamar("crm_estudio")
        self.assertFalse(error)
        self.assertEqual(estudio["usuario"], "Sofía Soto")
        self.assertEqual(estudio["conteos"]["causas"], 1)

        busqueda, _ = self.llamar("crm_causa_buscar", texto="andes")
        self.assertEqual(busqueda["total"], 1)
        self.assertEqual(busqueda["causas"][0]["rol_rit"], "C-1234-2026")

        vacia, _ = self.llamar("crm_causa_buscar", texto="no existe")
        self.assertEqual(vacia["total"], 0)

    def test_leer_causa_completa(self):
        plazo = service.crear_plazo(
            self.db, self.socio, self.causa, "Contestar demanda", dias=8, fecha_notificacion="2026-09-17"
        )
        service.crear_audiencia(self.db, self.socio, self.causa, "Preparatoria", "2026-10-05", "09:30")
        detalle, error = self.llamar("crm_causa_leer", causa_id=self.causa)
        self.assertFalse(error)
        self.assertEqual(detalle["causa"]["caratula"], "Pérez con Andes SpA")
        self.assertEqual(len(detalle["plazos"]), 1)
        self.assertEqual(detalle["plazos"][0]["fecha_vencimiento"], plazo["fecha_vencimiento"])
        self.assertEqual(len(detalle["audiencias"]), 1)
        self.assertEqual([f["nombre"] for f in detalle["equipo"]], ["Sofía Soto", "Ana Pérez"])

    def test_calcular_plazo_art_66(self):
        calculo, error = self.llamar("crm_plazo_calcular", notificacion="2026-09-17", dias=8)
        self.assertFalse(error)
        self.assertEqual(calculo["fecha_vencimiento"], "2026-09-29")
        self.assertFalse(calculo["feriados_pendientes_de_validacion"], "2026 ya está validado")
        self.assertEqual(calculo["advertencias"], [])

    def test_calculo_que_cruza_a_un_año_sin_validar_avisa(self):
        calculo, error = self.llamar("crm_plazo_calcular", notificacion="2026-12-30", dias=3)
        self.assertFalse(error)
        self.assertTrue(any("2027" in a for a in calculo["advertencias"]))

    def test_crear_plazo_y_verlo_en_el_listado(self):
        creado, error = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Contestar demanda",
            dias=8, notificacion="2026-09-17",
        )
        self.assertFalse(error)
        self.assertEqual(creado["fecha_vencimiento"], "2026-09-29")
        self.assertEqual(len(creado["detalle"]), 12)  # incluye feriados y domingo

        listado, _ = self.llamar("crm_plazo_listar", causa_id=self.causa)
        self.assertEqual(listado["total"], 1)

        cumplido, _ = self.llamar("crm_plazo_cumplido", plazo_id=creado["plazo_id"])
        self.assertTrue(cumplido["ok"])
        listado, _ = self.llamar("crm_plazo_listar", causa_id=self.causa)
        self.assertEqual(listado["total"], 0)

    def test_agenda_audiencia_y_documento(self):
        audiencia, error = self.llamar(
            "crm_audiencia_crear", causa_id=self.causa, tipo="Comparendo", fecha="2026-09-30",
            hora="11:00", modalidad="remota", lugar_o_url="https://zoom.us/j/1",
        )
        self.assertFalse(error)
        self.assertGreater(audiencia["audiencia_id"], 0)

        documento, _ = self.llamar(
            "crm_documento_registrar", causa_id=self.causa, nombre="demanda.pdf",
            ruta="/tmp/demanda.pdf", visibilidad="interno",
        )
        self.assertGreater(documento["documento_id"], 0)
        # lo registrado queda en la bitácora
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria")]
        self.assertIn("audiencia.crear", acciones)
        self.assertIn("documento.registrar", acciones)

    def test_minimizar_y_registrar_envio(self):
        estado, _ = self.llamar("crm_ia_estado", causa_id=self.causa)
        self.assertFalse(estado["autorizado"])
        self.assertIn("Constructora Andes SpA", estado["terminos_a_minimizar"])

        # sin autorización, el registro se niega con un error legible para el agente
        _, error = self.llamar(
            "crm_ia_registrar", causa_id=self.causa, proveedor="anthropic", texto="expediente"
        )
        self.assertTrue(error)

        service.autorizar_ia(self.db, self.socio, self.causa, titular="Representante legal")
        limpio, _ = self.llamar(
            "crm_ia_redactar", causa_id=self.causa,
            texto="Demanda de 76.543.210-3 contra Constructora Andes SpA",
        )
        self.assertNotIn("76.543.210-3", limpio["texto_minimizado"])
        self.assertEqual(len(limpio["mapa_local"]), 2)

        registro, error = self.llamar(
            "crm_ia_registrar", causa_id=self.causa, proveedor="anthropic", modelo="claude-opus-4.7",
            texto=limpio["texto_minimizado"], redactado=True,
        )
        self.assertFalse(error)
        self.assertTrue(registro["redactado"])

    def test_error_legible_cuando_la_causa_no_existe(self):
        # el agente tiene que poder leer el motivo, no un volcado técnico
        mensaje, error = self.llamar("crm_plazo_listar", causa_id=999)
        self.assertTrue(error)
        self.assertIn("Sin permiso", str(mensaje))
        self.assertIn("plazo.leer", str(mensaje))


class TestServidorRealPorStdio(BaseMCP):
    def test_handshake_y_creacion_de_plazo(self):
        """Arranca `openlegal mcp` de verdad y le habla como le hablaría el agente."""
        # El entorno del padre, como lo pasa el harness: ambiente heredado y sólo las
        # variables del MCP por encima. Antes acá se armaba un entorno MÍNIMO con un PATH
        # de Linux, y en Windows el hijo no arrancaba (la prueba fallaba sólo en 3.10 con
        # un JSON vacío, sin decir por qué).
        entorno = {
            **os.environ,
            "OPENLEGAL_MCP_DB": self.url,
            "OPENLEGAL_MCP_USUARIO": "socia@mcp.cl",
            "PYTHONPATH": str(RAIZ / "src"),
            # El servidor imprime español: sin esto, en Windows la salida va en cp1252.
            "PYTHONIOENCODING": "utf-8",
        }
        proceso = subprocess.Popen(
            [sys.executable, "-m", "openlegal.cli", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # La codificación se declara: en Windows el valor por defecto es cp1252 y el
            # servidor imprime UTF-8 (acentos, comillas), así que sin esto el propio
            # diagnóstico moría con UnicodeDecodeError en vez de mostrar la causa.
            text=True, encoding="utf-8", errors="replace", cwd=RAIZ, env=entorno,
        )
        try:
            entrada, salida, errores = proceso.stdin, proceso.stdout, proceso.stderr
            assert entrada is not None and salida is not None and errores is not None

            def pedir(solicitud: dict) -> dict:
                entrada.write(json.dumps(solicitud) + "\n")
                entrada.flush()
                linea = salida.readline()
                if not linea:
                    # Sin salida: lo más probable es que el hijo no haya arrancado. Hay
                    # que decir por qué en vez de morir con un JSON vacío.
                    codigo = proceso.poll()
                    detalle = errores.read() if codigo is not None else "(sigue vivo)"
                    raise AssertionError(
                        f"el servidor MCP no respondió (código de salida: {codigo}); "
                        f"stderr del hijo:\n{detalle}"
                    )
                return json.loads(linea)

            inicio = pedir({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(inicio["result"]["serverInfo"]["name"], "open-legal-harness")

            listado = pedir({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertGreaterEqual(len(listado["result"]["tools"]), 12)

            creacion = pedir({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "crm_plazo_crear",
                    "arguments": {
                        "causa_id": self.causa, "descripcion": "Contestar demanda",
                        "dias": 8, "notificacion": "2026-09-17",
                    },
                },
            })
            resultado = json.loads(creacion["result"]["content"][0]["text"])
            self.assertFalse(creacion["result"]["isError"])
            self.assertEqual(resultado["fecha_vencimiento"], "2026-09-29")

            # y quedó guardado en la base
            guardado = self.db.uno(
                "SELECT descripcion, fecha_vencimiento FROM plazos WHERE causa_id = ?", (self.causa,)
            )
            self.assertEqual(guardado["fecha_vencimiento"], "2026-09-29")
        finally:
            # Cerrar TODO lo del subproceso: si queda un descriptor abierto, en Windows
            # el temporal de la prueba no se puede borrar (un archivo abierto no se
            # borra) y la prueba falla en la limpieza, no en la afirmación.
            proceso.terminate()
            proceso.wait(timeout=10)
            for flujo in (proceso.stdin, proceso.stdout, proceso.stderr):
                if flujo is not None:
                    flujo.close()


class TestRectificarPlazo(BaseMCP):
    """Lo que destapó la prueba real: el agente quedó con un plazo cargado con el OCR
    equivocado y no tenía ninguna herramienta para corregirlo. Ahora tiene dos, y
    ninguna borra el rastro (un plazo fatal rectificado tiene que ser explicable).
    """

    def test_actualizar_recalcula_y_audita_el_motivo(self):
        creado, error = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Contestar demanda",
            dias=8, notificacion="2026-09-17",
        )
        self.assertFalse(error)
        self.assertEqual(creado["fecha_vencimiento"], "2026-09-29")
        plazo_id = creado["plazo_id"]

        actualizado, error = self.llamar(
            "crm_plazo_actualizar", plazo_id=plazo_id, dias=10,
            motivo="el proveído confiere traslado de diez días hábiles, no ocho",
        )
        self.assertFalse(error)
        self.assertEqual(actualizado["fecha_vencimiento"], "2026-10-01")
        self.assertIn("dias", actualizado["campos_actualizados"])
        self.assertTrue(any("feriado" in str(d).lower() for d in actualizado["detalle"]))

        fila = self.db.uno("SELECT dias, fecha_vencimiento FROM plazos WHERE id = ?", (plazo_id,))
        self.assertEqual((fila["dias"], fila["fecha_vencimiento"]), (10, "2026-10-01"))

        evento = self.db.uno(
            "SELECT accion, detalle FROM auditoria WHERE entidad = 'plazos' AND entidad_id = ? "
            "ORDER BY id DESC",
            (plazo_id,),
        )
        self.assertEqual(evento["accion"], "plazo.editar")
        self.assertIn("diez días", evento["detalle"])

    def test_cancelar_no_borra_la_fila(self):
        creado, _ = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Plazo duplicado",
            dias=8, notificacion="2026-09-17",
        )
        cancelado, error = self.llamar(
            "crm_plazo_cancelar", plazo_id=creado["plazo_id"], motivo="duplicado del plazo 1"
        )
        self.assertFalse(error)
        self.assertTrue(cancelado["ok"])
        fila = self.db.uno("SELECT estado FROM plazos WHERE id = ?", (creado["plazo_id"],))
        self.assertEqual(fila["estado"], "cancelado")

    def test_un_rol_sin_permiso_no_puede_rectificar(self):
        creado, _ = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Contestar demanda",
            dias=8, notificacion="2026-09-17",
        )
        secretaria_id = auth.crear_usuario(
            self.db, self.estudio, "Carmen Díaz", "carmen@mcp.cl", "administrativo", "clave"
        )
        service.asignar(self.db, self.socio, self.causa, secretaria_id, "paralegal")
        ctx_secretaria = self.contexto_extra("carmen@mcp.cl")
        solicitud = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "crm_plazo_cancelar",
                "arguments": {"plazo_id": creado["plazo_id"], "motivo": "no me corresponde"},
            },
        }
        respuesta = responder(solicitud, ctx_secretaria)
        self.assertTrue(respuesta["result"]["isError"])
        fila = self.db.uno("SELECT estado FROM plazos WHERE id = ?", (creado["plazo_id"],))
        self.assertEqual(fila["estado"], "pendiente")

    def test_las_herramientas_nuevas_se_anuncian_al_agente(self):
        nombres = {h["name"] for h in HERRAMIENTAS}
        self.assertIn("crm_plazo_actualizar", nombres)
        self.assertIn("crm_plazo_cancelar", nombres)


class TestErroresDeArgumento(BaseMCP):
    """Un argumento malo no puede tumbar el CRM ni dejar al agente a ciegas.

    El agente lee el texto del error: si dice «ValueError: Invalid isoformat
    string» no aprende nada, y si la excepción se escapa del bucle, el agente se
    queda sin CRM a mitad de sesión. Las dos cosas están cubiertas aquí.
    """

    def test_fecha_mal_formada_explica_el_formato(self):
        datos, error = self.llamar("crm_plazo_calcular", notificacion="ayer", dias=8)
        self.assertTrue(error)
        self.assertIn("YYYY-MM-DD", str(datos))
        self.assertIn("ayer", str(datos))

    def test_dias_no_numericos(self):
        datos, error = self.llamar("crm_plazo_calcular", notificacion="2026-09-17", dias="ocho")
        self.assertTrue(error)
        self.assertIn("entero", str(datos))

    def test_dias_cero_se_rechaza(self):
        datos, error = self.llamar("crm_plazo_calcular", notificacion="2026-09-17", dias=0)
        self.assertTrue(error)
        self.assertIn("mayor o igual a 1", str(datos))

    def test_descripcion_vacia_no_crea_nada(self):
        datos, error = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="   ", dias=8,
            notificacion="2026-09-17",
        )
        self.assertTrue(error)
        self.assertIn("descripcion", str(datos))
        self.assertEqual(self.db.uno("SELECT COUNT(*) AS n FROM plazos")["n"], 0)

    def test_rectificar_sin_motivo_se_rechaza_y_no_cambia_nada(self):
        creado, _ = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Contestar demanda",
            dias=8, notificacion="2026-09-17",
        )
        datos, error = self.llamar("crm_plazo_cancelar", plazo_id=creado["plazo_id"])
        self.assertTrue(error)
        self.assertIn("motivo", str(datos))
        fila = self.db.uno("SELECT estado FROM plazos WHERE id = ?", (creado["plazo_id"],))
        self.assertEqual(fila["estado"], "pendiente")

    def test_el_bucle_sobrevive_a_peticiones_malas(self):
        import io

        entrada = io.StringIO("\n".join([
            "esto no es json",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "crm_no_existe", "arguments": {}}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "crm_plazo_calcular",
                                   "arguments": {"notificacion": {"a": 1}, "dias": 8}}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        ]) + "\n")
        salida = io.StringIO()
        servir(entrada, salida, self.ctx)

        respuestas = [json.loads(linea) for linea in salida.getvalue().strip().splitlines()]
        self.assertEqual(len(respuestas), 4, "el bucle tiene que contestar las cuatro y seguir vivo")
        self.assertEqual(respuestas[-1]["id"], 3)
        self.assertIn("tools", respuestas[-1]["result"])


class TestAuditoria(BaseMCP):
    """La procedencia tiene que ser consultable, no adivinable.

    El agente ya atribuyó dos veces un plazo viejo al OCR de la sesión. La
    bitácora tiene el dato; ahora tiene herramienta para leerlo.
    """

    def test_la_bitacora_dice_quien_creo_el_plazo_y_cuando(self):
        creado, _ = self.llamar(
            "crm_plazo_crear", causa_id=self.causa, descripcion="Contestar demanda",
            dias=8, notificacion="2026-09-17",
        )
        datos, error = self.llamar(
            "crm_auditoria_leer", entidad="plazos", entidad_id=creado["plazo_id"]
        )
        self.assertFalse(error)
        self.assertEqual(datos["total"], 1)
        self.assertEqual(datos["creado_por"], "Sofía Soto")
        self.assertEqual(datos["eventos"][0]["accion"], "plazo.crear")
        self.assertTrue(datos["creado_en"])

        self.llamar("crm_plazo_cancelar", plazo_id=creado["plazo_id"], motivo="duplicado")
        completa, _ = self.llamar(
            "crm_auditoria_leer", entidad="plazos", entidad_id=creado["plazo_id"]
        )
        self.assertEqual([e["accion"] for e in completa["eventos"]], ["plazo.crear", "plazo.cancelar"])
        self.assertIn("duplicado", completa["eventos"][1]["detalle"])

    def test_registro_sin_bitacora_lo_dice_en_vez_de_inventar(self):
        datos, error = self.llamar("crm_auditoria_leer", entidad="plazos", entidad_id=999)
        self.assertFalse(error)
        self.assertEqual(datos["total"], 0)
        self.assertIn("no lo atribuyas", datos["aviso"].lower())

    def test_un_rol_sin_permiso_no_lee_la_bitacora_de_la_ia(self):
        secretaria_id = auth.crear_usuario(
            self.db, self.estudio, "Carmen Díaz", "carmen2@mcp.cl", "administrativo", "clave"
        )
        service.asignar(self.db, self.socio, self.causa, secretaria_id, "paralegal")
        contexto = self.contexto_extra("carmen2@mcp.cl")
        respuesta = responder({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "crm_auditoria_leer",
                       "arguments": {"entidad": "transferencias_ia", "entidad_id": 1}},
        }, contexto)
        self.assertTrue(respuesta["result"]["isError"])
        self.assertIn("auditoria.leer", respuesta["result"]["content"][0]["text"])

    def test_la_herramienta_se_anuncia(self):
        self.assertIn("crm_auditoria_leer", {h["name"] for h in HERRAMIENTAS})


class TestAudienciaRectificable(BaseMCP):
    """El agente quedó con una audiencia duplicada y no había forma de corregirla ni
    cancelarla: solo crear. Ahora hay dos herramientas, con el mismo criterio que los
    plazos — se rectifica, no se duplica, y nada se borra.
    """

    def test_actualizar_corrige_y_audita(self):
        audiencia_id = service.crear_audiencia(
            self.db, self.socio, self.causa, "Audiencia preparatoria", "2026-10-05", "09:30",
            modalidad="remota", lugar_o_url="https://zoom.us/j/1",
        )
        resultado, error = self.llamar(
            "crm_audiencia_actualizar", audiencia_id=audiencia_id, fecha="2026-10-07",
            hora="10:00", motivo="el tribunal reprogramó la audiencia",
        )
        self.assertFalse(error)
        self.assertEqual(resultado["campos_actualizados"], ["fecha", "hora"])

        fila = self.db.uno("SELECT fecha, hora FROM audiencias WHERE id = ?", (audiencia_id,))
        self.assertEqual((fila["fecha"], fila["hora"]), ("2026-10-07", "10:00"))

        evento = self.db.uno(
            "SELECT accion, detalle FROM auditoria WHERE entidad = 'audiencias' AND entidad_id = ? "
            "ORDER BY id DESC",
            (audiencia_id,),
        )
        self.assertEqual(evento["accion"], "audiencia.editar")
        self.assertIn("reprogramó", evento["detalle"])

    def test_cancelar_deja_la_fila_con_estado_cancelada(self):
        audiencia_id = service.crear_audiencia(
            self.db, self.socio, self.causa, "Audiencia preparatoria", "2026-10-05", "09:30",
        )
        resultado, error = self.llamar(
            "crm_audiencia_cancelar", audiencia_id=audiencia_id, motivo="duplicado de la id 1"
        )
        self.assertFalse(error)
        self.assertTrue(resultado["ok"])
        fila = self.db.uno("SELECT estado FROM audiencias WHERE id = ?", (audiencia_id,))
        self.assertEqual(fila["estado"], "cancelada")

    def test_motivo_obligatorio_en_ambas(self):
        audiencia_id = service.crear_audiencia(
            self.db, self.socio, self.causa, "Audiencia preparatoria", "2026-10-05",
        )
        for herramienta, argumentos in (
            ("crm_audiencia_actualizar", {"audiencia_id": audiencia_id, "fecha": "2026-10-07"}),
            ("crm_audiencia_cancelar", {"audiencia_id": audiencia_id}),
        ):
            datos, error = self.llamar(herramienta, **argumentos)
            self.assertTrue(error, f"{herramienta} debería exigir motivo")
            self.assertIn("motivo", str(datos))
        fila = self.db.uno("SELECT fecha, estado FROM audiencias WHERE id = ?", (audiencia_id,))
        self.assertEqual((fila["fecha"], fila["estado"]), ("2026-10-05", "programada"))

    def test_las_herramientas_se_anuncian(self):
        nombres = {h["name"] for h in HERRAMIENTAS}
        self.assertIn("crm_audiencia_actualizar", nombres)
        self.assertIn("crm_audiencia_cancelar", nombres)


class TestEnviosDeIA(BaseMCP):
    """`crm_envios_ia`: la prueba de licitud, con metadatos y sin contenido.

    Lo que el agente NO puede hacer con esto es leer lo que se mandó —no se guarda—, y eso
    tiene que quedar claro en la descripción de la herramienta: un modelo que crea que puede
    recuperar el texto inventaría una respuesta.
    """

    def _envio(self, causa=None, texto="texto del expediente", bloqueado=False, proveedor="deepseek",
               origen="proxy", estudio=None):
        registro = ia_proxy.registrar(
            self.db, proveedor=proveedor, modelo="deepseek-chat", destino_pais="China",
            texto=texto, causa_id=causa, estudio_id=self.estudio if estudio is None else estudio,
            bloqueado=bloqueado, motivo_bloqueo="la causa no tiene autorización vigente de IA" if bloqueado else None,
        )
        if origen != "proxy":
            self.db.ejecutar("UPDATE transferencias_ia SET origen = ? WHERE id = ?", (origen, registro["id"]))
        return registro

    def test_lista_los_envios_con_sus_metadatos(self):
        self._envio(causa=self.causa, texto="una demanda entera")
        datos, error = self.llamar("crm_envios_ia")

        self.assertFalse(error)
        self.assertEqual(datos["total"], 1)
        envio = datos["envios"][0]
        self.assertEqual(envio["proveedor"], "deepseek")
        self.assertEqual(envio["modelo"], "deepseek-chat")
        self.assertEqual(envio["destino_pais"], "China")
        self.assertEqual(envio["caracteres"], len("una demanda entera"))
        self.assertEqual(envio["causa_id"], self.causa)
        self.assertIn("caratula", envio)
        self.assertIn("metadatos", datos["aviso"])

    def test_no_devuelve_el_contenido(self):
        secreto = "el actor reconoció la deuda el 3 de marzo"
        self._envio(causa=self.causa, texto=secreto)

        datos, _ = self.llamar("crm_envios_ia")

        self.assertNotIn(secreto, json.dumps(datos, ensure_ascii=False))
        # Ninguna columna del registro guarda el texto: son todas metadatos.
        self.assertEqual(
            set(datos["envios"][0]),
            {
                "id", "causa_id", "autorizacion_id", "origen", "via", "proveedor", "modelo",
                "destino_pais", "caracteres", "hash_payload", "redactado", "bloqueado",
                "motivo_bloqueo", "creado_en", "caratula", "usuario",
            },
        )

    def test_filtra_por_causa_y_por_bloqueados(self):
        otra_causa = service.crear_causa(self.db, self.socio, "Otra causa", cliente_id=self.cliente_id)
        self._envio(causa=self.causa)
        self._envio(causa=otra_causa)
        self._envio(causa=self.causa, bloqueado=True)

        solo_una, _ = self.llamar("crm_envios_ia", causa_id=self.causa)
        bloqueados, _ = self.llamar("crm_envios_ia", bloqueados=True)
        todas, _ = self.llamar("crm_envios_ia")

        self.assertEqual(solo_una["total"], 2)
        self.assertEqual(bloqueados["total"], 1)
        self.assertEqual(bloqueados["envios"][0]["bloqueado"], 1)
        self.assertIn("autorización", bloqueados["envios"][0]["motivo_bloqueo"])
        self.assertEqual(todas["total"], 3)

    def test_los_envios_sin_causa_aparecen_sin_inventar_una_causa(self):
        self._envio(causa=None)

        datos, error = self.llamar("crm_envios_ia")

        self.assertFalse(error)
        self.assertEqual(datos["total"], 1)
        self.assertIsNone(datos["envios"][0]["causa_id"])
        self.assertIsNone(datos["envios"][0]["caratula"])

    def test_un_rol_sin_permiso_no_los_ve(self):
        self._envio(causa=self.causa)
        secretaria_id = auth.crear_usuario(
            self.db, self.estudio, "Carmen Díaz", "carmen@mcp.cl", "administrativo", "clave"
        )
        service.asignar(self.db, self.socio, self.causa, secretaria_id, "apoyo")
        contexto = self.contexto_extra("carmen@mcp.cl")

        respuesta = responder({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "crm_envios_ia", "arguments": {}},
        }, contexto)

        self.assertTrue(respuesta["result"]["isError"])
        self.assertIn("ia.leer", respuesta["result"]["content"][0]["text"])

    def test_la_herramienta_se_anuncia_con_sus_reglas(self):
        por_nombre = {h["name"]: h for h in HERRAMIENTAS}
        self.assertIn("crm_envios_ia", por_nombre)
        descripcion = por_nombre["crm_envios_ia"]["description"]
        # Lo que el agente no puede deducir solo: que son metadatos, que el contenido no se
        # guarda, y para qué sirve (poder demostrar el tratamiento).
        self.assertIn("METADATOS", descripcion)
        self.assertIn("NO se guarda", descripcion)
        self.assertIn("licitud", descripcion)
        self.assertIn("hash", descripcion.lower())
        self.assertEqual(por_nombre["crm_envios_ia"]["inputSchema"]["type"], "object")


if __name__ == "__main__":
    unittest.main()


class TestElAgenteCargaLaCausaCompleta(BaseMCP):
    """El escenario que pidió el estudio: dictarle la causa al agente y que la cargue.

    Hasta hace poco el agente podía cargar plazos y audiencias, pero no crear el cliente ni
    la causa: el alta había que hacerla a mano. Estas pruebas fijan ese camino, que es el
    que hace que la frase «tengo esta causa 00001, ingresá los datos» funcione de verdad.
    """

    def test_busca_antes_de_crear_para_no_duplicar(self):
        datos, error = self.llamar("crm_cliente_buscar", texto="Andes")
        self.assertFalse(error)
        self.assertEqual(datos["total"], 1)
        self.assertEqual(datos["clientes"][0]["nombre"], "Constructora Andes SpA")
        self.assertTrue(datos["clientes"][0]["rut_valido"])

    def test_crea_el_cliente_con_sus_datos_y_su_estudio(self):
        datos, error = self.llamar(
            "crm_cliente_crear", nombre="Lucía Herrera Vargas", rut="11.111.111-1",
            email="lucia@ejemplo.cl", telefono="+56 9 8765 4321", direccion="Providencia 1234",
        )
        self.assertFalse(error)
        self.assertIsNone(datos["aviso"])
        fila = self.db.uno("SELECT * FROM clientes WHERE id = ?", (datos["cliente_id"],))
        self.assertEqual(fila["telefono"], "+56 9 8765 4321")
        self.assertEqual(fila["estudio_id"], self.estudio)

    def test_avisa_si_el_rut_no_cuadra_pero_lo_guarda_igual(self):
        datos, error = self.llamar("crm_cliente_crear", nombre="Cliente con RUT raro", rut="12.345.678-0")
        self.assertFalse(error)
        self.assertIn("no cuadra", datos["aviso"])
        self.assertIsNotNone(
            self.db.uno("SELECT id FROM clientes WHERE id = ?", (datos["cliente_id"],))
        )

    def test_una_persona_juridica_sin_representante_no_se_acepta(self):
        datos, error = self.llamar("crm_cliente_crear", nombre="SpA Sin Nadie", tipo_persona="juridica")
        self.assertTrue(error)
        self.assertIn("representante_legal", datos)

    def test_carga_la_causa_los_plazos_la_audiencia_y_despues_la_lee(self):
        cliente, _ = self.llamar("crm_cliente_crear", nombre="Lucía Herrera", rut="11.111.111-1")
        causa, error = self.llamar(
            "crm_causa_crear", caratula="Herrera con Fondo del Norte",
            cliente_id=cliente["cliente_id"], rol_rit="C-00001-2026",
            tribunal="1° Juzgado Civil de Santiago", materia="civil", cuantia_clp=12500000,
        )
        self.assertFalse(error)
        self.assertIn("Art. 66", causa["siguiente_paso"])

        plazo, error = self.llamar(
            "crm_plazo_crear", causa_id=causa["causa_id"], descripcion="Contestar traslado",
            dias=10, notificacion="2026-09-15",
        )
        self.assertFalse(error)
        self.assertEqual(plazo["fecha_vencimiento"], "2026-09-29")  # 18 y 19 de septiembre son feriados

        _, error = self.llamar(
            "crm_audiencia_crear", causa_id=causa["causa_id"], tipo="Audiencia preparatoria",
            fecha="2026-10-05", hora="09:00", modalidad="presencial",
        )
        self.assertFalse(error)

        agenda, error = self.llamar("crm_agenda", dias=3650)
        self.assertFalse(error)
        self.assertEqual(agenda["total"], 1)
        self.assertEqual(agenda["audiencias"][0]["rol_rit"], "C-00001-2026")

        lectura, error = self.llamar("crm_causa_leer", causa_id=causa["causa_id"])
        self.assertFalse(error)
        self.assertEqual(len(lectura["plazos"]), 1)
        self.assertEqual(len(lectura["audiencias"]), 1)
        self.assertEqual(lectura["causa"]["cliente_id"], cliente["cliente_id"])

    def test_el_agente_no_crea_clientes_si_su_rol_no_puede(self):
        auth.crear_usuario(self.db, self.estudio, "Paula Paralegal", "paula@mcp.cl", "paralegal", "clave")
        contexto = self.contexto_extra("paula@mcp.cl")
        solicitud = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "crm_cliente_crear", "arguments": {"nombre": "No Puede"}},
        }
        resultado = responder(solicitud, contexto)["result"]
        self.assertTrue(resultado["isError"])
        self.assertIn("permiso", resultado["content"][0]["text"])
        self.assertIsNone(
            self.db.uno("SELECT id FROM clientes WHERE nombre = ?", ("No Puede",))
        )

    def test_no_se_cuelga_una_causa_de_un_cliente_de_otro_estudio(self):
        otro = auth.crear_estudio(self.db, "Otro Estudio", modo="oficina")
        otro_socio_id = auth.crear_usuario(self.db, otro, "Otro Socio", "otro@mcp.cl", "socio", "clave")
        otro_socio = self.db.uno("SELECT * FROM usuarios WHERE id = ?", (otro_socio_id,))
        otro_socio.pop("password_hash", None)
        ajeno = service.crear_cliente(self.db, otro_socio, "Cliente Ajeno")

        datos, error = self.llamar("crm_causa_crear", caratula="Intento cruzado", cliente_id=ajeno)
        self.assertTrue(error)
        self.assertIn("no hay cliente", datos)
        self.assertIsNone(
            self.db.uno("SELECT id FROM causas WHERE caratula = ?", ("Intento cruzado",))
        )


class TestHonorariosYPagosPorMCP(BaseMCP):
    """El agente carga la plata de la causa y lee la cuenta de dividendos.

    Dos reglas que la herramienta tiene que sostener por sí sola: la retención la declara el
    estudio (el agente no la inventa ni la calcula) y un pago imputado al honorario de otra
    causa se rechaza — imputarlo descuadraría dos cuentas a la vez.
    """

    def test_registra_los_tres_y_lee_la_cuenta(self):
        honorario, error = self.llamar(
            "crm_honorario_registrar", causa_id=self.causa, modalidad="fijo", monto_pactado=350000,
            descripcion="Demanda civil", monto_bruto=350000, retencion_sii=35000,
        )
        self.assertFalse(error)
        self.assertEqual(honorario["honorario"]["monto_liquido"], 315000)
        self.assertEqual(honorario["honorario"]["estado_pago"], "pendiente")
        self.assertIsNone(honorario["aviso"], "la retención venía declarada por el estudio")

        gasto, error = self.llamar(
            "crm_gasto_registrar", causa_id=self.causa, concepto="Notaría 45", monto=25000,
            comprobante="boleta 45", fecha="2026-09-16",
        )
        self.assertFalse(error)
        self.assertEqual(gasto["gasto"]["monto"], 25000)
        self.assertIsNone(gasto["aviso"])

        pago, error = self.llamar(
            "crm_pago_registrar", causa_id=self.causa, monto=200000,
            honorario_id=honorario["honorario_id"], referencia="transferencia 8812", fecha="2026-09-18",
        )
        self.assertFalse(error)
        self.assertEqual(pago["honorario"]["estado_pago"], "parcial")
        self.assertEqual(pago["saldo_de_la_causa"], 140000)     # 315.000 + 25.000 - 200.000
        self.assertIn("bitácora", pago["recordatorio"])

        cuenta, error = self.llamar("crm_cuenta_dividendos", causa_id=self.causa)
        self.assertFalse(error)
        self.assertEqual(cuenta["estudio"]["nombre"], "Estudio MCP")
        self.assertEqual(cuenta["cliente"]["nombre"], "Constructora Andes SpA")
        self.assertEqual(cuenta["causa"]["caratula"], "Pérez con Andes SpA")
        self.assertEqual(cuenta["totales"], {
            "honorarios_pactado": 350000, "honorarios_liquido": 315000, "honorarios_pagado": 200000,
            "gastos": 25000, "gastos_por_cuenta_del_cliente": 25000, "pagos": 200000, "saldo": 140000,
        })
        self.assertEqual(len(cuenta["honorarios"]), 1)
        self.assertEqual(len(cuenta["gastos"]), 1)
        self.assertEqual(len(cuenta["pagos"]), 1)

        # Y todo quedó en la bitácora, con quién lo hizo.
        acciones = [f["accion"] for f in self.db.todos("SELECT accion FROM auditoria")]
        self.assertIn("honorario.crear", acciones)
        self.assertIn("gasto.crear", acciones)
        self.assertIn("pago.crear", acciones)

    def test_sin_retencion_declarada_el_agente_recibe_el_aviso(self):
        honorario, error = self.llamar(
            "crm_honorario_registrar", causa_id=self.causa, modalidad="hora", monto_bruto=120000
        )
        self.assertFalse(error)
        self.assertEqual(honorario["honorario"]["monto_liquido"], 120000)
        self.assertIn("no se declaró retención", honorario["aviso"])
        self.assertIn("retencion_sii", honorario["aviso"], "el aviso dice dónde va la tasa")

        cuenta, _ = self.llamar("crm_cuenta_dividendos", causa_id=self.causa)
        self.assertTrue(any("no se declaró retención" in a for a in cuenta["advertencias"]))

    def test_el_pago_deja_el_honorario_en_parcial_y_despues_en_pagado(self):
        honorario, _ = self.llamar(
            "crm_honorario_registrar", causa_id=self.causa, monto_pactado=100000, monto_bruto=100000
        )
        primero, error = self.llamar(
            "crm_pago_registrar", causa_id=self.causa, monto=40000, honorario_id=honorario["honorario_id"],
            medio="efectivo",
        )
        self.assertFalse(error)
        self.assertEqual(primero["honorario"]["estado_pago"], "parcial")

        segundo, error = self.llamar(
            "crm_pago_registrar", causa_id=self.causa, monto=60000, honorario_id=honorario["honorario_id"]
        )
        self.assertFalse(error)
        self.assertEqual(segundo["honorario"]["estado_pago"], "pagado")
        self.assertEqual(segundo["honorario"]["monto_pagado"], 100000)

    def test_un_pago_al_honorario_de_otra_causa_se_rechaza_y_no_se_guarda(self):
        otra = service.crear_causa(self.db, self.socio, "Otra causa del estudio", cliente_id=self.cliente_id)
        honorario, _ = self.llamar("crm_honorario_registrar", causa_id=self.causa, monto_pactado=100000)

        datos, error = self.llamar(
            "crm_pago_registrar", causa_id=otra, monto=50000, honorario_id=honorario["honorario_id"]
        )
        self.assertTrue(error)
        self.assertIn("no de la causa", str(datos))
        self.assertEqual(self.db.uno("SELECT COUNT(*) AS n FROM pagos")["n"], 0)

    def test_argumentos_mal_formados_vuelven_como_error_accionable(self):
        for herramienta, argumentos, esperado in (
            ("crm_pago_registrar", {"causa_id": self.causa, "monto": "doscientos"}, "entero"),
            ("crm_pago_registrar", {"causa_id": self.causa, "monto": 0}, "mayor o igual a 1"),
            ("crm_pago_registrar", {"causa_id": self.causa, "monto": 5000, "medio": "bitcoin"}, "medio"),
            ("crm_gasto_registrar", {"causa_id": self.causa, "concepto": "   ", "monto": 1000}, "concepto"),
            ("crm_honorario_registrar", {"causa_id": self.causa, "modalidad": "por-las-ganancias"}, "modalidad"),
            ("crm_honorario_registrar", {"causa_id": self.causa}, "monto_pactado"),
            ("crm_cuenta_dividendos", {"causa_id": "la de Herrera"}, "causa_id"),
        ):
            datos, error = self.llamar(herramienta, **argumentos)
            self.assertTrue(error, f"{herramienta} con {argumentos} debería fallar")
            self.assertIn(esperado, str(datos))

        # Nada se escribió y el CRM sigue disponible: un argumento malo no tumba la conexión.
        self.assertEqual(self.db.uno("SELECT COUNT(*) AS n FROM pagos")["n"], 0)
        self.assertEqual(self.db.uno("SELECT COUNT(*) AS n FROM honorarios")["n"], 0)
        estudio, error = self.llamar("crm_estudio")
        self.assertFalse(error)
        self.assertEqual(estudio["rol"], "socio")

    def test_un_paralegal_no_ve_la_cuenta(self):
        paralegal_id = auth.crear_usuario(
            self.db, self.estudio, "Paula Paralegal", "paula@mcp.cl", "paralegal", "clave"
        )
        service.asignar(self.db, self.socio, self.causa, paralegal_id, "apoyo")
        contexto = self.contexto_extra("paula@mcp.cl")

        respuesta = responder({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "crm_cuenta_dividendos", "arguments": {"causa_id": self.causa}},
        }, contexto)
        self.assertTrue(respuesta["result"]["isError"])
        self.assertIn("honorario.leer", respuesta["result"]["content"][0]["text"])

    def test_un_abogado_no_registra_honorarios_por_el_mcp(self):
        contexto = self.contexto_extra("ana@mcp.cl")
        respuesta = responder({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "crm_honorario_registrar",
                "arguments": {"causa_id": self.causa, "monto_pactado": 100000},
            },
        }, contexto)
        self.assertTrue(respuesta["result"]["isError"])
        self.assertIn("honorario.editar", respuesta["result"]["content"][0]["text"])
        self.assertEqual(self.db.uno("SELECT COUNT(*) AS n FROM honorarios")["n"], 0)

    def test_las_cuatro_herramientas_se_anuncian_con_sus_reglas(self):
        por_nombre = {h["name"]: h for h in HERRAMIENTAS}
        for nombre in (
            "crm_honorario_registrar", "crm_gasto_registrar", "crm_pago_registrar", "crm_cuenta_dividendos",
        ):
            self.assertIn(nombre, por_nombre)
            self.assertEqual(por_nombre[nombre]["inputSchema"]["type"], "object")

        # Lo que el agente no puede deducir solo tiene que estar escrito en la descripción.
        self.assertIn("CLP", por_nombre["crm_honorario_registrar"]["description"])
        self.assertIn("retención", por_nombre["crm_honorario_registrar"]["description"])
        self.assertIn("SII", por_nombre["crm_honorario_registrar"]["description"])
        self.assertIn("consecuencias", por_nombre["crm_pago_registrar"]["description"])
        self.assertIn("confirma", por_nombre["crm_pago_registrar"]["description"])
        self.assertIn("sólo lectura", por_nombre["crm_cuenta_dividendos"]["description"])
        self.assertEqual(por_nombre["crm_pago_registrar"]["inputSchema"]["required"], ["causa_id", "monto"])
        self.assertEqual(por_nombre["crm_cuenta_dividendos"]["inputSchema"]["required"], ["causa_id"])
