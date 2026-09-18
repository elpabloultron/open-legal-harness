"""Retención: cuánto se conserva cada cosa y qué se hace cuando el plazo se cumple.

Dos decisiones de fondo, y las dos son para no mentir:

1. **El sistema no inventa plazos.** Trae sugerencias con su anclaje donde existe, y el
   estudio declara los suyos; la política vive en la base con fecha de cambio, para que
   dentro de tres años se pueda saber con qué criterio se anonimizó algo.
2. **Sólo sabe anonimizar, y no borrar lo que hay que conservar.** La contabilidad, la
   bitácora y las pruebas de tratamiento no se tocan: son justamente las que permiten
   responder por el tratamiento ante el titular o la Agencia. El informe dice qué conserva
   y por qué, en vez de cumplir el plazo borrando la prueba.

Y una tercera, sobre cómo se mide el tiempo: la tabla de causas no tiene fecha de cierre,
así que el corte se calcula por **último movimiento** (el rastro más reciente de cada
expediente). Es, además, como lo piensa un estudio: «este expediente no se toca desde…».
"""
from __future__ import annotations

import datetime as dt

from . import auth, titulares
from .db import DB

#: Permiso para ver y cambiar la política: es una decisión del estudio, no del día a día.
PERMISO = "usuario.gestionar"

#: Sugerencias del sistema, con el anclaje que las justifica cuando existe. NO son
#: obligaciones legales de borrar: son motivos para conservar al menos ese tiempo, que el
#: estudio puede alargar (o acortar, si su criterio lo respalda).
SUGERENCIAS: dict[str, dict] = {
    "datos_de_persona": {
        "meses": 60,
        "motivo": (
            "5 años: el plazo de prescripción ordinaria de las acciones civiles (art. 2515 "
            "del Código Civil) es el horizonte con el que un estudio suele necesitar el "
            "expediente para defenderse. Sugerencia del sistema: el estudio lo fija."
        ),
    },
    "documentos": {
        "meses": 60,
        "motivo": "misma razón que los datos del expediente; además llevan hash de integridad",
    },
}

#: Tablas que se conservan siempre, con el motivo que va escrito en el informe.
CONSERVADO = (
    ("honorarios", "registro contable del estudio"),
    ("gastos", "registro contable del estudio"),
    ("auditoria", "bitácora: prueba del tratamiento y del acceso"),
    ("transferencias_ia", "prueba de licitud de las comunicaciones a modelos"),
    ("autorizaciones_ia", "papel que respalda el tratamiento con IA"),
)


class ErrorRetencion(ValueError):
    """La política no se puede aplicar como viene."""


# ------------------------------------------------------------------- política
def politica(db: DB) -> dict[str, dict]:
    """La política vigente. Si un tipo no fue declarado, se informa la sugerencia."""
    declarada = {
        fila["tipo"]: {
            "meses": int(fila["meses"]),
            "motivo": fila["motivo"],
            "actualizado_en": fila["actualizado_en"],
            "origen": "declarada por el estudio",
        }
        for fila in db.todos("SELECT * FROM retencion_politica ORDER BY tipo")
    }
    for tipo, sugerencia in SUGERENCIAS.items():
        if tipo not in declarada:
            declarada[tipo] = {**sugerencia, "actualizado_en": None, "origen": "sugerencia del sistema"}
    return declarada


def definir(db: DB, usuario: dict, tipo: str, meses: int, motivo: str) -> None:
    """Declara el plazo de un tipo de dato. Queda con fecha y en la bitácora."""
    auth.exigir(db, usuario, PERMISO)
    if tipo not in SUGERENCIAS:
        raise ErrorRetencion(f"tipo desconocido: {tipo} (conocidos: {', '.join(SUGERENCIAS)})")
    if int(meses) <= 0:
        raise ErrorRetencion("el plazo tiene que ser mayor que cero meses")
    if not motivo or not motivo.strip():
        raise ErrorRetencion("falta el motivo: el plazo tiene que ser explicable después")
    existente = db.uno("SELECT tipo FROM retencion_politica WHERE tipo = ?", (tipo,))
    if existente:
        db.ejecutar(
            "UPDATE retencion_politica SET meses = ?, motivo = ?, actualizado_en = ? WHERE tipo = ?",
            (int(meses), motivo.strip(), dt.datetime.now().isoformat(timespec="seconds"), tipo),
        )
    else:
        db.insertar(
            "retencion_politica",
            {"tipo": tipo, "meses": int(meses), "motivo": motivo.strip()},
        )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "retencion.definir", "retencion_politica", None,
        f"{tipo}={meses} meses · {motivo.strip()[:80]}",
    )


# ---------------------------------------------------------------- movimiento
def _ultimo_movimiento(db: DB, causa_ids: list[int]) -> str | None:
    """El rastro más reciente de un conjunto de causas (o None si no hay ninguno)."""
    if not causa_ids:
        return None
    marcas = ",".join("?" * len(causa_ids))
    momentos: list[str] = []
    for tabla in ("plazos", "audiencias", "documentos"):
        fila = db.uno(
            f"SELECT MAX(creado_en) AS ultimo FROM {tabla} WHERE causa_id IN ({marcas})",
            tuple(causa_ids),
        )
        if fila and fila.get("ultimo"):
            momentos.append(str(fila["ultimo"]))
    fila = db.uno(
        f"SELECT MAX(creado_en) AS ultimo FROM auditoria WHERE entidad = 'causas' "
        f"AND entidad_id IN ({marcas})",
        tuple(causa_ids),
    )
    if fila and fila.get("ultimo"):
        momentos.append(str(fila["ultimo"]))
    fila = db.uno(f"SELECT MAX(creado_en) AS ultimo FROM causas WHERE id IN ({marcas})", tuple(causa_ids))
    if fila and fila.get("ultimo"):
        momentos.append(str(fila["ultimo"]))
    return max(momentos) if momentos else None


def _corte(meses: int, hoy: dt.date | None = None) -> dt.date:
    """La fecha de corte: lo que no se mueve desde antes de esto, ya cumplió su plazo."""
    base = hoy or dt.date.today()
    meses = int(meses)
    anio, mes = divmod(base.year * 12 + (base.month - 1) - meses, 12)
    return dt.date(anio, mes + 1, min(base.day, 28))


def personas_sin_movimiento(db: DB, meses: int, hoy: dt.date | None = None) -> list[dict]:
    """Personas cuyo expediente no se mueve desde antes del corte."""
    corte = _corte(meses, hoy)
    resultado = []
    for cliente in db.todos("SELECT * FROM clientes ORDER BY id"):
        causas = db.todos("SELECT id FROM causas WHERE cliente_id = ?", (cliente["id"],))
        ultimo = _ultimo_movimiento(db, [c["id"] for c in causas])
        if causas and ultimo and ultimo[:10] < corte.isoformat():
            resultado.append(
                {
                    "cliente_id": cliente["id"],
                    "nombre": cliente["nombre"],
                    "causas": [c["id"] for c in causas],
                    "ultimo_movimiento": ultimo[:10],
                    "dias_sin_movimiento": (dt.date.today() - dt.date.fromisoformat(ultimo[:10])).days,
                }
            )
    return resultado


# -------------------------------------------------------------------- informes
def informe(db: DB, hoy: dt.date | None = None) -> dict:
    """Qué está cumplido, qué se haría y qué se conserva. No escribe nada."""
    tarifas = politica(db)
    meses = int(tarifas["datos_de_persona"]["meses"])
    personas = personas_sin_movimiento(db, meses, hoy)
    pendiente = [
        {
            "tipo": "datos_de_persona",
            "cliente_id": p["cliente_id"],
            "nombre": p["nombre"],
            "causas": p["causas"],
            "ultimo_movimiento": p["ultimo_movimiento"],
            "dias_sin_movimiento": p["dias_sin_movimiento"],
            "se_haria": "anonimizar los identificadores directos y redactar el nombre en los textos",
        }
        for p in personas
    ]
    conservado = []
    for tabla, motivo in CONSERVADO:
        conservado.append({"tabla": tabla, "filas": len(db.todos(f"SELECT id FROM {tabla}")), "motivo": motivo})
    return {
        "politica": tarifas,
        "corte_meses": meses,
        "cumplidos": pendiente,
        "total": len(pendiente),
        "conservado": conservado,
        "aviso": (
            "El corte se mide por último movimiento del expediente, no por una fecha de cierre "
            "(la tabla de causas no tiene esa fecha). Antes de aplicar, revisá la lista: la "
            "anonimización es por coincidencia del nombre y no detecta menciones indirectas."
        ),
    }


def aplicar(
    db: DB,
    usuario: dict,
    motivo: str,
    hoy: dt.date | None = None,
    simular: bool = True,
) -> dict:
    """Anonimiza a las personas que ya cumplieron su plazo. Simula salvo que se pida.

    Lo que NO hace, y por qué: no borra honorarios, gastos, bitácora ni pruebas de
    tratamiento de IA. Son las que permiten responder por el tratamiento; el informe lo
    dice fila por fila en vez de dejar el vacío.
    """
    auth.exigir(db, usuario, PERMISO)
    if not motivo or not motivo.strip():
        raise ErrorRetencion("falta el motivo: una anonimización por retención tiene que ser explicable")
    datos = informe(db, hoy)
    resultado = {"simulado": simular, "motivo": motivo, "anonimizados": [], "conservado": datos["conservado"]}
    for cumplido in datos["cumplidos"]:
        if simular:
            resultado["anonimizados"].append({"cliente_id": cumplido["cliente_id"], "nombre": cumplido["nombre"], "hecho": False})
            continue
        informe_titular = titulares.anonimizar(
            db, usuario, nombre=cumplido["nombre"], motivo=f"retención cumplida: {motivo}"
        )
        resultado["anonimizados"].append(
            {
                "cliente_id": cumplido["cliente_id"],
                "nombre": cumplido["nombre"],
                "hecho": True,
                "textos_redactados": len(informe_titular["textos_redactados"]),
                "identificadores_borrados": sum(len(f["cambios"]) for f in informe_titular["identificadores_borrados"]),
            }
        )
    if not simular and resultado["anonimizados"]:
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "retencion.aplicar", "clientes", None,
            f"{len(resultado['anonimizados'])} titular(es) · motivo: {motivo.strip()}",
        )
        resultado["auditado"] = True
    return resultado
