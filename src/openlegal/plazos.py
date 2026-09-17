"""Computo de plazos de dias habiles judiciales (Art. 66 CPC).

Art. 66 CPC: los plazos de dias establecidos por la ley son de dias habiles;
se suspenden los dias feriados y se cuentan desde el dia siguiente a la
notificacion.

Los feriados NO se adivinan en el codigo: viven en `feriados_cl.json`, que hay
que validar cada ano contra el calendario oficial (BCN / Direccion del Trabajo).
Un feriado mal cargado se traduce en un plazo fatal mal calculado.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parent
ARCHIVO_FERIADOS = RAIZ / "feriados_cl.json"

DIAS_SEMANA = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


def cargar_feriados(anio: int, ruta: pathlib.Path | None = None) -> set[dt.date]:
    ruta = ruta or ARCHIVO_FERIADOS
    datos = json.loads(pathlib.Path(ruta).read_text(encoding="utf-8"))
    crudos = datos.get(str(anio), datos.get(anio, []))
    return {dt.date.fromisoformat(d) for d in crudos}


def es_habil(dia: dt.date, feriados: set[dt.date], sabado_habil: bool = True) -> bool:
    """Dia habil judicial: no domingo y no feriado. El sabado es habil (Art. 66 CPC)."""
    if dia.weekday() == 6:
        return False
    if dia.weekday() == 5 and not sabado_habil:
        return False
    return dia not in feriados


def vencimiento(
    fecha_notificacion: dt.date,
    dias: int,
    feriados: set[dt.date] | None = None,
    sabado_habil: bool = True,
) -> dict:
    """Devuelve {fecha_vencimiento, detalle} contando `dias` habiles.

    Se empieza a contar desde el dia siguiente a la notificacion.
    """
    if dias <= 0:
        raise ValueError("los dias del plazo deben ser mayores que cero")
    if feriados is None:
        feriados = cargar_feriados(fecha_notificacion.year)
        if fecha_notificacion.month == 12:
            try:
                feriados |= cargar_feriados(fecha_notificacion.year + 1)
            except (KeyError, ValueError):
                pass

    detalle: list[dict] = []
    dia = fecha_notificacion
    contados = 0
    while contados < dias:
        dia += dt.timedelta(days=1)
        habil = es_habil(dia, feriados, sabado_habil)
        motivo = ""
        if not habil:
            motivo = "feriado" if dia in feriados else ("domingo" if dia.weekday() == 6 else "sabado")
        else:
            contados += 1
        detalle.append(
            {
                "fecha": dia.isoformat(),
                "dia": DIAS_SEMANA[dia.weekday()],
                "habil": habil,
                "motivo": motivo,
                "dia_contado": contados if habil else None,
            }
        )
    return {"fecha_vencimiento": dia.isoformat(), "detalle": detalle}


def feriados_por_validar(anio: int) -> bool:
    datos = json.loads(ARCHIVO_FERIADOS.read_text(encoding="utf-8"))
    return bool(datos.get("_pendiente_validacion", {}).get(str(anio)))
