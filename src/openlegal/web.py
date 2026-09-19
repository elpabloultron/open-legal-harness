"""Servicio web local del CRM: la cara que se incrusta en el sidebar del harness.

Todo local por diseño (Ley 21.719 / privacidad del cliente): escucha solo en
127.0.0.1, no llama a ningún servicio externo, y la base vive en el disco del
estudio. Dos formas de entrar:

  1. Sesión de usuario (recomendado en oficina): correo + contraseña, con la
     tabla `sesiones` y expiración. Cada quien ve lo suyo: el abogado solo sus
     causas asignadas, la secretaria la agenda y los clientes, el socio todo.
  2. Token del panel (modo abogado solo): la URL que imprime `openlegal serve`
     abre el panel como el usuario activo, sin login.

Nunca se sirve información de un estudio distinto, y toda acción queda en
`auditoria` (quién, qué y cuándo), que es la evidencia de trazabilidad que la
ley espera poder demostrar.

Endpoints:
  GET  /                     panel HTML (token o sesión)
  POST /api/login            entra con correo y contraseña
  POST /api/logout           cierra la sesión
  GET  /api/sesion           quién soy, en qué estudio y qué módulos me tocan
  GET  /api/estado           estudio, usuario y conteos
  GET  /api/causas           causas visibles para el usuario       POST /api/causas
  GET  /api/plazos?dias=N    próximos vencimientos                 POST /api/plazos
  POST /api/plazos/{id}/cumplido
  GET  /api/agenda?dias=N    audiencias próximas                   POST /api/audiencias
  GET  /api/clientes?q=      clientes y búsqueda                   POST /api/clientes
  GET  /api/panel            KPIs del estudio
  GET  /api/calculo?notificacion=YYYY-MM-DD&dias=N   cómputo Art. 66 CPC

Módulos (lo mismo que la terminal, sin terminal):

  Avisos     GET  /api/avisos          GET /api/avisos (canales, cola, estado)
             POST /api/avisos/config   guarda correo y SMS (las claves nunca vuelven)
             POST /api/avisos/probar   manda un aviso de prueba al propio usuario
             POST /api/avisos/generar  encola recordatorios de plazos y audiencias
             POST /api/avisos/despachar  saca la cola
  Usuarios   GET  /api/usuarios        POST /api/usuarios        POST /api/usuarios/{id}
  Seguridad  GET  /api/seguridad       POST /api/seguridad/desbloquear
             POST /api/seguridad/2fa/preparar | /confirmar | /apagar
  Retención  GET  /api/retencion       POST /api/retencion       POST /api/retencion/ejecutar
  Titulares  GET  /api/titulares       POST /api/titulares/exportar | /anonimizar
  Honorarios GET  /api/cuenta?causa=N  cuenta de dividendos (formato=html: imprimible)
             POST /api/honorarios       POST /api/gastos        POST /api/pagos

Los errores del dominio salen con su código: 400 lo que se puede corregir, 401 lo que pide
entrar de nuevo, 403 lo que no le toca a ese rol, 429 la cuenta bloqueada por intentos
fallidos. Nada de esto reemplaza a `auth.exigir`, que se comprueba en cada endpoint.
"""
from __future__ import annotations

import datetime as dt
import hmac
import os
import pathlib
import secrets
import tempfile
from collections.abc import Iterator

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    __version__,
    auth,
    honorarios,
    notificaciones,
    plazos,
    retencion,
    seguridad,
    service,
    titulares,
)
from .db import DB

ESTATICOS = pathlib.Path(__file__).resolve().parent / "static"
COOKIE_SESION = "openlegal_sesion"
COOKIE_TOKEN = "openlegal_token"


class NuevoPlazo(BaseModel):
    causa_id: int
    descripcion: str
    dias: int | None = None
    notificacion: str | None = None
    tipo: str = "judicial"
    es_fatal: bool = True


class Credenciales(BaseModel):
    email: str
    password: str
    #: Código del segundo factor (TOTP). Opcional: sólo lo piden las cuentas que lo tienen.
    codigo: str | None = None


class NuevoCliente(BaseModel):
    nombre: str
    rut: str | None = None
    email: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    tipo_persona: str = "natural"


class NuevaCausa(BaseModel):
    caratula: str
    cliente_id: int | None = None
    rol_rit: str | None = None
    tribunal: str | None = None
    materia: str | None = None
    observaciones: str | None = None


class NuevaAudiencia(BaseModel):
    causa_id: int
    tipo: str
    fecha: str
    hora: str | None = None
    modalidad: str = "presencial"
    lugar_o_url: str | None = None
    minuta: str | None = None
    responsable_id: int | None = None


class NuevoHonorario(BaseModel):
    """Montos en CLP enteros. La retención la declara el estudio, no el CRM."""

    causa_id: int
    modalidad: str = "fijo"
    monto_pactado: int | None = None
    descripcion: str | None = None
    fecha: str | None = None
    monto_bruto: int | None = None
    retencion_sii: int | None = None


class NuevoGasto(BaseModel):
    causa_id: int
    concepto: str
    monto: int
    fecha: str | None = None
    comprobante: str | None = None
    pagado_por_estudio: bool = True


class NuevoPago(BaseModel):
    causa_id: int
    monto: int
    fecha: str | None = None
    medio: str = "transferencia"
    referencia: str | None = None
    nota: str | None = None
    honorario_id: int | None = None


class ConfigAvisos(BaseModel):
    """Lo que el módulo de avisos puede tocar. Un campo vacío significa «no lo cambies»."""

    email: dict | None = None
    sms: dict | None = None
    canales_por_defecto: list[str] | None = None
    dias_de_aviso: int | None = None
    avisar_asignaciones: bool | None = None


class PruebaAviso(BaseModel):
    canal: str = "email"


class NuevoUsuario(BaseModel):
    nombre: str
    email: str
    rol: str
    password: str | None = None
    telefono: str | None = None


class CambioUsuario(BaseModel):
    rol: str | None = None
    telefono: str | None = None
    activo: bool | None = None
    password: str | None = None


class Cuenta(BaseModel):
    email: str


class Alta2FA(BaseModel):
    email: str
    codigo: str | None = None


class Motivo(BaseModel):
    email: str
    motivo: str


class Politica(BaseModel):
    tipo: str
    meses: int
    motivo: str


class EjecutarRetencion(BaseModel):
    motivo: str
    #: Por defecto simula: mirar qué se haría es lo primero, y no rompe nada.
    simular: bool = True


class Titular(BaseModel):
    rut: str | None = None
    nombre: str | None = None
    email: str | None = None


class AnonimizarTitular(BaseModel):
    rut: str | None = None
    nombre: str | None = None
    email: str | None = None
    motivo: str
    simular: bool = True
    redactar_textos: bool = True
    #: Escribir ANONIMIZAR es la confirmación humana de una acción sin vuelta atrás.
    confirmar: str = ""


def crear_app(db_url: str | None = None, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Open Legal Harness", docs_url=None, redoc_url=None)
    app.state.db_url = db_url or os.environ.get("LEGALCRM_DB_URL")
    app.state.token = token or os.environ.get("OPENLEGAL_PANEL_TOKEN") or secrets.token_urlsafe(24)

    # CORS: el panel del harness vive en otro puerto (8801) y llama a este servidor (8899).
    # Sin estas cabeceras el navegador bloquea la respuesta: la petición llega, el servidor
    # contesta 200, y aun así el JavaScript no ve nada. El síntoma es «no responde el
    # servicio del CRM» con el CRM perfectamente levantado, y a un `curl` no le pasa nunca
    # —por eso esto se descubre con el navegador y no con la terminal—.
    #
    # Se permite sólo el loopback (esta máquina, cualquier puerto): la única página que tiene
    # por qué llamar al CRM es la que se sirve acá (el panel embebido y el harness). Igual
    # haría falta el token, pero no hay razón para darle el primer paso a nadie más. Si algún
    # día el harness corre en otro equipo, su origen se agrega en OPENLEGAL_ORIGENES.
    patron_origenes = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
    origen_extra = os.environ.get("OPENLEGAL_ORIGENES", "").strip()
    if origen_extra:
        patron_origenes = f"(?:{patron_origenes})|(?:{origen_extra})"
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=patron_origenes,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-OpenLegal-Token"],
        max_age=600,
    )

    # --------------------------------------------------------------- helpers
    def abrir_db() -> DB:
        db = DB(app.state.db_url)
        verificar_esquema(db)
        return db

    def verificar_esquema(db: DB) -> None:
        """Falla con un mensaje útil en vez de un 500 opaco si falta `openlegal init`."""
        try:
            db.uno("SELECT id FROM usuarios LIMIT 1")
        except Exception as exc:  # sqlite3.OperationalError / psycopg errores de tabla
            raise HTTPException(
                status_code=503,
                detail=(
                    f"la base no tiene el esquema del CRM ({exc}). "
                    f"Corre `openlegal init` sobre {db.url} y vuelve a abrir el panel."
                ),
            ) from exc

    def token_valido(recibido: str | None) -> bool:
        return bool(recibido) and hmac.compare_digest(recibido, app.state.token)

    def usuario_por_defecto(db: DB) -> dict:
        """Modo abogado solo: el panel abre como el usuario responsable del estudio."""
        fila = db.uno(
            "SELECT * FROM usuarios WHERE rol IN ('socio','administrador','abogado') AND activo = 1 "
            "ORDER BY CASE rol WHEN 'socio' THEN 0 WHEN 'administrador' THEN 1 ELSE 2 END, id LIMIT 1"
        )
        if not fila:
            raise HTTPException(status_code=409, detail="no hay usuarios activos en el estudio")
        fila.pop("password_hash", None)
        fila["_via"] = "token"
        return fila

    def identidad(
        db: DB,
        token_q: str | None = None,
        cookie_sesion: str | None = None,
        cookie_token: str | None = None,
        cabecera: str | None = None,
    ) -> dict:
        """Quién pide: por sesión de usuario o por token del panel (modo local)."""
        if cookie_sesion:
            usuario = auth.usuario_por_token(db, cookie_sesion)
            if usuario:
                usuario["_via"] = "sesion"
                return usuario
        if token_valido(token_q) or token_valido(cookie_token) or token_valido(cabecera):
            return usuario_por_defecto(db)
        raise HTTPException(
            status_code=401,
            detail="sin sesión: entra con tu correo y contraseña, o abre con el token del panel",
        )

    def contexto_peticion(
        token_q: str | None = Query(default=None, alias="token"),
        cookie_sesion: str | None = Cookie(default=None, alias=COOKIE_SESION),
        cookie_token: str | None = Cookie(default=None, alias=COOKIE_TOKEN),
        cabecera: str | None = Header(default=None, alias="X-OpenLegal-Token"),
    ) -> Iterator[dict]:
        # Dependencia con `yield`: la conexión se cierra cuando termina la petición.
        # Antes se abría y no se cerraba nunca: el panel dejaba una conexión viva por
        # petición (y en Windows, donde un archivo abierto no se puede borrar, eso
        # rompía la limpieza de las bases temporales).
        with abrir_db() as db:
            usuario = identidad(db, token_q, cookie_sesion, cookie_token, cabecera)
            estudio = db.uno("SELECT * FROM estudios WHERE id = ?", (usuario["estudio_id"],))
            yield {"db": db, "usuario": usuario, "estudio": estudio}

    def publico(usuario: dict) -> dict:
        return {k: usuario.get(k) for k in ("id", "nombre", "email", "rol", "estudio_id")}

    # ------------------------------------------------- errores del dominio
    # La interfaz tiene que decir *por qué no*, no devolver un 500 con un volcado: los
    # errores que el CRM ya sabe explicar se traducen a un código con sentido y a su
    # mensaje. Lo que no esté acá sigue siendo un error del programa, y se ve entero.

    @app.exception_handler(auth.ErrorSegundoFactor)
    async def _segundo_factor(peticion, exc):
        return JSONResponse({"detail": str(exc)}, status_code=401)

    @app.exception_handler(auth.ErrorBloqueado)
    async def _bloqueado(peticion, exc):
        return JSONResponse({"detail": str(exc)}, status_code=429)

    @app.exception_handler(auth.ErrorPermiso)
    async def _permiso(peticion, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(ValueError)
    async def _negocio(peticion, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ----------------------------------------------------------------- panel
    # El panel nuevo (React + React-Admin) se compila a static/app y se sirve desde ahí.
    # Si no está compilado —un clon recién bajado, por ejemplo— queda el panel clásico: el
    # CRM nunca se queda sin interfaz, y el clásico sigue accesible en /clasico mientras se
    # termina la migración.
    APP_COMPILADO = ESTATICOS / "app"
    if (APP_COMPILADO / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=APP_COMPILADO / "assets"), name="panel-assets")

    def abrir_panel(archivo: pathlib.Path, token: str | None) -> HTMLResponse:
        html = HTMLResponse(archivo.read_text(encoding="utf-8"))
        if token_valido(token):
            # La cookie tiene que ponerse sobre la respuesta que se devuelve: si se
            # pone en el objeto `Response` inyectado y además se retorna otra
            # respuesta, Starlette descarta las cabeceras de la inyectada.
            html.set_cookie(COOKIE_TOKEN, token, httponly=True, samesite="strict")
        return html

    @app.get("/", response_class=HTMLResponse)
    def panel(
        respuesta: Response,
        token: str | None = Query(default=None),
        cookie_token: str | None = Cookie(default=None, alias=COOKIE_TOKEN),
    ):
        """El HTML es estático y no lleva datos: se sirve siempre. Los datos quedan
        detrás de la API, que sí exige sesión o token. Así la secretaria puede
        abrir la página y entrar con su correo."""
        nuevo = APP_COMPILADO / "index.html"
        return abrir_panel(nuevo if nuevo.is_file() else ESTATICOS / "panel.html", token)

    @app.get("/clasico", response_class=HTMLResponse)
    def panel_clasico(
        respuesta: Response,
        token: str | None = Query(default=None),
        cookie_token: str | None = Cookie(default=None, alias=COOKIE_TOKEN),
    ):
        """El panel anterior, en JavaScript a mano. Queda como salida de emergencia."""
        return abrir_panel(ESTATICOS / "panel.html", token)

    @app.get("/panel.css")
    def css():
        return FileResponse(ESTATICOS / "panel.css", media_type="text/css")

    @app.get("/panel.js")
    def js():
        return FileResponse(ESTATICOS / "panel.js", media_type="application/javascript")

    # --------------------------------------------------------------- sesión
    @app.post("/api/login")
    def api_login(datos: Credenciales, respuesta: Response):
        with abrir_db() as db:
            try:
                # `autenticar` ya deja en la bitácora el fallo de credenciales y el fallo
                # del segundo factor: acá no se vuelve a registrar, o el contador de
                # intentos de la alerta contaría dos veces el mismo intento.
                usuario = auth.autenticar(db, datos.email, datos.password, datos.codigo)
            except auth.ErrorSegundoFactor as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            except auth.ErrorBloqueado as exc:
                # 429: no es que las credenciales estén mal, es que hay que esperar.
                raise HTTPException(status_code=429, detail=str(exc)) from exc
            if not usuario:
                raise HTTPException(status_code=401, detail="correo o contraseña incorrectos")
            token_sesion = usuario.pop("token")
            respuesta.set_cookie(COOKIE_SESION, token_sesion, httponly=True, samesite="strict")
            auth.auditar(db, usuario["estudio_id"], usuario["id"], "login.ok", "usuarios", usuario["id"])
            return {"usuario": publico(usuario)}

    @app.post("/api/logout")
    def api_logout(
        respuesta: Response,
        cookie_sesion: str | None = Cookie(default=None, alias=COOKIE_SESION),
    ):
        with abrir_db() as db:
            if cookie_sesion:
                usuario = auth.usuario_por_token(db, cookie_sesion)
                auth.cerrar_sesion(db, cookie_sesion)
                if usuario:
                    auth.auditar(db, usuario["estudio_id"], usuario["id"], "logout", "usuarios", usuario["id"])
            respuesta.delete_cookie(COOKIE_SESION)
            return {"ok": True}

    @app.get("/api/sesion")
    def api_sesion(ctx: dict = Depends(contexto_peticion)):
        usuario, estudio = ctx["usuario"], ctx["estudio"]
        return {
            "usuario": publico(usuario),
            "estudio": {"id": estudio["id"], "nombre": estudio["nombre"], "modo": estudio["modo"]},
            "via": usuario.get("_via"),
            # Lo que esta persona puede hacer, para que la interfaz le muestre sus módulos
            # y no botones que van a dar error. El permiso se vuelve a exigir en cada
            # endpoint: esto es información para la pantalla, no la seguridad.
            "permisos": sorted(auth.PERMISOS.get(usuario["rol"], set())),
            "version": __version__,
        }

    # -------------------------------------------------------------------- api
    @app.get("/api/estado")
    def api_estado(ctx: dict = Depends(contexto_peticion)):
        db, usuario, estudio = ctx["db"], ctx["usuario"], ctx["estudio"]
        conteos = {
            tabla: db.uno(f"SELECT COUNT(*) AS total FROM {tabla}")["total"]
            for tabla in ("clientes", "causas", "plazos", "audiencias")
        }
        return {
            "version": __version__,
            "estudio": {"id": estudio["id"], "nombre": estudio["nombre"], "modo": estudio["modo"]},
            "usuario": publico(usuario),
            "motores": db.dialecto,
            "conteos": conteos,
        }

    @app.get("/api/causas")
    def api_causas(ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        causas = auth.causas_visibles(db, usuario)
        for causa in causas:
            plazo = db.uno(
                "SELECT fecha_vencimiento, descripcion, es_fatal FROM plazos "
                "WHERE causa_id = ? AND estado = 'pendiente' AND fecha_vencimiento IS NOT NULL "
                "ORDER BY fecha_vencimiento LIMIT 1",
                (causa["id"],),
            )
            causa["proximo_vencimiento"] = dict(plazo) if plazo else None
            causa["equipo"] = [
                f["nombre"]
                for f in db.todos(
                    "SELECT u.nombre FROM causa_equipo e JOIN usuarios u ON u.id = e.usuario_id "
                    "WHERE e.causa_id = ? AND e.hasta IS NULL",
                    (causa["id"],),
                )
            ]
        return causas

    @app.get("/api/plazos")
    def api_plazos(dias: int = 30, desde: str | None = None, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        hoy = dt.date.today()
        filas = service.vencimientos(db, usuario, desde or hoy.isoformat(), dias)
        for fila in filas:
            vencimiento = dt.date.fromisoformat(fila["fecha_vencimiento"])
            fila["dias_restantes"] = (vencimiento - hoy).days
        return filas

    @app.get("/api/agenda")
    def api_agenda(dias: int = 30, desde: str | None = None, ctx: dict = Depends(contexto_peticion)):
        return service.agenda(ctx["db"], ctx["usuario"], desde, dias)

    @app.get("/api/panel")
    def api_panel(ctx: dict = Depends(contexto_peticion)):
        return service.panel(ctx["db"], ctx["usuario"])

    @app.get("/api/calculo")
    def api_calculo(notificacion: str, dias: int, ctx: dict = Depends(contexto_peticion)):
        return plazos.vencimiento(dt.date.fromisoformat(notificacion), dias)

    @app.post("/api/plazos")
    def api_crear_plazo(datos: NuevoPlazo, ctx: dict = Depends(contexto_peticion)):
        resultado = service.crear_plazo(
            ctx["db"], ctx["usuario"], datos.causa_id, datos.descripcion, dias=datos.dias,
            fecha_notificacion=datos.notificacion, tipo=datos.tipo, es_fatal=datos.es_fatal,
        )
        return resultado

    @app.post("/api/plazos/{plazo_id}/cumplido")
    def api_cumplido(plazo_id: int, ctx: dict = Depends(contexto_peticion)):
        service.marcar_cumplido(ctx["db"], ctx["usuario"], plazo_id)
        return {"ok": True, "plazo": plazo_id}

    # ---------------------------------------------------------------- módulos
    # Cada módulo del CRM tiene su puerta acá. Todo pasa por `contexto_peticion` (sesión o
    # token) y por `auth.exigir` (permiso): la interfaz es otra forma de escribir las
    # reglas, no un atajo para saltearlas. Las claves de correo y SMS nunca salen de acá.

    @app.get("/api/avisos")
    def api_avisos(ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, notificaciones.PERMISO)
        config = notificaciones.cargar_config()
        correo = config.get("email") or {}
        sms = config.get("sms") or {}
        return {
            "canales": notificaciones.resumen_config(config),
            "ajustes": {
                "dias_de_aviso": int(config.get("dias_de_aviso", notificaciones.DIAS_DE_AVISO)),
                "canales_por_defecto": list(config.get("canales_por_defecto") or ["email"]),
                "avisar_asignaciones": config.get("avisar_asignaciones", True) is not False,
            },
            # Los campos que la persona ya cargó, para no hacerle escribir todo de nuevo.
            # La clave y el token no están acá: no se devuelven nunca.
            "formulario": {
                "email": {k: v for k, v in correo.items() if k != "clave"},
                "sms": {k: v for k, v in sms.items() if k != "token"},
            },
            "estado": notificaciones.estado(db),
            "cola": db.todos(
                "SELECT id, canal, destino, asunto, estado, intentos, ultimo_error, creada_en, enviada_en "
                "FROM notificaciones ORDER BY id DESC LIMIT 25"
            ),
            "archivo": str(notificaciones.ruta_config()),
        }

    @app.post("/api/avisos/config")
    def api_avisos_config(datos: ConfigAvisos, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, notificaciones.PERMISO)
        cambios = {k: v for k, v in datos.model_dump().items() if v is not None}
        if not cambios:
            raise ValueError("no hay nada que guardar")
        notificaciones.guardar_config(cambios)
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "avisos.configurar", "notificaciones", None,
            ", ".join(sorted(cambios)),     # qué secciones se tocaron, nunca los valores
        )
        return {"ok": True, "canales": notificaciones.resumen_config()}

    @app.post("/api/avisos/probar")
    def api_avisos_probar(datos: PruebaAviso, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, notificaciones.PERMISO)
        canal = datos.canal if datos.canal in notificaciones.CANALES else "email"
        # El canal de prueba no sale a la red: el destino es sólo para el registro.
        destino = (usuario.get("email") if canal in ("email", "consola") else usuario.get("telefono")) or ""
        if not destino:
            raise ValueError(
                f"tu usuario no tiene {'correo' if canal == 'email' else 'teléfono'} cargado: "
                f"cárgalo en el módulo de usuarios y vuelve a probar"
            )
        identificador = notificaciones.encolar(
            db, usuario["estudio_id"], canal, destino,
            "Prueba de avisos del CRM. Si estás leyendo esto, el canal quedó bien configurado.",
            clave=f"prueba:{canal}:{usuario['id']}:{dt.datetime.now().strftime('%Y%m%d%H%M%S')}",
            asunto="Prueba de avisos del CRM",
        )
        resultado = notificaciones.enviar_aviso(db, usuario, identificador) if identificador else {"enviado": False, "error": "no se pudo encolar la prueba"}
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "avisos.probar", "notificaciones", identificador,
            f"canal={canal} destino={destino} ok={resultado['enviado']}",
        )
        return {"canal": canal, "destino": destino, **resultado}

    @app.post("/api/avisos/generar")
    def api_avisos_generar(ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        return notificaciones.generar_recordatorios(db, usuario)

    @app.post("/api/avisos/despachar")
    def api_avisos_despachar(ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        return notificaciones.enviar_pendientes(db, usuario)

    # ------------------------------------------------------------- usuarios
    @app.get("/api/usuarios")
    def api_usuarios(ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, "usuario.gestionar")
        filas = db.todos(
            "SELECT id, nombre, email, rol, telefono, activo, totp_activo FROM usuarios "
            "WHERE estudio_id = ? ORDER BY CASE rol WHEN 'socio' THEN 0 WHEN 'administrador' THEN 1 ELSE 2 END, nombre",
            (usuario["estudio_id"],),
        )
        return {
            "usuarios": filas,
            "roles": list(auth.ROLES),
            "bloqueos": {f["email"]: seguridad.bloqueo(db, f["email"]) for f in filas},
        }

    @app.post("/api/usuarios")
    def api_crear_usuario(datos: NuevoUsuario, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, "usuario.gestionar")
        if not datos.password or len(datos.password) < 10:
            # La misma regla que en la terminal: son datos de clientes.
            raise ValueError("la contraseña necesita al menos 10 caracteres")
        creado = auth.crear_usuario(
            db, usuario["estudio_id"], datos.nombre, datos.email, datos.rol,
            datos.password, actor_id=usuario["id"],
        )
        if datos.telefono:
            auth.actualizar_usuario(db, usuario, datos.email, telefono=datos.telefono)
        return {"id": creado, "email": datos.email.lower(), "rol": datos.rol}

    @app.post("/api/usuarios/{usuario_id}")
    def api_editar_usuario(usuario_id: int, datos: CambioUsuario, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        fila = db.uno(
            "SELECT * FROM usuarios WHERE id = ? AND estudio_id = ?", (usuario_id, usuario["estudio_id"])
        )
        if not fila:
            raise ValueError(f"no existe el usuario {usuario_id} en este estudio")
        if datos.password is not None and len(datos.password) < 10:
            raise ValueError("la contraseña necesita al menos 10 caracteres")
        actualizado = auth.actualizar_usuario(
            db, usuario, fila["email"], rol=datos.rol, telefono=datos.telefono,
            activo=datos.activo, password=datos.password,
        )
        if datos.password is not None or datos.activo is False:
            # Cambiar la clave o cortar el acceso invalida las sesiones abiertas de esa cuenta.
            db.ejecutar("DELETE FROM sesiones WHERE usuario_id = ?", (fila["id"],))
        actualizado.pop("password_hash", None)
        return actualizado

    # ------------------------------------------------------------ seguridad
    @app.get("/api/seguridad")
    def api_seguridad(minutos: int = 15, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, "usuario.gestionar")
        cuentas = auth.estado_segundo_factor(db, usuario)
        for cuenta in cuentas:
            cuenta["bloqueo"] = seguridad.bloqueo(db, cuenta["email"])
        return {
            "intentos": seguridad.intentos_fallidos(db, minutos=minutos, estudio_id=usuario["estudio_id"]),
            "cuentas": cuentas,
            "umbrales": {
                "intentos_alerta": seguridad.UMBRAL_INTENTOS,
                "bloqueo_intentos": seguridad.BLOQUEO_INTENTOS,
                "bloqueo_minutos": seguridad.BLOQUEO_MINUTOS,
            },
        }

    @app.post("/api/seguridad/desbloquear")
    def api_desbloquear(datos: Cuenta, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        estado = auth.desbloquear(db, usuario, datos.email)
        return {"email": datos.email, **estado}

    @app.post("/api/seguridad/2fa/preparar")
    def api_2fa_preparar(datos: Cuenta, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        return auth.preparar_segundo_factor(db, usuario, datos.email)

    @app.post("/api/seguridad/2fa/confirmar")
    def api_2fa_confirmar(datos: Alta2FA, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        return auth.confirmar_segundo_factor(db, usuario, datos.email, datos.codigo)

    @app.post("/api/seguridad/2fa/apagar")
    def api_2fa_apagar(datos: Motivo, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.desactivar_segundo_factor(db, usuario, datos.email, datos.motivo)
        return {"email": datos.email, "activo": False}

    # ------------------------------------------------------------ retención
    @app.get("/api/retencion")
    def api_retencion(ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, retencion.PERMISO)
        datos = retencion.informe(db)
        return {
            **datos,
            "tipos": list(retencion.SUGERENCIAS),
        }

    @app.post("/api/retencion")
    def api_definir_retencion(datos: Politica, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        retencion.definir(db, usuario, datos.tipo, datos.meses, datos.motivo)
        return {"ok": True, **retencion.informe(db)}

    @app.post("/api/retencion/ejecutar")
    def api_ejecutar_retencion(datos: EjecutarRetencion, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        return retencion.aplicar(db, usuario, datos.motivo, simular=datos.simular)

    # ------------------------------------------------------------- clientes
    @app.get("/api/clientes")
    def api_clientes(q: str | None = None, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        auth.exigir(db, usuario, "cliente.leer")
        if q:
            return titulares.buscar(db, usuario, texto=q)
        return db.todos(
            "SELECT id, nombre, rut, email, telefono, direccion, tipo_persona FROM clientes "
            "WHERE estudio_id = ? ORDER BY nombre",
            (usuario["estudio_id"],),
        )

    @app.post("/api/clientes")
    def api_crear_cliente(datos: NuevoCliente, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        identificador = service.crear_cliente(
            db, usuario, datos.nombre, datos.rut, tipo_persona=datos.tipo_persona,
            email=datos.email, telefono=datos.telefono, direccion=datos.direccion,
        )
        return {"id": identificador, "nombre": datos.nombre}

    @app.post("/api/causas")
    def api_crear_causa(datos: NuevaCausa, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        identificador = service.crear_causa(
            db, usuario, datos.caratula, cliente_id=datos.cliente_id, rol_rit=datos.rol_rit,
            tribunal=datos.tribunal, materia=datos.materia, observaciones=datos.observaciones,
        )
        return {"id": identificador, "caratula": datos.caratula}

    @app.post("/api/audiencias")
    def api_crear_audiencia(datos: NuevaAudiencia, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        identificador = service.crear_audiencia(
            db, usuario, datos.causa_id, datos.tipo, datos.fecha, datos.hora,
            modalidad=datos.modalidad, lugar_o_url=datos.lugar_o_url, minuta=datos.minuta,
            responsable_id=datos.responsable_id,
        )
        return {"id": identificador}

    # ------------------------------------------- honorarios, gastos y pagos
    # La cuenta de dividendos del estudio: lo que se pactó, lo que se gastó y lo que el
    # cliente abonó. El panel muestra esa cuenta y da de alta registros; el HTML imprimible
    # sale por la misma ruta (`formato=html`) y viaja con la sesión por cookie, así que el
    # navegador lo abre en otra pestaña y lo imprime sin que haya tokens en JavaScript.
    @app.get("/api/cuenta")
    def api_cuenta(causa: int, formato: str = "json", ctx: dict = Depends(contexto_peticion)):
        datos = honorarios.cuenta(ctx["db"], ctx["usuario"], causa)
        if formato == "html":
            return HTMLResponse(honorarios.html_cuenta(datos))
        return datos

    @app.post("/api/honorarios")
    def api_crear_honorario(datos: NuevoHonorario, ctx: dict = Depends(contexto_peticion)):
        honorario_id = honorarios.registrar_honorario(
            ctx["db"], ctx["usuario"], datos.causa_id, datos.modalidad,
            monto_pactado=datos.monto_pactado, descripcion=datos.descripcion, fecha=datos.fecha,
            monto_bruto=datos.monto_bruto, retencion_sii=datos.retencion_sii,
        )
        fila = ctx["db"].uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
        sin_retencion = bool(fila) and fila.get("monto_bruto") is not None and fila.get("retencion_sii") is None
        return {
            "id": honorario_id,
            "honorario": fila,
            "aviso": honorarios.ADVERTENCIA_SIN_RETENCION_DE_UNO if sin_retencion else None,
        }

    @app.post("/api/gastos")
    def api_crear_gasto(datos: NuevoGasto, ctx: dict = Depends(contexto_peticion)):
        gasto_id = honorarios.registrar_gasto(
            ctx["db"], ctx["usuario"], datos.causa_id, datos.concepto, datos.monto,
            fecha=datos.fecha, comprobante=datos.comprobante, pagado_por_estudio=datos.pagado_por_estudio,
        )
        return {"id": gasto_id, "gasto": ctx["db"].uno("SELECT * FROM gastos WHERE id = ?", (gasto_id,))}

    @app.post("/api/pagos")
    def api_crear_pago(datos: NuevoPago, ctx: dict = Depends(contexto_peticion)):
        pago_id = honorarios.registrar_pago(
            ctx["db"], ctx["usuario"], datos.causa_id, datos.monto, fecha=datos.fecha,
            medio=datos.medio, referencia=datos.referencia, nota=datos.nota,
            honorario_id=datos.honorario_id,
        )
        respuesta: dict[str, object] = {"id": pago_id}
        if datos.honorario_id:
            respuesta["honorario"] = honorarios.recalcular_honorario(ctx["db"], datos.honorario_id)
        return respuesta

    # ---------------------------------------------------- derechos del titular
    @app.get("/api/titulares")
    def api_buscar_titular(
        rut: str | None = None, nombre: str | None = None, email: str | None = None,
        texto: str | None = None, ctx: dict = Depends(contexto_peticion),
    ):
        db, usuario = ctx["db"], ctx["usuario"]
        return titulares.buscar(db, usuario, rut=rut, nombre=nombre, email=email, texto=texto)

    @app.post("/api/titulares/exportar")
    def api_exportar_titular(datos: Titular, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        with tempfile.TemporaryDirectory() as carpeta:
            destino = pathlib.Path(carpeta) / "titular.json"
            informe = titulares.exportar(db, usuario, rut=datos.rut, nombre=datos.nombre,
                                         email=datos.email, destino=str(destino))
            contenido = destino.read_bytes()
        return Response(
            content=contenido,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="expediente-titular.json"',
                "X-OpenLegal-Sha256": informe["sha256"],
            },
        )

    @app.post("/api/titulares/anonimizar")
    def api_anonimizar_titular(datos: AnonimizarTitular, ctx: dict = Depends(contexto_peticion)):
        db, usuario = ctx["db"], ctx["usuario"]
        if not datos.simular and datos.confirmar.strip().upper() != "ANONIMIZAR":
            raise ValueError(
                "la anonimización no se puede deshacer: escribe ANONIMIZAR en la confirmación "
                "(o pide primero la simulación, que no cambia nada)"
            )
        return titulares.anonimizar(
            db, usuario, rut=datos.rut, nombre=datos.nombre, email=datos.email,
            motivo=datos.motivo, simular=datos.simular, redactar_textos=datos.redactar_textos,
        )

    return app
