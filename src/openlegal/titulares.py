"""Derechos de los titulares: acceso/portabilidad y supresion, ejecutables.

El procedimiento escrito (docs/procedimiento_arspob.md) promete dos cosas que hay que
poder hacer de verdad cuando un titular las pide, dentro del plazo de 30 dias corridos
del art. 11 prorrogable una sola vez:

  exportar()    -> todo lo que el sistema tiene de una persona, en un archivo JSON con
                   su hash, para responder el derecho de acceso y de portabilidad.
  anonimizar()  -> borra los identificadores directos y redacta el nombre en los textos
                   libres, conservando lo que hay que conservar (contabilidad, bitacora
                   y prueba del propio tratamiento). Con simular=True no escribe nada:
                   informa que cambiaria.

Dos decisiones que conviene tener presentes:

1. La anonimizacion es por coincidencia del nombre y el RUT. No detecta menciones
   indirectas (iniciales, roles, "la senora del 4B") ni el contenido de los PDF: eso
   lo tiene que revisar una persona, y el informe lo dice en vez de callarlo.
2. Nada de esto se expone por MCP a proposito. Una operacion destructiva no puede
   quedar al alcance de un modelo que alucina argumentos.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import unicodedata

from . import auth
from .db import DB

#: Permiso que exigen las dos operaciones (socio y abogado; no administrativos: la
#: respuesta a un titular es un acto juridico, no una tarea de facturacion).
PERMISO = "titular.gestionar"

#: Reemplazo de todo identificador directo. Irreversible por si solo, pero estable:
#: permite seguir viendo que ese cliente existio, y que se le anonimizo.
TOKEN = "[TITULAR ANONIMIZADO]"

#: Campos de `clientes` que identifican a una persona.
CAMPOS_IDENTIFICADORES = ("nombre", "rut", "email", "telefono", "direccion", "representante_legal")

#: Tablas hijas de la causa que viajan con ella en la exportacion.
TABLAS_DE_CAUSA = ("plazos", "audiencias", "documentos", "honorarios", "gastos")

#: Textos libres donde puede aparecer el nombre de una persona, y que por lo mismo hay
#: que redactar al anonimizar. (tabla, campo)
TEXTOS_LIBRES = (
    ("causas", "observaciones"),
    ("causas", "contraparte"),
    ("plazos", "descripcion"),
    ("audiencias", "minuta"),
    ("documentos", "nombre"),
)

#: Lo que NO se borra, con el motivo que va escrito en el informe.
CONSERVADO = (
    ("honorarios", "registro contable del estudio: la obligacion de conservar respaldos manda sobre el borrado"),
    ("gastos", "registro contable del estudio, por lo mismo que los honorarios"),
    ("auditoria", "bitacora de quien hizo que: es la prueba del tratamiento y del acceso"),
    ("transferencias_ia", "prueba de licitud de las comunicaciones a terceros (arts. 14 ter h) y 11)"),
    ("autorizaciones_ia", "papel que respalda el tratamiento: hay que poder mostrarlo"),
)


class ErrorTitular(ValueError):
    """La solicitud no se puede atender como viene (falta el motivo, no hay titular)."""


# ------------------------------------------------------------------ herramientas
def _normalizar(texto: str) -> str:
    """Sin acentos, sin mayusculas y con espacios simples."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def _limpiar_rut(rut: str) -> str:
    """Solo digitos y K, para que 11.111.111-1 y 11111111-1 sean el mismo rut."""
    return re.sub(r"[^0-9kK]", "", rut).upper()


def _clientes(db: DB, estudio_id: int, rut: str | None, nombre: str | None, email: str | None) -> list[dict]:
    """Filas de `clientes` que calzan con al menos un identificador."""
    if not any((rut, nombre, email)):
        raise ErrorTitular("hay que dar al menos un identificador: --rut, --nombre o --email")
    filas: list[dict] = []
    vistos: set[int] = set()
    if rut:
        objetivo = _limpiar_rut(rut)
        for fila in db.todos("SELECT * FROM clientes WHERE estudio_id = ?", (estudio_id,)):
            if fila.get("rut") and _limpiar_rut(str(fila["rut"])) == objetivo:
                filas.append(fila)
                vistos.add(fila["id"])
    if nombre:
        # El LIKE de SQLite no ignora los acentos, asi que se compara en memoria:
        # "Perez" tiene que encontrar a "Pérez", y al revés.
        aguja = _normalizar(nombre)
        for fila in db.todos("SELECT * FROM clientes WHERE estudio_id = ?", (estudio_id,)):
            if aguja in _normalizar(str(fila.get("nombre") or "")) and fila["id"] not in vistos:
                filas.append(fila)
                vistos.add(fila["id"])
    if email:
        aguja = email.strip().lower()
        for fila in db.todos("SELECT * FROM clientes WHERE estudio_id = ?", (estudio_id,)):
            if aguja in str(fila.get("email") or "").lower() and fila["id"] not in vistos:
                filas.append(fila)
                vistos.add(fila["id"])
    return filas


def _causas(db: DB, cliente_ids: list[int]) -> list[dict]:
    if not cliente_ids:
        return []
    marcas = ",".join("?" * len(cliente_ids))
    return db.todos(f"SELECT * FROM causas WHERE cliente_id IN ({marcas}) ORDER BY id", tuple(cliente_ids))


def _hijas(db: DB, tabla: str, causa_ids: list[int]) -> list[dict]:
    if not causa_ids:
        return []
    marcas = ",".join("?" * len(causa_ids))
    return db.todos(f"SELECT * FROM {tabla} WHERE causa_id IN ({marcas}) ORDER BY id", tuple(causa_ids))


def _textos_con(db: DB, agujas: list[str]) -> list[tuple[str, int, str, str]]:
    """(tabla, id, campo, valor) de los textos libres donde aparece alguna aguja."""
    encontrados: list[tuple[str, int, str, str]] = []
    normalizadas = [_normalizar(a) for a in agujas if a]
    if not normalizadas:
        return encontrados
    for tabla, campo in TEXTOS_LIBRES:
        for fila in db.todos(f"SELECT id, {campo} AS valor FROM {tabla}"):
            valor = fila.get("valor") or ""
            if valor and any(aguja in _normalizar(str(valor)) for aguja in normalizadas):
                encontrados.append((tabla, fila["id"], campo, str(valor)))
    return encontrados


def _reemplazar_nombre(valor: str, nombre: str) -> tuple[str, int]:
    """Cambia el nombre por el token sin depender de acentos ni de mayusculas.

    Dos pasadas: primero la coincidencia exacta (el caso normal), y si no hubo, una
    por palabras normalizadas, que es la que rescata "Perez" en el expediente cuando el
    cliente se llama "Pérez". Devuelve el texto nuevo y cuantas veces reemplazo.
    """
    if not nombre:
        return valor, 0
    exacto, veces = re.compile(re.escape(nombre), re.IGNORECASE).subn(TOKEN, valor)
    if veces:
        return exacto, veces

    # Por palabras: se busca la corrida de palabras que, normalizadas, iguala el nombre.
    partes = [_normalizar(p) for p in nombre.split() if p]
    if not partes:
        return valor, 0
    palabras = list(re.finditer(r"\S+", valor))
    reemplazos: list[tuple[int, int]] = []
    for i in range(len(palabras) - len(partes) + 1):
        corrida = [_normalizar(palabras[i + j].group()) for j in range(len(partes))]
        if corrida == partes:
            reemplazos.append((palabras[i].start(), palabras[i + len(partes) - 1].end()))
    for inicio, fin in reversed(reemplazos):
        valor = valor[:inicio] + TOKEN + valor[fin:]
    return valor, len(reemplazos)


def _hash_archivo(ruta: pathlib.Path) -> tuple[int, str]:
    datos = ruta.read_bytes()
    return len(datos), hashlib.sha256(datos).hexdigest()


def _bitacora_del_titular(db: DB, estudio_id: int, agujas: list[str]) -> list[dict]:
    """Entradas de auditoria que mencionan al titular (por nombre o por rut)."""
    condiciones = " OR ".join("detalle LIKE ?" for _ in agujas if _)
    parametros = [f"%{a}%" for a in agujas if a]
    if not condiciones:
        return []
    return db.todos(
        f"SELECT * FROM auditoria WHERE estudio_id = ? AND ({condiciones}) ORDER BY id",
        (estudio_id, *parametros),
    )


# ------------------------------------------------------------------- exportar
def buscar(
    db: DB,
    usuario: dict,
    *,
    rut: str | None = None,
    nombre: str | None = None,
    email: str | None = None,
    texto: str | None = None,
) -> list[dict]:
    """Busca titulares por RUT, nombre, correo o texto libre.

    Es la puerta del módulo de datos del panel: el mismo criterio que usa `exportar`, para
    que lo que se ve en pantalla sea exactamente lo que se va a entregar o a anonimizar.
    """
    auth.exigir(db, usuario, PERMISO)
    if texto and not (rut or nombre or email):
        aguja = _normalizar(texto)
        # Se consulta directo y no se pasa por `_clientes`: esa exige un identificador, y acá
        # el criterio es justamente el texto libre (nombre, RUT o correo).
        todos = db.todos(
            "SELECT * FROM clientes WHERE estudio_id = ? ORDER BY nombre", (usuario["estudio_id"],)
        )
        return [
            fila for fila in todos
            if aguja in _normalizar(fila.get("nombre") or "")
            or aguja in _limpiar_rut(fila.get("rut") or "")
            or aguja in _normalizar(fila.get("email") or "")
        ]
    return _clientes(db, usuario["estudio_id"], rut, nombre, email)


def exportar(
    db: DB,
    usuario: dict,
    *,
    rut: str | None = None,
    nombre: str | None = None,
    email: str | None = None,
    destino: str | None = None,
) -> dict:
    """Arma el expediente completo de un titular y lo deja en un JSON con su hash.

    Responde el derecho de acceso y el de portabilidad con el mismo archivo. Los
    documentos escaneados no se copian adentro (son binarios y pueden pesar cientos de
    MB): se listan con ruta, tamano y hash, y el propio archivo lo advierte.
    """
    auth.exigir(db, usuario, PERMISO)
    clientes = _clientes(db, usuario["estudio_id"], rut, nombre, email)
    if not clientes:
        raise ErrorTitular("no hay ningun cliente que calce con ese identificador en este estudio")

    causas = _causas(db, [c["id"] for c in clientes])
    expediente: list[dict] = []
    for causa in causas:
        ficha = dict(causa)
        for tabla in TABLAS_DE_CAUSA:
            ficha[tabla] = _hijas(db, tabla, [causa["id"]])
        ficha["autorizaciones_ia"] = db.todos(
            "SELECT * FROM autorizaciones_ia WHERE causa_id = ? ORDER BY id", (causa["id"],)
        )
        ficha["transferencias_ia"] = db.todos(
            "SELECT * FROM transferencias_ia WHERE causa_id = ? ORDER BY id", (causa["id"],)
        )
        expediente.append(ficha)

    respaldos = []
    for causa in expediente:
        for documento in causa["documentos"]:
            ruta = documento.get("ruta")
            ficha = {"causa_id": causa["id"], "documento_id": documento["id"], "nombre": documento["nombre"], "ruta": ruta}
            if ruta and pathlib.Path(str(ruta)).is_file():
                bytes_, sha = _hash_archivo(pathlib.Path(str(ruta)))
                ficha.update({"existe": True, "bytes": bytes_, "sha256": sha})
            else:
                ficha["existe"] = False
            respaldos.append(ficha)

    agujas = [c.get("nombre") for c in clientes] + [c.get("rut") for c in clientes]
    entradas = _bitacora_del_titular(db, usuario["estudio_id"], [str(a) for a in agujas if a])

    paquete = {
        "aviso": (
            "Este archivo reune lo que el sistema tiene registrado. Los documentos escaneados NO van "
            "adentro: se listan en `respaldos` con ruta, tamano y hash. Si se entrega al titular como "
            "portabilidad, hay que adjuntar esos archivos aparte."
        ),
        "generado_en": dt.datetime.now().isoformat(timespec="seconds"),
        "generado_por": usuario["email"],
        "estudio_id": usuario["estudio_id"],
        "criterio": {"rut": rut, "nombre": nombre, "email": email},
        "titular": clientes,
        "causas": expediente,
        "respaldos": respaldos,
        "auditoria": entradas,
    }
    crudo = json.dumps(paquete, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    sha = hashlib.sha256(crudo).hexdigest()

    if destino:
        ruta_salida = pathlib.Path(destino)
    else:
        sello = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", _normalizar(nombre or rut or email or "titular")).strip("-") or "titular"
        ruta_salida = pathlib.Path.home() / ".openlegal" / "arsopb" / f"titular-{slug}-{sello}.json"
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_bytes(crudo)

    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "titular.exportar", "clientes",
        clientes[0]["id"], f"criterio={rut or nombre or email} causas={len(causas)} sha256={sha}",
    )
    return {
        "archivo": str(ruta_salida),
        "sha256": sha,
        "bytes": len(crudo),
        "titulares": len(clientes),
        "causas": len(causas),
        "respaldos": len(respaldos),
        "respaldos_ausentes": sum(1 for r in respaldos if not r.get("existe")),
        "entradas_auditoria": len(entradas),
    }


# ----------------------------------------------------------------- anonimizar
def anonimizar(
    db: DB,
    usuario: dict,
    *,
    rut: str | None = None,
    nombre: str | None = None,
    email: str | None = None,
    motivo: str,
    simular: bool = False,
    redactar_textos: bool = True,
) -> dict:
    """Borra los identificadores directos del titular y redacta su nombre en los textos.

    Conserva honorarios, gastos, auditoria y las pruebas de tratamiento de IA, y lo
    informa con el motivo de cada una. Con `simular=True` devuelve el mismo informe sin
    escribir nada, para poder revisarlo antes de firmar la respuesta al titular.
    """
    auth.exigir(db, usuario, PERMISO)
    if not motivo or not motivo.strip():
        raise ErrorTitular("falta el motivo: hay que poder decir por que se anonimiza (queda en la bitacora)")
    clientes = _clientes(db, usuario["estudio_id"], rut, nombre, email)
    if not clientes:
        raise ErrorTitular("no hay ningun cliente que calce con ese identificador en este estudio")

    causas = _causas(db, [c["id"] for c in clientes])
    causa_ids = [c["id"] for c in causas]
    agujas = [str(a) for a in (clientes[0].get("nombre"), clientes[0].get("rut")) if a]

    # 1. Identificadores directos de la ficha del cliente.
    plan_clientes = []
    for cliente in clientes:
        cambios = {}
        for campo in CAMPOS_IDENTIFICADORES:
            valor = cliente.get(campo)
            if valor not in (None, ""):
                cambios[campo] = {
                    "antes": valor,
                    "despues": None if campo != "nombre" else f"{TOKEN} #{cliente['id']}",
                }
        if cambios:
            plan_clientes.append({"id": cliente["id"], "cambios": cambios})

    # 2. Textos libres donde aparece su nombre. El RUT no se redacta: aparece citado como
    #    dato del expediente y borrarlo lo rompe sin anonimizar a nadie (el numero solo
    #    no identifica si no esta el nombre).
    plan_textos = []
    if redactar_textos and clientes[0].get("nombre"):
        for tabla, id_, campo, valor in _textos_con(db, [clientes[0]["nombre"]]):
            nuevo, reemplazos = _reemplazar_nombre(valor, clientes[0]["nombre"])
            if reemplazos:
                plan_textos.append(
                    {"tabla": tabla, "id": id_, "campo": campo, "antes": valor, "despues": nuevo, "reemplazos": reemplazos}
                )

    # 3. Lo que se conserva, con su motivo y su volumen.
    conservado = []
    for tabla, razon in CONSERVADO:
        if tabla in ("honorarios", "gastos"):
            filas = len(_hijas(db, tabla, causa_ids))
        elif tabla == "auditoria":
            filas = len(_bitacora_del_titular(db, usuario["estudio_id"], agujas))
        else:
            filas = len(_hijas(db, tabla, causa_ids))
        conservado.append({"tabla": tabla, "filas": filas, "motivo": razon})

    informe = {
        "simulado": simular,
        "motivo": motivo,
        "titulares": [c["id"] for c in clientes],
        "causas": causa_ids,
        "identificadores_borrados": plan_clientes,
        "textos_redactados": plan_textos,
        "conservado": conservado,
        "aviso": (
            "La redaccion es por coincidencia del nombre: no detecta iniciales, roles ni menciones "
            "indirectas, ni el texto dentro de los PDF. Revise los expedientes alcanzados."
        ),
    }
    if simular:
        return informe

    for ficha in plan_clientes:
        for campo, cambio in ficha["cambios"].items():
            db.ejecutar(f"UPDATE clientes SET {campo} = ? WHERE id = ?", (cambio["despues"], ficha["id"]))
    for cambio in plan_textos:
        db.ejecutar(
            f"UPDATE {cambio['tabla']} SET {cambio['campo']} = ? WHERE id = ?",
            (cambio["despues"], cambio["id"]),
        )

    detalle = json.dumps(
        {
            "motivo": motivo,
            "identificadores": [f["id"] for f in plan_clientes],
            "textos": len(plan_textos),
            "conservado": {c["tabla"]: c["filas"] for c in conservado},
        },
        ensure_ascii=False,
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "titular.anonimizar", "clientes",
        clientes[0]["id"], detalle,
    )
    informe["auditado"] = True
    return informe
