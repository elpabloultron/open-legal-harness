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
  GET  /api/sesion           quién soy y en qué estudio
  GET  /api/estado           estudio, usuario y conteos
  GET  /api/causas           causas visibles para el usuario
  GET  /api/plazos?dias=N    próximos vencimientos
  GET  /api/agenda?dias=N    audiencias próximas
  GET  /api/panel            KPIs del estudio
  GET  /api/calculo?notificacion=YYYY-MM-DD&dias=N   cómputo Art. 66 CPC
  POST /api/plazos           crear plazo (calcula vencimiento)
  POST /api/plazos/{id}/cumplido
"""
from __future__ import annotations

import datetime as dt
import hmac
import os
import pathlib
import secrets
from collections.abc import Iterator

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import __version__, auth, plazos, service
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


def crear_app(db_url: str | None = None, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Open Legal Harness", docs_url=None, redoc_url=None)
    app.state.db_url = db_url or os.environ.get("LEGALCRM_DB_URL")
    app.state.token = token or os.environ.get("OPENLEGAL_PANEL_TOKEN") or secrets.token_urlsafe(24)

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

    # ----------------------------------------------------------------- panel
    @app.get("/", response_class=HTMLResponse)
    def panel(
        respuesta: Response,
        token: str | None = Query(default=None),
        cookie_token: str | None = Cookie(default=None, alias=COOKIE_TOKEN),
    ):
        """El HTML es estático y no lleva datos: se sirve siempre. Los datos quedan
        detrás de la API, que sí exige sesión o token. Así la secretaria puede
        abrir la página y entrar con su correo."""
        html = HTMLResponse((ESTATICOS / "panel.html").read_text(encoding="utf-8"))
        if token_valido(token):
            # La cookie tiene que ponerse sobre la respuesta que se devuelve: si se
            # pone en el objeto `Response` inyectado y además se retorna otra
            # respuesta, Starlette descarta las cabeceras de la inyectada.
            html.set_cookie(COOKIE_TOKEN, token, httponly=True, samesite="strict")
        return html

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
            usuario = auth.autenticar(db, datos.email, datos.password)
            if not usuario:
                # El intento fallido también se registra: sirve para detectar abuso.
                auth.auditar(db, None, None, "login.fallido", "usuarios", None, datos.email.lower()[:80])
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

    return app
