"""Servicio web local del CRM: la cara que se incrusta en el sidebar derecho del harness.

Corre solo en 127.0.0.1 y exige token (igual que el harness): el panel no queda
expuesto a la red. No hay login multiusuario todavia: el panel opera como el
usuario activo del estudio (el primer socio/abogado), y la autenticacion real
llega con F1. Nunca se sirve informacion de un estudio distinto.

Endpoints:
  GET  /                     panel HTML
  GET  /api/estado           estudio, usuario activo y conteos
  GET  /api/causas           causas visibles para el usuario activo
  GET  /api/plazos?dias=N    proximos vencimientos
  GET  /api/agenda?dias=N    audiencias proximas
  GET  /api/panel            KPIs del estudio
  GET  /api/calculo?notificacion=YYYY-MM-DD&dias=N   computo Art. 66 CPC
  POST /api/plazos           crear plazo (calcula vencimiento)
  POST /api/plazos/{id}/cumplido
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import secrets

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import __version__, auth, plazos, service
from .db import DB

ESTATICOS = pathlib.Path(__file__).resolve().parent / "static"


class NuevoPlazo(BaseModel):
    causa_id: int
    descripcion: str
    dias: int | None = None
    notificacion: str | None = None
    tipo: str = "judicial"
    es_fatal: bool = True


def crear_app(db_url: str | None = None, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Open Legal CRM", docs_url=None, redoc_url=None)
    app.state.db_url = db_url or os.environ.get("LEGALCRM_DB_URL")
    app.state.token = token or os.environ.get("OPENLEGAL_PANEL_TOKEN") or secrets.token_urlsafe(24)

    # --------------------------------------------------------------- helpers
    def abrir_db() -> DB:
        db = DB(app.state.db_url)
        verificar_esquema(db)
        return db

    def verificar_esquema(db: DB) -> None:
        """Falla con un mensaje util en vez de un 500 opaco si falta `openlegal init`."""
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

    def autorizar(
        token_q: str | None = Query(default=None, alias="token"),
        cookie: str | None = Cookie(default=None, alias="openlegal_token"),
        cabecera: str | None = Header(default=None, alias="X-OpenLegal-Token"),
    ) -> str:
        recibido = token_q or cookie or cabecera
        if recibido != app.state.token:
            raise HTTPException(status_code=401, detail="token invalido")
        return recibido

    def usuario_activo(db: DB) -> dict:
        """Usuario que opera el panel: el primer socio, o el primer abogado activo."""
        fila = db.uno(
            "SELECT * FROM usuarios WHERE rol IN ('socio','abogado') AND activo = 1 "
            "ORDER BY CASE rol WHEN 'socio' THEN 0 ELSE 1 END, id LIMIT 1"
        )
        if not fila:
            raise HTTPException(status_code=409, detail="no hay usuarios activos en el estudio")
        fila.pop("password_hash", None)
        return fila

    def contexto(db: DB) -> tuple[dict, dict]:
        usuario = usuario_activo(db)
        estudio = db.uno("SELECT * FROM estudios WHERE id = ?", (usuario["estudio_id"],))
        return usuario, estudio

    # ----------------------------------------------------------------- panel
    @app.get("/", response_class=HTMLResponse)
    def panel(respuesta: Response, token: str | None = Query(default=None)):
        if token != app.state.token:
            return HTMLResponse(
                "<h1>401</h1><p>Falta el token. Abre la URL que imprime `openlegal serve`.</p>",
                status_code=401,
            )
        html = (ESTATICOS / "panel.html").read_text(encoding="utf-8")
        respuesta.set_cookie("openlegal_token", token, httponly=True, samesite="strict")
        return HTMLResponse(html)

    @app.get("/panel.css")
    def css():
        return FileResponse(ESTATICOS / "panel.css", media_type="text/css")

    @app.get("/panel.js")
    def js():
        return FileResponse(ESTATICOS / "panel.js", media_type="application/javascript")

    # -------------------------------------------------------------------- api
    @app.get("/api/estado")
    def api_estado(_: str = Depends(autorizar)):
        db = abrir_db()
        usuario, estudio = contexto(db)
        conteos = {
            tabla: db.uno(f"SELECT COUNT(*) AS total FROM {tabla}")["total"]
            for tabla in ("clientes", "causas", "plazos", "audiencias")
        }
        return {
            "version": __version__,
            "estudio": {"id": estudio["id"], "nombre": estudio["nombre"], "modo": estudio["modo"]},
            "usuario": {"id": usuario["id"], "nombre": usuario["nombre"], "rol": usuario["rol"]},
            "motores": db.dialecto,
            "conteos": conteos,
        }

    @app.get("/api/causas")
    def api_causas(_: str = Depends(autorizar)):
        db = abrir_db()
        usuario, _ = contexto(db)
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
    def api_plazos(dias: int = 30, desde: str | None = None, _: str = Depends(autorizar)):
        db = abrir_db()
        usuario, _ = contexto(db)
        hoy = dt.date.today()
        filas = service.vencimientos(db, usuario, desde or hoy.isoformat(), dias)
        for fila in filas:
            vencimiento = dt.date.fromisoformat(fila["fecha_vencimiento"])
            fila["dias_restantes"] = (vencimiento - hoy).days
        return filas

    @app.get("/api/agenda")
    def api_agenda(dias: int = 30, desde: str | None = None, _: str = Depends(autorizar)):
        db = abrir_db()
        usuario, _ = contexto(db)
        return service.agenda(db, usuario, desde, dias)

    @app.get("/api/panel")
    def api_panel(_: str = Depends(autorizar)):
        db = abrir_db()
        usuario, _ = contexto(db)
        return service.panel(db, usuario)

    @app.get("/api/calculo")
    def api_calculo(notificacion: str, dias: int, _: str = Depends(autorizar)):
        return plazos.vencimiento(dt.date.fromisoformat(notificacion), dias)

    @app.post("/api/plazos")
    def api_crear_plazo(datos: NuevoPlazo, _: str = Depends(autorizar)):
        db = abrir_db()
        usuario, _ = contexto(db)
        resultado = service.crear_plazo(
            db, usuario, datos.causa_id, datos.descripcion, dias=datos.dias,
            fecha_notificacion=datos.notificacion, tipo=datos.tipo, es_fatal=datos.es_fatal,
        )
        return resultado

    @app.post("/api/plazos/{plazo_id}/cumplido")
    def api_cumplido(plazo_id: int, _: str = Depends(autorizar)):
        db = abrir_db()
        usuario, _ = contexto(db)
        service.marcar_cumplido(db, usuario, plazo_id)
        return {"ok": True, "plazo": plazo_id}

    return app
