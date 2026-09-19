"""Honorarios, gastos, pagos y la cuenta de dividendos de una causa.

Es la parte del CRM donde es más fácil inventar un dato, así que conviene decir de
entrada qué hace y qué no:

- Los montos son **enteros en CLP**: sin decimales y sin puntos (350000 son $350.000).
  Un monto con decimales se rechaza en vez de redondearse por cuenta propia.
- La **retención del SII no se calcula ni se codifica**: es un dato que el estudio copia
  de su boleta y llega como argumento (`retencion_sii`). Si no viene, el líquido queda
  igual al bruto y la cuenta lo advierte por escrito. Una tasa supuesta en el código
  produciría cuentas que parecen correctas y no lo son.
- **No hay integración con el SII ni emisión de boletas**: el CRM registra lo que el
  estudio declara, no tributa por él. La cuenta es un estado de cuenta interno.
- Nada se borra: un gasto o un pago mal cargado es un problema del estudio y se corrige
  con otro registro, nunca eliminando la fila (la obligación de conservar respaldos
  contables manda sobre las ganas de limpiar).

Todo lo que escribe pasa por `auth.exigir` y deja fila en `auditoria`.
"""
from __future__ import annotations

import datetime as dt
import html

from . import auth
from .db import DB

MODALIDADES = ("fijo", "hora", "cuota_litis", "mixto")
MEDIOS = ("transferencia", "efectivo", "cheque", "tarjeta", "otro")

ADVERTENCIA_NO_TRIBUTARIA = (
    "esta cuenta es un estado de cuenta interno del estudio: no es un documento "
    "tributario y no reemplaza la boleta ni la factura de honorarios."
)

ADVERTENCIA_SIN_RETENCION = (
    "no se declaró retención en uno o más honorarios: el líquido quedó igual al bruto. "
    "El CRM no calcula la tasa —la copia el estudio de su boleta y llega en `retencion_sii`—. "
    "Si corresponde retención, corrígela después de emitir la boleta."
)

ADVERTENCIA_SIN_RETENCION_DE_UNO = (
    "el líquido quedó igual al bruto porque no se declaró retención: la tasa la copia el estudio "
    "de su boleta y se registra en `retencion_sii`. El CRM no la calcula (y no hay integración "
    "con el SII)."
)


def clp(valor: int | None) -> str:
    """Monto en pesos, como se escribe acá: $350.000. `None` no es cero, es «sin dato»."""
    if valor is None:
        return "—"
    numero = int(valor)
    signo = "-" if numero < 0 else ""
    return signo + "$" + f"{abs(numero):,}".replace(",", ".")


def _entero_positivo(valor, campo: str, permite_cero: bool = False) -> int:
    """Convierte a entero CLP y rechaza lo que no sirve, con el motivo escrito."""
    try:
        numero = int(valor)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{campo} tiene que ser un entero en CLP (sin decimales): {valor!r}") from exc
    if numero < 0 or (numero == 0 and not permite_cero):
        minimo = "0" if permite_cero else "1"
        raise ValueError(f"{campo} tiene que ser mayor o igual a {minimo} CLP: {numero}")
    return numero


def _hoy() -> str:
    return dt.date.today().isoformat()


# ----------------------------------------------------------------- honorarios
def registrar_honorario(
    db: DB,
    usuario: dict,
    causa_id: int,
    modalidad: str,
    monto_pactado: int | None = None,
    descripcion: str | None = None,
    fecha: str | None = None,
    monto_bruto: int | None = None,
    retencion_sii: int | None = None,
) -> int:
    """Registra un honorario pactado en una causa y devuelve su id.

    El líquido se calcula con lo que el estudio declara: `monto_liquido = bruto -
    retencion_sii`. Si no llega la retención, el líquido es el bruto — y quien pida la
    cuenta verá la advertencia, porque el CRM no puede saber si falta un dato o si el
    honorario realmente no está afecto a retención.

    El honorario nace `pendiente`: todavía no hay plata recibida. El estado lo mueven los
    pagos (ver `registrar_pago`), no una suposición.
    """
    auth.exigir(db, usuario, "honorario.editar", causa_id)
    modalidad = str(modalidad or "").strip().lower()
    if modalidad not in MODALIDADES:
        raise ValueError(f"modalidad inválida: {modalidad!r} (válidas: {', '.join(MODALIDADES)})")

    pactado = _entero_positivo(monto_pactado, "monto_pactado", permite_cero=True) if monto_pactado is not None else None
    bruto = _entero_positivo(monto_bruto, "monto_bruto", permite_cero=True) if monto_bruto is not None else None
    retencion = _entero_positivo(retencion_sii, "retencion_sii", permite_cero=True) if retencion_sii is not None else None
    if retencion is not None and bruto is None:
        raise ValueError(
            "declaraste una retención sin monto bruto: la retención se descuenta de la boleta, "
            "así que hace falta el bruto del que sale (o registra sólo el pactado, sin retención)"
        )
    liquido = bruto if bruto is None else bruto - (retencion or 0)
    if liquido is not None and liquido < 0:
        raise ValueError(
            f"la retención ({clp(retencion)}) es mayor que el bruto ({clp(bruto)}): "
            "revisa los montos que salen de la boleta"
        )

    honorario_id = db.insertar(
        "honorarios",
        {
            "causa_id": causa_id,
            "modalidad": modalidad,
            "monto_pactado": pactado,
            "monto_bruto": bruto,
            "retencion_sii": retencion,
            "monto_liquido": liquido,
            "monto_pagado": 0,
            "estado_pago": "pendiente",
            "descripcion": (descripcion or "").strip() or None,
            "fecha": fecha or _hoy(),
        },
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "honorario.crear", "honorarios", honorario_id,
        f"causa={causa_id} modalidad={modalidad} pactado={pactado} bruto={bruto} "
        f"retencion={retencion if retencion is not None else 'no declarada'} liquido={liquido}",
    )
    return honorario_id


# ---------------------------------------------------------------------- gastos
def registrar_gasto(
    db: DB,
    usuario: dict,
    causa_id: int,
    concepto: str,
    monto: int,
    fecha: str | None = None,
    comprobante: str | None = None,
    pagado_por_estudio: bool = True,
) -> int:
    """Registra un gasto de la causa y devuelve su id.

    `pagado_por_estudio=True` significa que lo adelantó el estudio y por lo tanto se le
    cuenta al cliente en la cuenta de dividendos; en `False` lo pagó el cliente y sólo
    queda constancia. Si el respaldo no está a mano, `comprobante` va con el nombre del
    papel que existe en la carpeta: es mejor un dato incompleto dicho que un campo vacío
    que parezca verificado.
    """
    auth.exigir(db, usuario, "gasto.editar", causa_id)
    concepto = (concepto or "").strip()
    if not concepto:
        raise ValueError("el gasto necesita un concepto: qué se pagó (notaría, receptor, tasas, peritaje…)")
    monto_clp = _entero_positivo(monto, "monto")
    gasto_id = db.insertar(
        "gastos",
        {
            "causa_id": causa_id,
            "concepto": concepto,
            "monto": monto_clp,
            "pagado_por_estudio": 1 if pagado_por_estudio else 0,
            "fecha": fecha or _hoy(),
            "comprobante": (comprobante or "").strip() or None,
        },
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "gasto.crear", "gastos", gasto_id,
        f"causa={causa_id} {concepto} {clp(monto_clp)} "
        f"por_cuenta_del_cliente={'si' if pagado_por_estudio else 'no'} "
        f"comprobante={comprobante or 'sin respaldo cargado'}",
    )
    return gasto_id


# ---------------------------------------------------------------------- pagos
def registrar_pago(
    db: DB,
    usuario: dict,
    causa_id: int,
    monto: int,
    fecha: str | None = None,
    medio: str = "transferencia",
    referencia: str | None = None,
    nota: str | None = None,
    honorario_id: int | None = None,
) -> int:
    """Registra un abono del cliente y devuelve su id.

    Un pago es un acto con consecuencias —baja lo que el cliente debe y queda en la
    bitácora con el nombre de quien lo escribió—, así que acá no se completa nada por
    cuenta propia: el monto y la fecha son los del comprobante.

    Si viene `honorario_id`, tiene que ser **de esta causa**: imputar el pago de una causa
    al honorario de otra descuadra dos cuentas a la vez. Después de guardar, el honorario
    imputado se recalcula (`monto_pagado` y `estado_pago`).
    """
    auth.exigir(db, usuario, "honorario.editar", causa_id)
    monto_clp = _entero_positivo(monto, "monto")
    medio = (medio or "").strip().lower()
    if medio not in MEDIOS:
        raise ValueError(f"medio de pago desconocido: {medio!r} (válidos: {', '.join(MEDIOS)})")

    imputado: int | None = None
    if honorario_id is not None:
        try:
            imputado = int(honorario_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"honorario_id tiene que ser un número entero: {honorario_id!r}") from exc
        honorario = db.uno("SELECT id, causa_id FROM honorarios WHERE id = ?", (imputado,))
        if not honorario:
            raise ValueError(f"no existe el honorario {imputado}: revisa el listado antes de imputar el pago")
        if int(honorario["causa_id"]) != int(causa_id):
            raise ValueError(
                f"el honorario {imputado} es de la causa {honorario['causa_id']}, no de la causa {causa_id}: "
                "un pago no puede cancelar el honorario de otra causa"
            )

    fecha_pago = fecha or _hoy()
    pago_id = db.insertar(
        "pagos",
        {
            "causa_id": causa_id,
            "honorario_id": imputado,
            "fecha": fecha_pago,
            "monto": monto_clp,
            "medio": medio,
            "referencia": (referencia or "").strip() or None,
            "nota": (nota or "").strip() or None,
            "registrado_por": usuario["id"],
        },
    )
    auth.auditar(
        db, usuario["estudio_id"], usuario["id"], "pago.crear", "pagos", pago_id,
        f"causa={causa_id} {clp(monto_clp)} fecha={fecha_pago} medio={medio} "
        f"honorario={imputado if imputado is not None else 'sin imputar'} "
        f"referencia={referencia or 's/informar'}",
    )
    if imputado is not None:
        recalcular_honorario(db, imputado)
    return pago_id


def recalcular_honorario(db: DB, honorario_id: int) -> dict:
    """Vuelve a sumar los pagos imputados y deja el estado del honorario al día.

    La referencia para decir «pagado» es el líquido. Si no hay líquido se usa el pactado
    y, si tampoco hay monto de referencia, el estado no puede pasar de `parcial`: con
    plata recibida pero sin un monto contra el cual compararla, afirmar «pagado» sería
    inventar.
    """
    honorario = db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
    if not honorario:
        raise ValueError(f"no existe el honorario {honorario_id}")
    fila = db.uno(
        "SELECT COALESCE(SUM(monto), 0) AS total FROM pagos WHERE honorario_id = ?", (int(honorario_id),)
    )
    total = int((fila or {}).get("total") or 0)
    referencia = honorario["monto_liquido"] if honorario["monto_liquido"] is not None else honorario["monto_pactado"]
    if total <= 0:
        estado = "pendiente"
    elif referencia is None or total < int(referencia):
        estado = "parcial" if total > 0 else "pendiente"
    else:
        estado = "pagado"
    db.ejecutar(
        "UPDATE honorarios SET monto_pagado = ?, estado_pago = ? WHERE id = ?",
        (total, estado, int(honorario_id)),
    )
    return {
        "honorario_id": int(honorario_id),
        "monto_pagado": total,
        "estado_pago": estado,
        "referencia": referencia,
    }


# ----------------------------------------------------------------- listados
def _causas_visibles(db: DB, usuario: dict) -> list[int]:
    return [int(c["id"]) for c in auth.causas_visibles(db, usuario)]


def _listar(db: DB, usuario: dict, tabla: str, permiso: str, causa_id: int | None, orden: str) -> list[dict]:
    """Listado acotado: con causa, esa causa; sin causa, sólo las causas del usuario."""
    if causa_id is not None:
        auth.exigir(db, usuario, permiso, causa_id)
        return db.todos(
            f"SELECT t.*, c.caratula FROM {tabla} t JOIN causas c ON c.id = t.causa_id "
            f"WHERE t.causa_id = ? ORDER BY {orden}",
            (causa_id,),
        )
    auth.exigir(db, usuario, permiso)
    visibles = _causas_visibles(db, usuario)
    if not visibles:
        return []
    marcadores = ", ".join("?" for _ in visibles)
    return db.todos(
        f"SELECT t.*, c.caratula FROM {tabla} t JOIN causas c ON c.id = t.causa_id "
        f"WHERE t.causa_id IN ({marcadores}) ORDER BY {orden}",
        tuple(sorted(visibles)),
    )


def listar_honorarios(db: DB, usuario: dict, causa_id: int | None = None) -> list[dict]:
    """Honorarios de una causa, o de todas las causas que este usuario puede ver."""
    return _listar(db, usuario, "honorarios", "honorario.leer", causa_id, "t.id DESC")


def listar_gastos(db: DB, usuario: dict, causa_id: int | None = None) -> list[dict]:
    """Gastos de una causa, o de las causas visibles (`gasto.leer`)."""
    return _listar(db, usuario, "gastos", "gasto.leer", causa_id, "t.fecha DESC, t.id DESC")


def listar_pagos(db: DB, usuario: dict, causa_id: int | None = None) -> list[dict]:
    """Pagos de una causa, o de las causas visibles. Los pagos son plata de honorarios:
    se leen con `honorario.leer`, no con `gasto.leer`."""
    return _listar(db, usuario, "pagos", "honorario.leer", causa_id, "t.fecha DESC, t.id DESC")


# ------------------------------------------------------------------- cuenta
def _por_cuenta_del_cliente(gasto: dict) -> bool:
    """¿Este gasto se le pasa al cliente? Sólo si lo adelantó el estudio y no se reembolsó."""
    return bool(int(gasto.get("pagado_por_estudio") or 0)) and not int(gasto.get("reembolsado") or 0)


def cuenta(db: DB, usuario: dict, causa_id: int) -> dict:
    """La cuenta de dividendos de una causa: qué se pactó, qué se gastó, qué se pagó y cuánto queda.

    El saldo es `(honorarios líquidos + gastos por cuenta del cliente) - pagos`. Se dicen
    por separado los gastos que se le pasan al cliente de los que no, porque un gasto que
    pagó el cliente no puede engordar lo que debe.

    Los honorarios sin retención declarada y un saldo negativo (el cliente pagó más de lo
    que hay cargado) salen como advertencias: son las dos formas en que esta cuenta puede
    estar incompleta sin que se note mirando los totales.
    """
    auth.exigir(db, usuario, "honorario.leer", causa_id)
    causa = db.uno("SELECT * FROM causas WHERE id = ?", (causa_id,))
    if not causa:
        raise ValueError(f"no existe la causa {causa_id} en este estudio")
    estudio = db.uno("SELECT * FROM estudios WHERE id = ?", (usuario["estudio_id"],)) or {}
    cliente = None
    if causa.get("cliente_id"):
        cliente = db.uno("SELECT * FROM clientes WHERE id = ?", (causa["cliente_id"],))

    honorarios = db.todos("SELECT * FROM honorarios WHERE causa_id = ? ORDER BY id", (causa_id,))
    gastos = db.todos("SELECT * FROM gastos WHERE causa_id = ? ORDER BY fecha, id", (causa_id,))
    pagos = db.todos(
        "SELECT p.*, u.nombre AS registrado_por_nombre FROM pagos p "
        "LEFT JOIN usuarios u ON u.id = p.registrado_por WHERE p.causa_id = ? ORDER BY p.fecha, p.id",
        (causa_id,),
    )

    pactado = sum(int(h.get("monto_pactado") or 0) for h in honorarios)
    liquido = sum(int(h.get("monto_liquido") or 0) for h in honorarios)
    pagado = sum(int(h.get("monto_pagado") or 0) for h in honorarios)
    gastos_total = sum(int(g.get("monto") or 0) for g in gastos)
    gastos_cliente = sum(int(g.get("monto") or 0) for g in gastos if _por_cuenta_del_cliente(g))
    pagos_total = sum(int(p.get("monto") or 0) for p in pagos)

    advertencias: list[str] = []
    sin_retencion = [h["id"] for h in honorarios if h.get("monto_bruto") is not None and h.get("retencion_sii") is None]
    if sin_retencion:
        advertencias.append(
            ADVERTENCIA_SIN_RETENCION
            + " Honorarios sin retención declarada: "
            + ", ".join(str(i) for i in sin_retencion)
            + "."
        )
    saldo = liquido + gastos_cliente - pagos_total
    sin_liquido = [h["id"] for h in honorarios if h.get("monto_liquido") is None]
    if sin_liquido:
        # Un honorario sin líquido no se puede sumar: entra al saldo como cero y el saldo queda
        # subestimado. Se dice, porque una cuenta que se ve prolija pero no cuadra es peor que
        # una que advierte lo que le falta.
        advertencias.append(
            "los honorarios " + ", ".join(str(i) for i in sin_liquido) + " no tienen monto líquido declarado "
            "(falta el bruto de la boleta o no hay monto pactado): no entran en el saldo, así que puede estar "
            "subestimado. Se arregla registrando el bruto y la retención cuando el estudio emita la boleta."
        )
    if saldo < 0:
        advertencias.append(
            f"el cliente pagó {clp(-saldo)} más de lo que hay cargado a su cuenta (saldo a favor): "
            "revisa si falta registrar un gasto o un honorario antes de rendir esta cuenta."
        )
    if any(p.get("honorario_id") is None for p in pagos) and any(
        int(h.get("monto_pagado") or 0) < int(h.get("monto_liquido") or h.get("monto_pactado") or 0)
        for h in honorarios
    ):
        advertencias.append(
            "hay pagos sin imputar a un honorario mientras quedan honorarios con saldo: "
            "imputarlos permite saber cuál quedó pagado (el total de la cuenta no cambia)."
        )
    advertencias.append(ADVERTENCIA_NO_TRIBUTARIA)

    return {
        "estudio": {"nombre": estudio.get("nombre"), "rut": estudio.get("rut")},
        "cliente": (
            {"nombre": cliente.get("nombre"), "rut": cliente.get("rut"), "direccion": cliente.get("direccion")}
            if cliente
            else None
        ),
        "causa": {
            "id": causa["id"],
            "caratula": causa.get("caratula"),
            "rol_rit": causa.get("rol_rit"),
            "tribunal": causa.get("tribunal"),
        },
        "honorarios": honorarios,
        "gastos": gastos,
        "pagos": pagos,
        "totales": {
            "honorarios_pactado": pactado,
            "honorarios_liquido": liquido,
            "honorarios_pagado": pagado,
            "gastos": gastos_total,
            "gastos_por_cuenta_del_cliente": gastos_cliente,
            "pagos": pagos_total,
            "saldo": saldo,
        },
        "advertencias": advertencias,
        "generado_en": dt.datetime.now().isoformat(timespec="seconds"),
    }


# -------------------------------------------------------- cuenta imprimible
def _texto(valor) -> str:
    if valor is None or str(valor).strip() == "":
        return "—"
    return html.escape(str(valor))


def _filas_html(filas: list[list[str]]) -> str:
    if not filas:
        return '<tr><td colspan="99" class="vacio">(sin registros)</td></tr>'
    return "\n".join("<tr>" + "".join(f"<td>{celda}</td>" for celda in fila) + "</tr>" for fila in filas)


def _tabla_html(titulo: str, cabeceras: list[str], filas: list[list[str]]) -> str:
    encabezado = "".join(f"<th>{html.escape(c)}</th>" for c in cabeceras)
    return (
        f'<h2>{html.escape(titulo)}</h2>\n'
        f'<table>\n<thead><tr>{encabezado}</tr></thead>\n<tbody>\n{_filas_html(filas)}\n</tbody>\n</table>'
    )


def html_cuenta(datos: dict) -> str:
    """El HTML imprimible de la cuenta de dividendos, en A4 y sin nada externo.

    Todo va embebido —estilos en el documento, sin JavaScript, sin imágenes ni fuentes
    remotas—: el archivo se tiene que poder abrir e imprimir en un notebook que nunca
    salió del estudio, sin red. No es un documento tributario y lo dice.
    """
    estudio = datos.get("estudio") or {}
    cliente = datos.get("cliente") or {}
    causa = datos.get("causa") or {}
    totales = datos.get("totales") or {}

    honorarios = [
        [
            _texto(h.get("id")),
            _texto(h.get("fecha")),
            _texto(h.get("modalidad")),
            _texto(h.get("descripcion")),
            clp(h.get("monto_pactado")),
            clp(h.get("monto_bruto")),
            clp(h.get("retencion_sii")),
            clp(h.get("monto_liquido")),
            clp(h.get("monto_pagado")),
            _texto(h.get("estado_pago")),
        ]
        for h in datos.get("honorarios") or []
    ]
    gastos = [
        [
            _texto(g.get("fecha")),
            _texto(g.get("concepto")),
            _texto(g.get("comprobante")),
            clp(g.get("monto")),
            "sí" if _por_cuenta_del_cliente(g) else "no (lo pagó el cliente)",
        ]
        for g in datos.get("gastos") or []
    ]
    pagos = [
        [
            _texto(p.get("fecha")),
            clp(p.get("monto")),
            _texto(p.get("medio")),
            _texto(p.get("honorario_id")),
            _texto(p.get("referencia")),
            _texto(p.get("registrado_por_nombre")),
            _texto(p.get("nota")),
        ]
        for p in datos.get("pagos") or []
    ]

    advertencias = "\n".join(
        f'<li>{html.escape(str(a))}</li>' for a in (datos.get("advertencias") or [])
    )
    saldo = int(totales.get("saldo") or 0)
    saldo_texto = clp(saldo)
    saldo_clase = "negativo" if saldo < 0 else ""
    rut_estudio = f" · RUT {_texto(estudio.get('rut'))}" if estudio.get("rut") else ""

    return f"""<!DOCTYPE html>
<html lang="es-CL">
<head>
<meta charset="utf-8">
<title>Cuenta de dividendos — {_texto(causa.get("caratula"))}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1b1f24; margin: 0; padding: 18mm 16mm; }}
  header {{ border-bottom: 2px solid #1b1f24; padding-bottom: 8px; margin-bottom: 14px; }}
  h1 {{ font-size: 17px; margin: 0 0 2px; }}
  h2 {{ font-size: 12px; margin: 16px 0 6px; border-bottom: 1px solid #c8ced6; padding-bottom: 3px; }}
  p {{ margin: 2px 0; }}
  .partes {{ display: flex; gap: 18px; margin-top: 10px; }}
  .partes > div {{ flex: 1; border: 1px solid #c8ced6; padding: 8px; }}
  .rotulo {{ font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px; color: #5b6675; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 4px; }}
  th, td {{ border: 1px solid #c8ced6; padding: 4px 6px; text-align: left; vertical-align: top; }}
  th {{ background: #eef1f5; font-size: 10px; }}
  td.vacio {{ text-align: center; color: #5b6675; }}
  tfoot td {{ font-weight: bold; background: #f7f9fb; }}
  .saldo {{ font-size: 14px; }}
  .saldo.negativo {{ color: #a3121f; }}
  .advertencias {{ margin-top: 10px; border: 1px solid #c8ced6; padding: 8px; }}
  .advertencias li {{ margin-bottom: 4px; }}
  .pie {{ margin-top: 14px; font-size: 9px; color: #5b6675; }}
  @media print {{ body {{ padding: 0; }} h2 {{ page-break-after: avoid; }} table {{ page-break-inside: auto; }} tr {{ page-break-inside: avoid; }} }}
</style>
</head>
<body>
<header>
  <h1>Cuenta de dividendos</h1>
  <p><strong>{_texto(estudio.get("nombre"))}</strong>{rut_estudio}</p>
  <p class="rotulo">Emitida el {_texto(str(datos.get("generado_en") or "")[:10])}</p>
</header>

<div class="partes">
  <div>
    <p class="rotulo">Cliente</p>
    <p><strong>{_texto(cliente.get("nombre") or "(sin cliente asignado a la causa)")}</strong></p>
    <p>RUT: {_texto(cliente.get("rut"))}</p>
    <p>Domicilio: {_texto(cliente.get("direccion"))}</p>
  </div>
  <div>
    <p class="rotulo">Causa</p>
    <p><strong>{_texto(causa.get("caratula"))}</strong></p>
    <p>Rol/RIT: {_texto(causa.get("rol_rit"))}</p>
    <p>Tribunal: {_texto(causa.get("tribunal"))}</p>
  </div>
</div>

{_tabla_html("Honorarios", ["#", "Fecha", "Modalidad", "Qué se pactó", "Pactado", "Bruto", "Retención", "Líquido", "Pagado", "Estado"], honorarios)}
{_tabla_html("Gastos de la causa", ["Fecha", "Concepto", "Comprobante", "Monto", "¿Se le cuenta al cliente?"], gastos)}
{_tabla_html("Pagos recibidos", ["Fecha", "Monto", "Medio", "Honorario", "Referencia", "Registrado por", "Nota"], pagos)}

<h2>Resumen</h2>
<table>
  <tbody>
    <tr><td>Honorarios pactados</td><td>{clp(totales.get("honorarios_pactado"))}</td></tr>
    <tr><td>Honorarios líquidos</td><td>{clp(totales.get("honorarios_liquido"))}</td></tr>
    <tr><td>Gastos de la causa (total)</td><td>{clp(totales.get("gastos"))}</td></tr>
    <tr><td>Gastos por cuenta del cliente</td><td>{clp(totales.get("gastos_por_cuenta_del_cliente"))}</td></tr>
  </tbody>
  <tfoot>
    <tr><td>Pagos recibidos</td><td>- {clp(totales.get("pagos"))}</td></tr>
    <tr><td class="saldo">Saldo {("a favor del cliente" if saldo < 0 else "por pagar")}</td><td class="saldo {saldo_clase}">{saldo_texto}</td></tr>
  </tfoot>
</table>

<div class="advertencias">
  <p class="rotulo">Advertencias</p>
  <ul>
{advertencias}
  </ul>
</div>

<p class="pie">
  Saldo = honorarios líquidos + gastos por cuenta del cliente − pagos recibidos.
  Documento generado por el CRM del estudio a partir de los registros cargados: no es un
  documento tributario ni un título ejecutivo, y no reemplaza la boleta, la factura ni la
  conciliación bancaria.
</p>
</body>
</html>
"""
