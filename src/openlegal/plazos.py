"""Computo de plazos de dias habiles judiciales (Art. 66 CPC).

Art. 59 CPC: "Las actuaciones judiciales deben practicarse en dias y horas
habiles. Son dias habiles los no feriados." Art. 66 CPC: los terminos de dias se
entienden suspendidos durante los feriados, y los terminos corren desde el dia
siguiente a la notificacion.

El sabado es habil por defecto, con este respaldo: en el procedimiento civil el
"feriado" del art. 66 se ha entendido referido a domingos y festivos (los sabados
son habiles), mientras que en el procedimiento administrativo de la Ley 19.880 los
sabados son inhabiles. La eleccion se deja EXPLICITA en cada calculo —el campo
`regla_dias_habiles` dice cual se aplico y el parametro `sabado_habil` la cambia—,
porque un dia de diferencia en un plazo fatal no puede quedar implicito.

Nota de direccion del error: tratar el sabado como habil acorta el plazo (fecha mas
temprana = conservadora); tratarlo como inhabil lo alarga (fecha mas tardia =
peligrosa si el criterio verdadero fuera el otro). Por eso el defecto es habil.

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
    """Fechas feriadas del año. Acepta entradas como texto o como ficha con nombre y ley."""
    ruta = ruta or ARCHIVO_FERIADOS
    datos = json.loads(pathlib.Path(ruta).read_text(encoding="utf-8"))
    crudos = datos.get(str(anio), datos.get(anio, []))
    fechas: set[dt.date] = set()
    for entrada in crudos:
        if isinstance(entrada, dict):
            fechas.add(dt.date.fromisoformat(entrada["fecha"]))
        else:
            fechas.add(dt.date.fromisoformat(entrada))
    return fechas


def ficha_feriado(anio: int, dia: dt.date, ruta: pathlib.Path | None = None) -> dict | None:
    """Nombre y ley del feriado, para poder fundamentar el cómputo."""
    ruta = ruta or ARCHIVO_FERIADOS
    datos = json.loads(pathlib.Path(ruta).read_text(encoding="utf-8"))
    for entrada in datos.get(str(anio), []):
        if isinstance(entrada, dict) and entrada.get("fecha") == dia.isoformat():
            return entrada
    return None


def es_habil(dia: dt.date, feriados: set[dt.date], sabado_habil: bool = True) -> bool:
    """Dia habil judicial: no domingo y no feriado. El sabado es habil por defecto.

    Ver el encabezado del modulo: la regla del sabado se deja explicita en el
    resultado del calculo, y el defecto (sabado habil) es el conservador.
    """
    if dia.weekday() == 6:
        return False
    if dia.weekday() == 5 and not sabado_habil:
        return False
    return dia not in feriados


def regla_dias_habiles(sabado_habil: bool) -> str:
    """Texto de la regla aplicada, para que el calculo se pueda auditar."""
    if sabado_habil:
        return ("días hábiles de lunes a sábado: se suspenden domingos y feriados "
                "(art. 59 y 66 CPC; el sábado es hábil en el procedimiento civil)")
    return ("días hábiles de lunes a viernes: se suspenden sábados, domingos y feriados "
            "(criterio del procedimiento administrativo, Ley 19.880)")


def vencimiento(
    fecha_notificacion: dt.date,
    dias: int,
    feriados: set[dt.date] | None = None,
    sabado_habil: bool = True,
) -> dict:
    """Devuelve {fecha_vencimiento, detalle, advertencias, regla_dias_habiles} contando `dias` habiles.

    Se empieza a contar desde el dia siguiente a la notificacion. Si el computo
    pisa un año cuyos feriados no estan validados, lo dice en `advertencias`: un
    vencimiento que depende de un feriado desconocido no se puede usar en juicio.
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
            ficha = ficha_feriado(dia.year, dia)
            if ficha:
                motivo = f"feriado: {ficha.get('nombre')} ({ficha.get('ley')})"
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

    advertencias: list[str] = []
    for anio in range(fecha_notificacion.year, dia.year + 1):
        if feriados_por_validar(anio) or not cargar_feriados(anio):
            advertencias.append(
                f"los feriados de {anio} no están validados contra el calendario oficial: "
                f"revisa el vencimiento antes de usarlo en juicio"
            )
    return {
        "fecha_vencimiento": dia.isoformat(),
        "detalle": detalle,
        "advertencias": advertencias,
        "regla_dias_habiles": regla_dias_habiles(sabado_habil),
        "sabado_habil": sabado_habil,
    }


def feriados_por_validar(anio: int) -> bool:
    datos = json.loads(ARCHIVO_FERIADOS.read_text(encoding="utf-8"))
    return bool(datos.get("_pendiente_validacion", {}).get(str(anio)))
