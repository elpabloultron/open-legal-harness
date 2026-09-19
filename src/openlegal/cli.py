"""CLI del Harness Legal: mismo nucleo para abogado solo y para oficina.

  openlegal init --modo solo
  openlegal estudio crear --nombre "Estudio Perez" --modo oficina
  openlegal usuario crear --email abogado@estudio.cl --nombre "Ana Perez" --rol abogado
  openlegal cliente crear --nombre "Constructora Andes SpA" --rut 76.543.210-K
  openlegal causa crear --caratula "Perez con Andes SpA" --materia laboral
  openlegal causa asignar --causa 1 --usuario 2 --rol-en-causa colaborador
  openlegal plazo crear --causa 1 --descripcion "Contestar demanda" --dias 8 --notificacion 2026-09-17
  openlegal honorario crear --causa 1 --modalidad fijo --monto 350000 --bruto 350000 --retencion 35000
  openlegal gasto crear --causa 1 --concepto "Notaría" --monto 25000 --comprobante "boleta 45"
  openlegal pago crear --causa 1 --monto 200000 --honorario 1 --referencia "transferencia 8812"
  openlegal cuenta --causa 1 --html cuenta.html       # cuenta de dividendos (A4, imprimible)
  openlegal vencimientos --dias 30
  openlegal agenda --dias 15
  openlegal panel
  openlegal auditoria
  openlegal titular exportar --rut 11.111.111-1          # acceso + portabilidad, JSON con hash
  openlegal titular anonimizar --rut 11.111.111-1 --motivo "pide supresión"   # sin --escribir, sólo informa
  openlegal ia-proxy                     # proxy local (dialectos OpenAI y Anthropic): avisa y registra cada uso de IA
  openlegal serve --host 127.0.0.1 --port 8899   # panel web (CRM en el sidebar del harness)
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import pathlib
import sys
from typing import NoReturn

from . import (
    auth,
    honorarios,
    ia,
    ia_proxy,
    notificaciones,
    plazos,
    retencion,
    seguridad,
    service,
    titulares,
)
from .db import DB, MIGRACIONES


def _ctx(args):
    db = DB(getattr(args, "db", None))
    db.migrar()
    return db


def _estudio_actual(db: DB, estudio_id: int | None) -> int:
    if estudio_id:
        return estudio_id
    filas = db.todos("SELECT id, nombre FROM estudios ORDER BY id")
    if not filas:
        salida_error("no hay estudios creados: corre `openlegal estudio crear --nombre ...`")
    if len(filas) > 1:
        salida_error(
            "hay varios estudios; indica --estudio. Disponibles: "
            + ", ".join(f"{f['id']}={f['nombre']}" for f in filas)
        )
    return filas[0]["id"]


def _usuario_actual(db: DB, estudio_id: int, email: str | None) -> dict:
    if email:
        usuario = db.uno("SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (estudio_id, email.lower()))
        if not usuario:
            salida_error(f"no existe el usuario {email} en el estudio {estudio_id}")
        usuario.pop("password_hash", None)
        return usuario
    usuario = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND rol IN ('socio','abogado') AND activo = 1 ORDER BY id",
        (estudio_id,),
    )
    if not usuario:
        # En modo solo alcanza cualquier usuario activo (incluido el unico creado).
        usuario = db.uno("SELECT * FROM usuarios WHERE estudio_id = ? AND activo = 1 ORDER BY id", (estudio_id,))
    if not usuario:
        salida_error("no hay usuarios activos; crea uno con `openlegal usuario crear`")
    usuario.pop("password_hash", None)
    return usuario


def salida_error(mensaje: str) -> NoReturn:
    print(f"error: {mensaje}", file=sys.stderr)
    raise SystemExit(1)


def _imprimir(titulo: str, filas: list[dict] | dict) -> None:
    print(f"\n{titulo}")
    print("-" * len(titulo))
    if isinstance(filas, dict):
        filas = [filas]
    if not filas:
        print("(sin registros)")
        return
    columnas = list(filas[0].keys())
    print(" | ".join(columnas))
    for fila in filas:
        print(" | ".join("" if fila[c] is None else str(fila[c]) for c in columnas))


# ------------------------------------------------------------------ comandos
def cmd_init(args) -> None:
    db = _ctx(args)
    tablas = db.migrar()
    print(f"base de datos lista en {db.url} ({db.dialecto}, {len(tablas)} objetos)")


def cmd_estudio_crear(args) -> None:
    db = _ctx(args)
    estudio_id = auth.crear_estudio(db, args.nombre, args.rut, args.modo)
    print(f"estudio {estudio_id} creado: {args.nombre} (modo {args.modo})")


def cmd_usuario_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    password = args.password
    if not password and not args.sin_password:
        password = getpass.getpass("contrasena (enter para omitir): ") or None
    actor = db.uno(
        "SELECT id FROM usuarios WHERE estudio_id = ? AND rol = 'socio' ORDER BY id", (estudio_id,)
    )
    usuario_id = auth.crear_usuario(
        db, estudio_id, args.nombre, args.email, args.rol, password,
        actor_id=actor["id"] if actor else None,
    )
    print(f"usuario {usuario_id} creado: {args.nombre} <{args.email}> rol={args.rol}")


def cmd_usuario_editar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    actor = _usuario_actual(db, estudio_id, args.actor)
    activo = None
    if args.activo is not None:
        activo = args.activo.lower() in ("si", "sí", "1", "true", "verdadero")
    fila = auth.actualizar_usuario(
        db, actor, args.email, telefono=args.telefono, activo=activo, rol=args.rol,
    )
    print(f"usuario {fila['id']} actualizado: {fila['nombre']} <{fila['email']}> "
          f"rol={fila['rol']} activo={'sí' if fila['activo'] else 'no'} "
          f"teléfono={fila['telefono'] or '(sin cargar)'}")


def cmd_usuario_listar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    _imprimir(
        "usuarios del estudio",
        db.todos(
            f"SELECT id, nombre, email, rol, {'telefono, ' if 'telefono' in db.columnas('usuarios') else ''}"
            "activo FROM usuarios WHERE estudio_id = ? ORDER BY id",
            (estudio_id,),
        ),
    )


def cmd_cliente_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    cliente_id = service.crear_cliente(
        db, usuario, args.nombre, args.rut, tipo_persona=args.tipo, email=args.email,
        telefono=args.telefono, representante_legal=args.representante,
    )
    print(f"cliente {cliente_id} creado: {args.nombre}")


def cmd_causa_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    causa_id = service.crear_causa(
        db, usuario, args.caratula, cliente_id=args.cliente, rol_rit=args.rol_rit,
        tribunal=args.tribunal, materia=args.materia, contraparte=args.contraparte,
        cuantia_clp=args.cuantia,
    )
    print(f"causa {causa_id} creada: {args.caratula}")


def cmd_causa_listar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    _imprimir(
        f"causas visibles para {usuario['nombre']} ({usuario['rol']})",
        [
            {k: c[k] for k in ("id", "rol_rit", "caratula", "tribunal", "materia", "estado_procesal")}
            for c in auth.causas_visibles(db, usuario)
        ],
    )


def cmd_causa_asignar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    service.asignar(db, usuario, args.causa, args.a_usuario, args.rol_en_causa)
    print(f"usuario {args.a_usuario} agregado a la causa {args.causa} como {args.rol_en_causa}")


def cmd_plazo_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    resultado = service.crear_plazo(
        db, usuario, args.causa, args.descripcion, dias=args.dias,
        fecha_notificacion=args.notificacion, tipo=args.tipo, es_fatal=not args.no_fatal,
    )
    print(f"plazo {resultado['id']} creado. Vence: {resultado['fecha_vencimiento']}")
    for aviso in (resultado["calculo"] or {}).get("advertencias", []):
        print(f"AVISO: {aviso}")
    if resultado["calculo"] and args.ver_detalle:
        _imprimir("computo de dias habiles (Art. 66 CPC)", resultado["calculo"]["detalle"])


def cmd_vencimientos(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    filas = service.vencimientos(db, usuario, args.desde, args.dias)
    _imprimir(
        f"vencimientos desde {args.desde or dt.date.today().isoformat()} ({args.dias} dias)",
        [
            {
                "plazo": f["id"],
                "vence": f["fecha_vencimiento"],
                "fatal": "si" if f["es_fatal"] else "no",
                "causa": f["caratula"],
                "descripcion": f["descripcion"],
            }
            for f in filas
        ],
    )


def cmd_agenda(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    _imprimir(f"agenda ({args.dias} dias)", service.agenda(db, usuario, args.desde, args.dias))


def cmd_panel(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    datos = service.panel(db, usuario)
    _imprimir("causas por estado", datos["causas_por_estado"])
    _imprimir("carga por abogado", datos["carga_por_abogado"])
    print(f"\nplazos pendientes: {datos['plazos_pendientes']}")


def cmd_plazo_simular(args) -> None:
    notificacion = dt.date.fromisoformat(args.notificacion)
    resultado = plazos.vencimiento(notificacion, args.dias)
    print(f"notificacion: {notificacion.isoformat()} | {args.dias} dias habiles")
    for aviso in resultado["advertencias"]:
        print(f"AVISO: {aviso}")
    _imprimir("detalle", resultado["detalle"])
    print(f"\nVENCE: {resultado['fecha_vencimiento']}")


def cmd_auditoria(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    _imprimir(
        "bitacora",
        db.todos(
            "SELECT a.id, a.creado_en, u.nombre AS usuario, a.accion, a.entidad, a.entidad_id, a.detalle "
            "FROM auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id "
            "WHERE a.estudio_id = ? ORDER BY a.id DESC LIMIT ?",
            (estudio_id, args.limite),
        ),
    )


def cmd_ia_autorizar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    autorizacion = service.autorizar_ia(
        db, usuario, args.causa, alcance=args.alcance, titular=args.titular, base_licitud=args.base
    )
    print(f"autorización {autorizacion} registrada para la causa {args.causa} (alcance {args.alcance})")


def cmd_ia_revocar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    cuantas = service.revocar_ia(db, usuario, args.causa)
    print(f"{cuantas} autorización(es) revocada(s) en la causa {args.causa}")


def cmd_ia_estado(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    estado = service.estado_ia(db, usuario, args.causa)
    print(f"causa {args.causa}: {'AUTORIZADA' if estado['autorizado'] else 'sin autorización'}")
    if estado["autorizacion"]:
        autorizacion = estado["autorizacion"]
        print(f"  alcance: {autorizacion['alcance']} · titular: {autorizacion['titular'] or 's/informar'}")
        print(f"  base: {autorizacion['base_licitud']}")
    print(f"  comunicaciones registradas: {estado['transferencias']}")
    print("  términos a minimizar antes de enviar:")
    for termino in estado["terminos_a_minimizar"]:
        print(f"    - {termino}")


def _texto_de(args) -> str:
    if args.archivo:
        return pathlib.Path(args.archivo).read_text(encoding="utf-8")
    if args.texto:
        return args.texto
    return sys.stdin.read()


def cmd_ia_redactar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    if args.causa:
        # Los términos de la causa son nombres y RUT de las personas del expediente:
        # para verlos hay que tener acceso a ESA causa, no sólo al estudio.
        auth.exigir(db, usuario, "causa.leer", args.causa)
    terminos = ia.terminos_de_causa(db, args.causa) if args.causa else args.termino
    limpio, mapa = ia.redactar(_texto_de(args), terminos)
    print("--- texto minimizado (esto es lo que se manda al modelo) ---")
    print(limpio)
    print("--- mapa de reidentificación (SE QUEDA EN EL ESTUDIO) ---")
    for marcador_, valor in mapa.items():
        print(f"  {marcador_} = {valor}")


def cmd_ia_registrar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    texto = _texto_de(args)
    if not args.redactado:
        print("AVISO: registras el envío SIN redactar. Si el texto lleva RUT, correos o nombres,")
        print("       minimiza primero con `openlegal ia redactar` (el registro queda igual).")
    registro = service.registrar_transferencia(
        db, usuario, args.causa, args.proveedor, texto,
        modelo=args.modelo, documentos=args.documentos, redactado=args.redactado,
    )
    print(f"comunicación registrada #{registro['id']}")
    print(f"  proveedor: {registro['proveedor']} · destino: {registro['destino_pais']}")
    print(f"  caracteres: {registro['caracteres']} · redactado: {'sí' if registro['redactado'] else 'no'}")
    print(f"  hash: {registro['hash_payload'][:16]}…")


def cmd_ia_transferencias(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    filas = service.transferencias_ia(db, usuario, args.causa)
    _imprimir(
        "comunicaciones a proveedores de IA",
        [
            {
                "id": f["id"],
                "fecha": f["creado_en"],
                "causa": f.get("caratula") or f["causa_id"],
                "proveedor": f["proveedor"],
                "modelo": f["modelo"],
                "destino": f["destino_pais"],
                "caracteres": f["caracteres"],
                "redactado": "sí" if f["redactado"] else "no",
                "hash": (f["hash_payload"] or "")[:12] + "…",
            }
            for f in filas
        ],
    )


def cmd_ia_proveedores(args) -> None:
    tabla = [
        {
            "proveedor": nombre,
            "pais": datos["pais"],
            "entrena_con_datos_de_api": (
                "no" if datos["entrena_con_api"] is False else
                ("SIN VERIFICAR" if datos["entrena_con_api"] is None else "sí")
            ),
            "retencion": datos["retencion"],
            "zdr": datos["zdr"],
        }
        for nombre, datos in ia.PROVEEDORES.items()
    ]
    _imprimir("proveedores de modelos", tabla)
    print("\nnotas:")
    for nombre, datos in ia.PROVEEDORES.items():
        print(f"  {nombre}: {datos['nota']}")


def cmd_ia_proxy(args) -> None:
    """Arranca el proxy de IA, o muestra su estado y los últimos envíos (nunca la clave).

    El estado es para responder la pregunta que importa antes de apuntarle el harness: ¿dónde
    escucha, a qué proveedor le manda, minimiza o no, y qué salió últimamente? La api_key se
    informa como «configurada» o «falta»: su valor no se imprime nunca, ni acá ni en el panel.
    """
    if not args.estado:
        # El proxy no elige usuario: corre en la máquina del estudio y escribe en la base del
        # CRM sin sesión (queda con origen 'proxy' y sin usuario). Lo que sí exige permisos es
        # leer su registro, que es lo que hacen `--estado` y el panel.
        ia_proxy.servir(base_de_datos=getattr(args, "db", None))
        return

    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    auth.exigir(db, usuario, "ia.leer")
    resumen = ia_proxy.resumen()
    print(f"proxy de IA: {resumen['url']} (dialecto OpenAI) · {resumen['url_anthropic']} (dialecto Anthropic)")
    print(f"  {'escuchando' if resumen['activo'] else 'NO está corriendo (arráncalo con `openlegal ia-proxy`)'}")
    print(f"  proveedor: {resumen['proveedor']} · aguas arriba: {resumen['base_url']}")
    print(f"  aguas arriba (Anthropic, /v1/messages): {resumen['base_url_anthropic']}")
    print(f"  país de destino: {resumen['destino_pais']} · modelo por defecto: {resumen['modelo_por_defecto'] or 'el del pedido'}")
    print(
        f"  minimizar: {'sí' if resumen['minimizar'] else 'NO'} · "
        f"permitir sin autorización: {'sí' if resumen['permitir_sin_autorizacion'] else 'no'} · "
        f"avisos de escritorio: {'sí' if resumen['avisar_escritorio'] else 'no'}"
    )
    print(f"  causa por defecto: {resumen['causa_por_defecto'] or 'ninguna'} · api_key: {resumen['api_key']}")
    print(f"  configuración: {resumen['archivo']} (permisos {resumen['permisos']})")
    print(f"  registro: {ia_proxy.AVISO_SIN_CONTENIDO}")
    print(
        f"  apunta el harness acá: baseURL={resumen['url_anthropic']} si habla el protocolo "
        f"`messages` (sin /v1 ni /anthropic: el adaptador agrega /v1/messages) · "
        f"base_url={resumen['url']} si habla el de OpenAI"
    )
    for aviso in resumen["avisos"]:
        print(f"  AVISO: {aviso}")

    filas = ia_proxy.envios_recientes(
        db,
        limite=20,
        visibles=[c["id"] for c in auth.causas_visibles(db, usuario)],
        estudio_id=usuario["estudio_id"],
    )
    _imprimir(
        "últimos envíos a proveedores de IA",
        [
            {
                "id": f["id"],
                "fecha": f["creado_en"],
                "causa": f.get("caratula") or (f"causa {f['causa_id']}" if f["causa_id"] else "sin causa"),
                "origen": f["origen"],
                "proveedor": f["proveedor"],
                "modelo": f["modelo"],
                "destino": f["destino_pais"],
                "caracteres": f["caracteres"],
                "minimizado": "sí" if f["redactado"] else "no",
                "bloqueado": f["motivo_bloqueo"] or ("sí" if f["bloqueado"] else "no"),
                "hash": (f["hash_payload"] or "")[:12] + "…",
            }
            for f in filas
        ],
    )
    db.cerrar()


def cmd_usuario_clave(args) -> None:
    """Fija la contraseña de un usuario. Se pide dos veces y nunca se escribe en la línea de comandos."""
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    actor = _usuario_actual(db, estudio_id, getattr(args, "actor", None))
    usuario = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (estudio_id, args.email.lower())
    )
    if not usuario:
        salida_error(f"no existe el usuario {args.email} en el estudio {estudio_id}")
    primera = getpass.getpass("nueva contrasena: ")
    segunda = getpass.getpass("repetir contrasena: ")
    if primera != segunda:
        salida_error("las contrasenas no coinciden")
    if len(primera) < 10:
        salida_error("usa al menos 10 caracteres (los datos son de clientes)")
    fila = auth.actualizar_usuario(db, actor, args.email, password=primera)
    _cerrar_sesiones(db, fila["id"])
    print(f"contrasena actualizada para {fila['email']} ({fila['rol']})")


def cmd_usuario_desactivar(args) -> None:
    """Corta el acceso sin borrar el historial: la causa sigue mostrando quién la llevaba.

    Pasa por `auth.actualizar_usuario`, que exige el permiso y no deja al estudio sin su
    último socio activo — y anota en la bitácora **quién** revocó el acceso, no el revocado.
    """
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    actor = _usuario_actual(db, estudio_id, getattr(args, "actor", None))
    fila = auth.actualizar_usuario(db, actor, args.email, activo=False)
    _cerrar_sesiones(db, fila["id"])
    print(f"acceso revocado a {fila['email']} (el historial queda intacto)")


def _cerrar_sesiones(db: DB, usuario_id: int) -> None:
    """Cierra las sesiones abiertas de un usuario: al revocar o cambiar la clave, ya no sirven."""
    db.ejecutar("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))


def cmd_audiencia_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    audiencia_id = service.crear_audiencia(
        db, usuario, args.causa, args.tipo, args.fecha, args.hora,
        modalidad=args.modalidad, lugar_o_url=args.lugar,
    )
    print(f"audiencia {audiencia_id} creada: {args.tipo} {args.fecha} {args.hora or ''}".strip())


# ------------------------------------------------ honorarios, gastos y pagos
# El circuito de la plata del estudio: lo que se pacta, lo que se gasta y lo que el
# cliente abona. Los montos son CLP enteros y las tasas las declara el estudio: acá no
# se calcula ninguna retención (ver `openlegal/honorarios.py`).
def cmd_honorario_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    if args.monto is None and args.bruto is None:
        salida_error(
            "indica --monto (lo pactado con el cliente) o --bruto (lo que sale en tu boleta): "
            "sin uno de los dos no hay nada que cobrar. Son CLP enteros: 350000, no 350.000,00"
        )
    honorario_id = honorarios.registrar_honorario(
        db, usuario, args.causa, args.modalidad, monto_pactado=args.monto,
        descripcion=args.descripcion, fecha=args.fecha, monto_bruto=args.bruto,
        retencion_sii=args.retencion,
    )
    fila = db.uno("SELECT * FROM honorarios WHERE id = ?", (honorario_id,))
    if not fila:
        salida_error(f"no pude leer el honorario {honorario_id} que se acaba de registrar")
    print(f"honorario {honorario_id} registrado en la causa {args.causa} ({args.modalidad})")
    print(
        f"  fecha: {fila['fecha']} · pactado: {honorarios.clp(fila['monto_pactado'])} · "
        f"líquido: {honorarios.clp(fila['monto_liquido'])} · estado: {fila['estado_pago']}"
    )
    if fila["descripcion"]:
        print(f"  qué se pactó: {fila['descripcion']}")
    if fila["monto_bruto"] is not None and fila["retencion_sii"] is None:
        print("AVISO: no declaraste retención, así que el líquido quedó igual al bruto.")
        print("       La tasa la copia el estudio de su boleta y se pasa con --retencion:")
        print("       el CRM no la calcula ni supone (y no hay integración con el SII).")


def cmd_honorario_listar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    filas = honorarios.listar_honorarios(db, usuario, args.causa)
    _imprimir(
        "honorarios",
        [
            {
                "id": f["id"],
                "causa": f["caratula"],
                "fecha": f["fecha"],
                "modalidad": f["modalidad"],
                "pactado": honorarios.clp(f["monto_pactado"]),
                "liquido": honorarios.clp(f["monto_liquido"]),
                "pagado": honorarios.clp(f["monto_pagado"]),
                "estado": f["estado_pago"],
                "descripcion": f["descripcion"],
            }
            for f in filas
        ],
    )


def cmd_gasto_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    gasto_id = honorarios.registrar_gasto(
        db, usuario, args.causa, args.concepto, args.monto, fecha=args.fecha,
        comprobante=args.comprobante, pagado_por_estudio=not args.lo_pago_el_cliente,
    )
    print(f"gasto {gasto_id} registrado en la causa {args.causa}: {args.concepto} {honorarios.clp(args.monto)}")
    if args.lo_pago_el_cliente:
        print("  (lo pagó el cliente: queda de constancia, no se le cuenta en la cuenta de dividendos)")
    elif not args.comprobante:
        print("  ojo: quedó sin comprobante. Anota el respaldo con --comprobante cuando lo tengas a mano.")
        print("       Un gasto sin respaldo en la cuenta del cliente es difícil de sostener.")


def cmd_pago_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    pago_id = honorarios.registrar_pago(
        db, usuario, args.causa, args.monto, fecha=args.fecha, medio=args.medio,
        referencia=args.referencia, nota=args.nota, honorario_id=args.honorario,
    )
    print(f"pago {pago_id} registrado: {honorarios.clp(args.monto)} a la causa {args.causa} ({args.medio})")
    if args.honorario:
        fila = db.uno("SELECT * FROM honorarios WHERE id = ?", (args.honorario,))
        if not fila:
            salida_error(f"no existe el honorario {args.honorario} en este estudio")
        print(
            f"  honorario {args.honorario}: pagado {honorarios.clp(fila['monto_pagado'])} de "
            f"{honorarios.clp(fila['monto_liquido'])} · estado {fila['estado_pago']}"
        )
    else:
        print("  el pago quedó sin imputar a un honorario: se descuenta del saldo de la causa igual,")
        print("  pero el CRM no puede decir cuál honorario quedó pagado. Con --honorario N lo imputa.")


def cmd_cuenta(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    datos = honorarios.cuenta(db, usuario, args.causa)
    estudio = datos["estudio"]
    cliente = datos["cliente"] or {}
    causa = datos["causa"]

    print(f"\ncuenta de dividendos · causa {causa['id']}")
    print("=" * 62)
    print(f"estudio:  {estudio.get('nombre')}" + (f" · RUT {estudio.get('rut')}" if estudio.get("rut") else ""))
    print(f"cliente:  {cliente.get('nombre') or '(sin cliente asignado a la causa)'}")
    print(f"          RUT {cliente.get('rut') or 's/informar'} · {cliente.get('direccion') or 'domicilio s/informar'}")
    print(f"causa:    {causa.get('caratula')}")
    print(f"          ROL/RIT {causa.get('rol_rit') or 's/informar'} · {causa.get('tribunal') or 'tribunal s/informar'}")

    _imprimir(
        "honorarios",
        [
            {
                "id": h["id"],
                "fecha": h["fecha"],
                "modalidad": h["modalidad"],
                "que se pacto": h["descripcion"],
                "pactado": honorarios.clp(h["monto_pactado"]),
                "liquido": honorarios.clp(h["monto_liquido"]),
                "pagado": honorarios.clp(h["monto_pagado"]),
                "estado": h["estado_pago"],
            }
            for h in datos["honorarios"]
        ],
    )
    _imprimir(
        "gastos de la causa",
        [
            {
                "fecha": g["fecha"],
                "concepto": g["concepto"],
                "comprobante": g["comprobante"],
                "monto": honorarios.clp(g["monto"]),
                "se le cuenta al cliente": "si" if honorarios._por_cuenta_del_cliente(g) else "no",
            }
            for g in datos["gastos"]
        ],
    )
    _imprimir(
        "pagos recibidos",
        [
            {
                "fecha": p["fecha"],
                "monto": honorarios.clp(p["monto"]),
                "medio": p["medio"],
                "honorario": p["honorario_id"],
                "referencia": p["referencia"],
                "registro": p["registrado_por_nombre"],
            }
            for p in datos["pagos"]
        ],
    )

    totales = datos["totales"]
    print("\ntotales")
    print("-" * 7)
    print(f"  honorarios pactados:            {honorarios.clp(totales['honorarios_pactado'])}")
    print(f"  honorarios líquidos:            {honorarios.clp(totales['honorarios_liquido'])}")
    print(f"  gastos de la causa:             {honorarios.clp(totales['gastos'])}")
    print(f"  gastos por cuenta del cliente:  {honorarios.clp(totales['gastos_por_cuenta_del_cliente'])}")
    print(f"  pagos recibidos:              - {honorarios.clp(totales['pagos'])}")
    etiqueta = "a favor del cliente" if totales["saldo"] < 0 else "por pagar"
    print(f"  SALDO {etiqueta}:{' ' * max(1, 20 - len(etiqueta))}{honorarios.clp(totales['saldo'])}")
    print("  (saldo = honorarios líquidos + gastos por cuenta del cliente - pagos)")
    for aviso in datos["advertencias"]:
        print(f"AVISO: {aviso}")

    if args.html:
        ruta = pathlib.Path(args.html)
        ruta.write_text(honorarios.html_cuenta(datos), encoding="utf-8")
        print(f"\ncuenta imprimible escrita en {ruta}")
        print("  (A4, todo embebido: se abre e imprime sin conexión; no es un documento tributario)")


def cmd_mcp(args) -> None:
    """Servidor MCP por stdio: es el puente para que el agente escriba en el CRM."""
    from .mcp import main as mcp_main

    # El protocolo MCP va en UTF-8 por definición, y en Windows la salida por defecto de
    # un proceso es cp1252: sin esto, la primera tilde en una respuesta rompe el flujo
    # JSON-RPC (o lo corrompe en silencio). Se declara acá, en el punto de entrada, para
    # que valga en cualquier plataforma y no dependa del entorno que herede.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - flujos ya envueltos
            pass

    db = getattr(args, "db", None)
    argv = []
    if db:
        argv += ["--db", db]
    if args.usuario:
        argv += ["--usuario", args.usuario]
    mcp_main(argv)


def _ruta_token() -> pathlib.Path:
    """Dónde queda anotado el token del panel (permiso 600, junto al resto de la config)."""
    return pathlib.Path(
        os.environ.get("OPENLEGAL_TOKEN_FILE") or "~/.openlegal/token_panel.txt"
    ).expanduser()


def _guardar_token(token: str) -> pathlib.Path | None:
    """Escribe el token del panel para poder leerlo sin buscarlo.

    El token es la llave del panel —con él se ve el estudio entero—, así que va a un archivo
    con permiso 600 y nunca a un lugar público. Se escribe sólo cuando lo generó el servidor:
    si lo eligió quien lo arrancó, ya sabe cuál es. Si no se puede escribir, no pasa nada: el
    servidor igual lo imprime y el panel sigue funcionando.
    """
    ruta = _ruta_token()
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(token, encoding="utf-8")
        ruta.chmod(0o600)
    except OSError:
        return None
    return ruta


def cmd_serve(args) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError:
        salida_error("falta el servidor web: instala con `pip install 'open-legal-harness[ui]'`")
    from .web import crear_app

    app = crear_app(getattr(args, "db", None), args.token)
    certificado = getattr(args, "cert", None)
    clave_cert = getattr(args, "key", None) or certificado
    esquema = "https" if certificado else "http"
    # El token se imprime sólo cuando lo generó el servidor: si lo pasó quien lo arrancó, ya
    # lo sabe, y repetirlo por la salida estándar es la forma más fácil de que termine escrito
    # en un registro, en un archivo de servicio o en el chat de un asistente. Un token que
    # aparece donde no debe se rota; no imprimirlo evita el problema en vez de administrarlo.
    lo_generamos = not args.token
    url = f"{esquema}://{args.host}:{args.port}/?token={app.state.token}"
    if not lo_generamos:
        url = f"{esquema}://{args.host}:{args.port}/"

    # flush=True a propósito: cuando la salida va a un archivo en vez de a una terminal,
    # Python la guarda en un búfer y el mensaje no aparece hasta que el proceso termina —
    # o sea, nunca, porque es un servidor. Sin esto, arrancar el panel como servicio deja un
    # archivo vacío y nadie se entera de cuál era la dirección.
    print("\nOpen Legal Harness — CRM Jurídico en marcha", flush=True)
    print(f"  panel:  {url}", flush=True)
    if not args.token:
        archivo = _guardar_token(app.state.token)
        if archivo:
            print(f"  token:  también queda en {archivo} (permiso 600)", flush=True)
    print(f"  base:   {app.state.db_url or '(por defecto)'}", flush=True)
    if certificado:
        # En la oficina el panel queda en la red local: sin certificado, las contraseñas
        # viajan en claro entre los equipos. El certificado no hace falta que sea de una
        # autoridad: alcanza con que sea el mismo en todos los equipos y que el navegador
        # lo acepte una vez (scripts/certificado_local.sh lo genera).
        print(f"  TLS:    {certificado}", flush=True)
        print("  (si el navegador avisa que el certificado no es de confianza: es el esperado")
        print("   en un certificado propio; se acepta una vez por equipo)")
    else:
        print("  aviso: sin certificado el panel va en HTTP. Si lo van a usar desde otros", flush=True)
        print("         equipos, generá uno con scripts/certificado_local.sh y pasalo con --cert")
    print("  el token es local; el servicio solo escucha en el host indicado\n")

    # `uvicorn.run` tiene una firma enorme y acá sólo se usan cuatro cosas: el diccionario
    # se arma según haya certificado o no, y se le pasa sin más.
    opciones: dict[str, object] = {"host": args.host, "port": args.port, "log_level": "warning"}
    if certificado:
        for etiqueta, ruta in (("certificado", certificado), ("clave del certificado", clave_cert)):
            if not pathlib.Path(str(ruta)).is_file():
                salida_error(f"no encuentro el {etiqueta} {ruta}")
        opciones["ssl_certfile"] = str(certificado)
        opciones["ssl_keyfile"] = str(clave_cert)
    uvicorn.run(app, **opciones)


def cmd_titular_exportar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    informe = titulares.exportar(
        db, usuario, rut=args.rut, nombre=args.nombre, email=args.email, destino=args.destino
    )
    print("exportación lista: sirve para el derecho de acceso y para el de portabilidad")
    print(f"  archivo:   {informe['archivo']}")
    print(f"  sha256:    {informe['sha256']}")
    print(
        f"  contenido: {informe['titulares']} titular(es) · {informe['causas']} causa(s) · "
        f"{informe['respaldos']} respaldo(s) · {informe['entradas_auditoria']} entrada(s) de bitácora"
    )
    if informe["respaldos_ausentes"]:
        print(f"  ojo: {informe['respaldos_ausentes']} documento(s) del registro no están en disco")
    print("  aviso: los documentos escaneados no van dentro del archivo; hay que adjuntarlos aparte")


def cmd_titular_anonimizar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    informe = titulares.anonimizar(
        db, usuario, rut=args.rut, nombre=args.nombre, email=args.email, motivo=args.motivo,
        simular=not args.escribir, redactar_textos=not args.sin_redactar_textos,
    )
    if informe["simulado"]:
        print("SIMULACIÓN: no se escribió nada. Para ejecutarla de verdad, agrega --escribir")
    print(f"titular(es): {informe['titulares']} · causa(s) alcanzada(s): {len(informe['causas'])}")
    borrados = sum(len(f["cambios"]) for f in informe["identificadores_borrados"])
    apariciones = sum(t["reemplazos"] for t in informe["textos_redactados"])
    print(f"  identificadores directos que se borran: {borrados}")
    print(f"  textos que se redactan: {len(informe['textos_redactados'])} ({apariciones} apariciones del nombre)")
    for cambio in informe["conservado"]:
        print(f"  se conserva · {cambio['tabla']}: {cambio['filas']} fila(s) — {cambio['motivo']}")
    print(f"  aviso: {informe['aviso']}")
    if not informe["simulado"]:
        print("  la operación quedó en la bitácora como titular.anonimizar, con su motivo")


def cmd_migraciones(args) -> None:
    # Este comando NO migra, a propósito: si migrara antes de mirar, siempre diría que
    # está todo al día y no serviría para diagnosticar. El resto de los comandos sí
    # aplican lo pendiente al abrir la base.
    db = DB(getattr(args, "db", None))
    print(f"base: {db.url.split('@')[-1] if '@' in db.url else db.url}")
    print(f"motor: {db.dialecto}")
    if "migraciones" not in db.tablas():
        print("registro: todavía no existe (esta base es anterior a él)")
        print("se marcará en la 1 y se aplicará lo que falte la primera vez que corras")
        print("cualquier comando (init, panel, mcp…). Migraciones conocidas:")
        for version, nombre, _ in MIGRACIONES:
            print(f"  {version:>3}  {nombre}")
        db.cerrar()
        return
    aplicadas = db.migraciones_aplicadas()
    pendientes = db.migraciones_pendientes()
    if aplicadas:
        print("aplicadas:")
        for version, fila in sorted(aplicadas.items()):
            print(f"  {version:>3}  {fila['nombre']}  ({fila['aplicada_en']})")
    else:
        print("aplicadas: ninguna")
    if pendientes:
        print("pendientes:")
        for version, nombre in pendientes:
            print(f"  {version:>3}  {nombre}")
    else:
        print("pendientes: ninguna, la base está al día")
    db.cerrar()


def cmd_usuario_2fa(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    actor = _usuario_actual(db, estudio_id, args.actor)
    if args.estado:
        print("segundo factor por usuario:")
        for fila in auth.estado_segundo_factor(db, actor):
            marca = "activo" if fila["totp_activo"] else ("FALTA" if fila["esperado"] else "—")
            print(f"  {fila['email']:32s} {fila['rol']:15s} {marca}")
        return
    if args.desactivar:
        auth.desactivar_segundo_factor(db, actor, args.email, args.motivo or "")
        print(f"segundo factor apagado para {args.email.lower()}")
        return
    informe = auth.activar_segundo_factor(db, actor, args.email)
    print(f"segundo factor activado para {informe['email']}")
    print(f"  secreto: {informe['secreto']}")
    print(f"  uri:     {informe['uri']}")
    print("  escanéala con la app de autenticación (Google Authenticator, Aegis, 1Password…)")
    print(f"  son {informe['digitos']} dígitos que cambian cada {informe['periodo_segundos']} segundos")
    print("  el secreto no se vuelve a mostrar: si se pierde el teléfono, se enrola de nuevo")


def cmd_seguridad(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)

    if getattr(args, "desbloquear", None):
        estado = auth.desbloquear(db, usuario, args.desbloquear)
        if estado["bloqueada"]:
            print(f"{args.desbloquear}: sigue bloqueada, faltan {estado['faltan_minutos']} minuto(s)")
        else:
            print(f"{args.desbloquear}: cuenta destrabada (queda anotado quién la destrabó)")
        db.cerrar()
        return

    informe = seguridad.intentos_fallidos(db, minutos=args.minutos, estudio_id=estudio_id)
    print(f"intentos fallidos en los últimos {informe['ventana_minutos']} minutos: {informe['total']}")
    if informe["por_cuenta"]:
        print("por cuenta:")
        for cuenta, veces in sorted(informe["por_cuenta"].items(), key=lambda x: -x[1]):
            print(f"  {veces:>4}  {cuenta}")
    if informe["por_motivo"]:
        print("por motivo:")
        for motivo, veces in sorted(informe["por_motivo"].items(), key=lambda x: -x[1]):
            print(f"  {veces:>4}  {motivo}")
    if informe["total"] == 0:
        print("nadie ha fallado: o no hubo intentos, o todos entraron")
    elif informe["sospechoso"]:
        print(f"AVISO: pasó el umbral de {informe['umbral']} intentos. Revisa quién y desde dónde.")
    # El bloqueo no se deduce de la alerta: la alerta es del estudio, el bloqueo es de la
    # cuenta. Se dicen aparte para que no se confundan.
    bloqueadas = [c for c in informe["por_cuenta"] if seguridad.bloqueo(db, c)["bloqueada"]]
    if bloqueadas:
        print(f"bloqueadas ahora mismo: {', '.join(bloqueadas)}")
        print("  (se destraban solas; 'openlegal seguridad --desbloquear correo' las destraba ya)")
    # El estado del segundo factor completa el cuadro: un estudio con socios sin 2FA es
    # una puerta abierta, y se dice acá en vez de esperar a que alguien se acuerde.
    faltantes = [f for f in auth.estado_segundo_factor(db, usuario) if f["esperado"] and not f["totp_activo"]]
    if faltantes:
        print(f"segundo factor pendiente en {len(faltantes)} cuenta(s) de socio o administrador:")
        for fila in faltantes:
            print(f"  {fila['email']} ({fila['rol']})")


def cmd_retencion(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)

    if args.definir:
        if not args.meses:
            salida_error("falta --meses: el plazo que declaras para ese tipo de dato")
        retencion.definir(db, usuario, args.definir, args.meses, args.motivo or "")
        print(f"plazo declarado · {args.definir}: {args.meses} meses")
        return

    if args.aplicar:
        if not args.motivo:
            salida_error("falta --motivo: una anonimización por retención tiene que ser explicable")
        informe_ = retencion.aplicar(db, usuario, args.motivo, simular=not args.escribir)
        if informe_["simulado"]:
            print("SIMULACIÓN: no se escribió nada. Para ejecutarla, agrega --escribir")
        for fila in informe_["anonimizados"]:
            estado = "anonimizado" if fila["hecho"] else "se anonimizaría"
            print(f"  {estado}: {fila['nombre']} (cliente {fila['cliente_id']})")
        if not informe_["anonimizados"]:
            print("  no hay ninguna persona cuyo plazo esté cumplido")
        for fila in informe_["conservado"]:
            print(f"  se conserva · {fila['tabla']}: {fila['filas']} fila(s) — {fila['motivo']}")
        if not informe_["simulado"] and informe_["anonimizados"]:
            print("  la operación quedó en la bitácora como retencion.aplicar")
        db.cerrar()
        return

    datos = retencion.informe(db)
    print(f"política de retención · corte de {datos['corte_meses']} meses por último movimiento")
    for tipo, regla in sorted(datos["politica"].items()):
        print(f"  {tipo}: {regla['meses']} meses ({regla['origen']})")
        print(f"     {regla['motivo']}")
    print(f"\ncumplidos: {datos['total']}")
    for fila in datos["cumplidos"]:
        print(f"  {fila['nombre']} (cliente {fila['cliente_id']}) — sin movimiento desde {fila['ultimo_movimiento']} ({fila['dias_sin_movimiento']} días)")
    for fila in datos["conservado"]:
        print(f"  se conserva · {fila['tabla']}: {fila['filas']} fila(s) — {fila['motivo']}")
    print(f"  aviso: {datos['aviso']}")
    db.cerrar()


def cmd_notificar(args) -> None:
    db = _ctx(args)

    if args.config:
        existe = notificaciones.ruta_config().is_file()
        print(f"archivo de configuración: {notificaciones.ruta_config()} {'(existe)' if existe else '(no existe todavía)'}")
        print("(las claves nunca se muestran)")
        for fila in notificaciones.resumen_config():
            marca = "listo " if fila["listo"] else "FALTA "
            print(f"  {fila['canal']:20s} {marca} {fila['detalle']}")
        if not existe:
            print("\npara configurarlo: crea ese archivo con permisos 600 — ver docs/notificaciones.md")
        db.cerrar()
        return

    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)

    if args.generar or not (args.enviar or args.estado):
        informe = notificaciones.generar_recordatorios(db, usuario, dias=args.dias)
        print(
            f"revisé {informe['plazos_revisados']} plazo(s) y {informe['audiencias_revisadas']} audiencia(s) "
            f"con {informe['dias_de_aviso']} día(s) de anticipación"
        )
        print(f"  encolados: {informe['encoladas']} · ya estaban encolados: {informe['repetidas']} · sin destino: {informe['sin_destino']}")
        if informe["sin_destino"]:
            print("  (sin destino = la persona no tiene correo/teléfono cargado, o el canal no está configurado)")

    if args.enviar:
        resultado = notificaciones.enviar_pendientes(db, usuario, limite=args.limite)
        print(f"\ncola: {resultado['revisadas']} revisado(s) · enviadas: {resultado['enviadas']} · fallidas: {resultado['fallidas']}")
        for error in resultado["errores"]:
            print(f"  falló el aviso {error['id']} por {error['canal']}: {error['error']}")

    if args.estado:
        datos = notificaciones.estado(db)
        print(f"\ncola de avisos: {datos['conteos'] or '(vacía)'}")
        for falla in datos["ultimas_fallas"]:
            print(f"  falló {falla['canal']} a {falla['destino']}: {falla['ultimo_error']}")

    db.cerrar()


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openlegal", description="Harness legal chileno con CRM")
    parser.add_argument("--db", help="URL de la base: sqlite:///ruta.db o postgresql://...")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("init", help="crea el esquema en la base")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("migraciones", help="en qué versión está la base y qué falta aplicarle")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_migraciones)

    p = sub.add_parser("estudio", help="estudios")
    sub_e = p.add_subparsers(dest="accion", required=True)
    pe = sub_e.add_parser("crear")
    pe.add_argument("--nombre", required=True)
    pe.add_argument("--rut")
    pe.add_argument("--modo", choices=["solo", "oficina"], default="solo")
    pe.set_defaults(func=cmd_estudio_crear)

    p = sub.add_parser("usuario", help="usuarios y roles")
    sub_u = p.add_subparsers(dest="accion", required=True)
    pu = sub_u.add_parser("crear")
    pu.add_argument("--nombre", required=True)
    pu.add_argument("--email", required=True)
    pu.add_argument("--rol", required=True, choices=list(auth.ROLES))
    pu.add_argument("--password")
    pu.add_argument("--sin-password", action="store_true")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_crear)
    pu = sub_u.add_parser("editar", help="teléfono, rol, contraseña o dejar a alguien inactivo")
    pu.add_argument("--email", required=True, help="a quién se le cambian los datos")
    pu.add_argument("--telefono", help="teléfono para los avisos por SMS")
    pu.add_argument("--rol", choices=list(auth.ROLES))
    pu.add_argument("--activo", help="si o no: si el usuario puede entrar al CRM")
    pu.add_argument("--actor", help="email de quien hace el cambio (por defecto, el socio)")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_editar)
    pu = sub_u.add_parser("listar")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_listar)
    pu = sub_u.add_parser("clave", help="fija la contrasena de un usuario (se pide por teclado)")
    pu.add_argument("--email", required=True)
    pu.add_argument("--actor", help="email de quien hace el cambio (por defecto, el socio)")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_clave)
    pu = sub_u.add_parser("desactivar", help="revoca el acceso de un usuario")
    pu.add_argument("--email", required=True)
    pu.add_argument("--actor", help="email de quien revoca el acceso (por defecto, el socio)")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_desactivar)
    pu = sub_u.add_parser("2fa", help="segundo factor (TOTP) de un usuario: enrolar, apagar o ver estado")
    pu.add_argument("--email", help="usuario a enrolar (no hace falta con --estado)")
    pu.add_argument("--estado", action="store_true", help="muestra quién tiene segundo factor en el estudio")
    pu.add_argument("--desactivar", action="store_true", help="lo apaga (exige --motivo)")
    pu.add_argument("--motivo", help="por qué se apaga; queda en la bitácora")
    pu.add_argument("--actor", help="email de quien ejecuta (debe poder gestionar usuarios)")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_2fa)

    p = sub.add_parser("seguridad", help="intentos fallidos, bloqueos y estado del segundo factor")
    p.add_argument("--minutos", type=int, default=15)
    p.add_argument("--desbloquear", help="correo de la cuenta a destrabar ahora mismo")
    p.add_argument("--usuario", help="email del usuario que ejecuta")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_seguridad)

    p = sub.add_parser("notificar", help="avisos por correo y SMS: encola recordatorios y despacha la cola")
    p.add_argument("--generar", action="store_true", help="encola recordatorios de plazos y audiencias (es lo que hace por defecto)")
    p.add_argument("--enviar", action="store_true", help="manda lo que está en la cola")
    p.add_argument("--estado", action="store_true", help="cuántos avisos hay en cada estado y las últimas fallas")
    p.add_argument("--config", action="store_true", help="qué está configurado y qué falta (sin mostrar claves)")
    p.add_argument("--dias", type=int, help="con cuántos días de anticipación avisar (por defecto 3)")
    p.add_argument("--limite", type=int, default=50, help="cuántos avisos despachar en esta corrida")
    p.add_argument("--usuario", help="email del usuario que ejecuta")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_notificar)

    p = sub.add_parser("retencion", help="cuánto se conserva cada dato y qué plazo está cumplido")
    p.add_argument("--definir", choices=["datos_de_persona", "documentos"], help="tipo cuyo plazo se declara")
    p.add_argument("--meses", type=int, help="plazo en meses (con --definir)")
    p.add_argument("--aplicar", action="store_true", help="anonimiza lo cumplido (sin --escribir, sólo informa)")
    p.add_argument("--escribir", action="store_true", help="ejecuta la anonimización de verdad")
    p.add_argument("--motivo", help="motivo de la anonimización; queda en la bitácora")
    p.add_argument("--usuario", help="email del usuario que ejecuta")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_retencion)

    p = sub.add_parser("cliente", help="clientes")
    sub_c = p.add_subparsers(dest="accion", required=True)
    pc = sub_c.add_parser("crear")
    pc.add_argument("--nombre", required=True)
    pc.add_argument("--rut")
    pc.add_argument("--tipo", choices=["natural", "juridica"], default="natural")
    pc.add_argument("--email")
    pc.add_argument("--telefono")
    pc.add_argument("--representante", help="representante legal (para personas jurídicas)")
    pc.add_argument("--usuario", help="email del usuario que ejecuta")
    pc.add_argument("--estudio", type=int)
    pc.set_defaults(func=cmd_cliente_crear)

    p = sub.add_parser("titular", help="derechos del titular: acceso/portabilidad y supresión de datos")
    sub_t = p.add_subparsers(dest="accion", required=True)
    pt = sub_t.add_parser("exportar", help="reúne todo lo del titular en un JSON con su hash (acceso y portabilidad)")
    pt.add_argument("--rut", help="RUT del titular (con o sin puntos)")
    pt.add_argument("--nombre", help="nombre del titular; calza sin distinguir acentos")
    pt.add_argument("--email", help="correo del titular")
    pt.add_argument("--destino", help="ruta del archivo de salida (por defecto ~/.openlegal/arsopb)")
    pt.add_argument("--usuario", help="email del usuario que ejecuta")
    pt.add_argument("--estudio", type=int)
    pt.set_defaults(func=cmd_titular_exportar)
    pt = sub_t.add_parser("anonimizar", help="borra los identificadores directos y redacta el nombre en los textos")
    pt.add_argument("--rut", help="RUT del titular (con o sin puntos)")
    pt.add_argument("--nombre", help="nombre del titular; calza sin distinguir acentos")
    pt.add_argument("--email", help="correo del titular")
    pt.add_argument("--motivo", required=True, help="por qué se anonimiza: queda escrito en la bitácora")
    pt.add_argument(
        "--escribir",
        action="store_true",
        help="ejecuta la anonimización; sin esta bandera sólo informa qué cambiaría",
    )
    pt.add_argument("--sin-redactar-textos", action="store_true", help="no toca los textos libres del expediente")
    pt.add_argument("--usuario", help="email del usuario que ejecuta")
    pt.add_argument("--estudio", type=int)
    pt.set_defaults(func=cmd_titular_anonimizar)

    p = sub.add_parser("causa", help="causas y expedientes")
    sub_ca = p.add_subparsers(dest="accion", required=True)
    pca = sub_ca.add_parser("crear")
    pca.add_argument("--caratula", required=True)
    pca.add_argument("--cliente", type=int)
    pca.add_argument("--rol-rit")
    pca.add_argument("--tribunal")
    pca.add_argument("--materia")
    pca.add_argument("--contraparte")
    pca.add_argument("--cuantia", type=int)
    pca.add_argument("--usuario")
    pca.add_argument("--estudio", type=int)
    pca.set_defaults(func=cmd_causa_crear)
    pca = sub_ca.add_parser("listar")
    pca.add_argument("--usuario")
    pca.add_argument("--estudio", type=int)
    pca.set_defaults(func=cmd_causa_listar)
    pca = sub_ca.add_parser("asignar")
    pca.add_argument("--causa", type=int, required=True)
    pca.add_argument("--a-usuario", type=int, required=True)
    pca.add_argument("--rol-en-causa", default="colaborador", choices=["responsable", "colaborador", "apoyo"])
    pca.add_argument("--usuario")
    pca.add_argument("--estudio", type=int)
    pca.set_defaults(func=cmd_causa_asignar)

    p = sub.add_parser("plazo", help="plazos procesales")
    sub_p = p.add_subparsers(dest="accion", required=True)
    pp = sub_p.add_parser("crear")
    pp.add_argument("--causa", type=int, required=True)
    pp.add_argument("--descripcion", required=True)
    pp.add_argument("--dias", type=int)
    pp.add_argument("--notificacion", help="YYYY-MM-DD")
    pp.add_argument("--tipo", default="judicial", choices=["judicial", "administrativo", "interno"])
    pp.add_argument("--no-fatal", action="store_true")
    pp.add_argument("--ver-detalle", action="store_true")
    pp.add_argument("--usuario")
    pp.add_argument("--estudio", type=int)
    pp.set_defaults(func=cmd_plazo_crear)
    pp = sub_p.add_parser("simular", help="calcula un vencimiento sin guardarlo")
    pp.add_argument("--notificacion", required=True)
    pp.add_argument("--dias", type=int, required=True)
    pp.set_defaults(func=cmd_plazo_simular)

    p = sub.add_parser("vencimientos", help="proximos plazos")
    p.add_argument("--desde")
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--usuario")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_vencimientos)

    p = sub.add_parser("agenda", help="audiencias proximas")
    p.add_argument("--desde")
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--usuario")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_agenda)

    p = sub.add_parser("panel", help="KPIs del estudio")
    p.add_argument("--usuario")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_panel)

    p = sub.add_parser("auditoria", help="bitacora de acciones")
    p.add_argument("--limite", type=int, default=25)
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_auditoria)

    p = sub.add_parser("audiencia", help="audiencias")
    sub_a = p.add_subparsers(dest="accion", required=True)
    pa = sub_a.add_parser("crear")
    pa.add_argument("--causa", type=int, required=True)
    pa.add_argument("--tipo", required=True)
    pa.add_argument("--fecha", required=True, help="YYYY-MM-DD")
    pa.add_argument("--hora")
    pa.add_argument("--modalidad", default="presencial", choices=["presencial", "remota", "hibrida"])
    pa.add_argument("--lugar", help="sala o URL de la audiencia remota")
    pa.add_argument("--usuario")
    pa.add_argument("--estudio", type=int)
    pa.set_defaults(func=cmd_audiencia_crear)

    p = sub.add_parser("honorario", help="honorarios pactados por causa (montos en CLP enteros)")
    sub_ho = p.add_subparsers(dest="accion", required=True)
    pho = sub_ho.add_parser("crear", help="registra un honorario pactado en una causa")
    pho.add_argument("--causa", type=int, required=True)
    pho.add_argument("--modalidad", default="fijo", choices=list(honorarios.MODALIDADES))
    pho.add_argument("--monto", type=int, help="lo pactado con el cliente, en CLP enteros (350000 = $350.000)")
    pho.add_argument("--descripcion", help="qué se pactó, en palabras")
    pho.add_argument("--fecha", help="desde cuándo rige el pacto: YYYY-MM-DD")
    pho.add_argument("--bruto", type=int, help="monto bruto de tu boleta, en CLP enteros")
    pho.add_argument(
        "--retencion", type=int,
        help="retención que declara el estudio (la copia de su boleta); el CRM no la calcula",
    )
    pho.add_argument("--usuario")
    pho.add_argument("--estudio", type=int)
    pho.set_defaults(func=cmd_honorario_crear)
    pho = sub_ho.add_parser("listar", help="honorarios de una causa, o de las causas visibles")
    pho.add_argument("--causa", type=int)
    pho.add_argument("--usuario")
    pho.add_argument("--estudio", type=int)
    pho.set_defaults(func=cmd_honorario_listar)

    p = sub.add_parser("gasto", help="gastos de una causa (notaría, receptor, peritajes…)")
    sub_g = p.add_subparsers(dest="accion", required=True)
    pg = sub_g.add_parser("crear")
    pg.add_argument("--causa", type=int, required=True)
    pg.add_argument("--concepto", required=True, help="qué se pagó")
    pg.add_argument("--monto", type=int, required=True, help="CLP enteros (25000 = $25.000)")
    pg.add_argument("--fecha", help="YYYY-MM-DD")
    pg.add_argument("--comprobante", help="boleta, factura o recibo que lo respalda")
    pg.add_argument(
        "--lo-pago-el-cliente", action="store_true",
        help="el gasto lo pagó el cliente: queda de constancia y no se le cuenta en la cuenta",
    )
    pg.add_argument("--usuario")
    pg.add_argument("--estudio", type=int)
    pg.set_defaults(func=cmd_gasto_crear)

    p = sub.add_parser("pago", help="pagos recibidos del cliente")
    sub_pg = p.add_subparsers(dest="accion", required=True)
    ppg = sub_pg.add_parser("crear", help="registra un abono; con --honorario N queda imputado")
    ppg.add_argument("--causa", type=int, required=True)
    ppg.add_argument("--monto", type=int, required=True, help="CLP enteros (200000 = $200.000)")
    ppg.add_argument("--fecha", help="la del comprobante: YYYY-MM-DD")
    ppg.add_argument("--medio", default="transferencia", choices=list(honorarios.MEDIOS))
    ppg.add_argument("--referencia", help="n° de transferencia, cheque o comprobante")
    ppg.add_argument("--nota")
    ppg.add_argument("--honorario", type=int, help="honorario de ESTA causa al que se imputa el pago")
    ppg.add_argument("--usuario")
    ppg.add_argument("--estudio", type=int)
    ppg.set_defaults(func=cmd_pago_crear)

    p = sub.add_parser("cuenta", help="cuenta de dividendos de una causa; con --html la deja imprimible")
    p.add_argument("--causa", type=int, required=True)
    p.add_argument("--html", help="ruta donde escribir la cuenta imprimible (A4, todo embebido)")
    p.add_argument("--usuario")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_cuenta)

    p = sub.add_parser("ia", help="uso de IA con datos de causas: autorizar, minimizar, registrar")
    sub_ia = p.add_subparsers(dest="accion", required=True)
    pi = sub_ia.add_parser("autorizar", help="registra la autorización de la causa para tratarse con IA")
    pi.add_argument("--causa", type=int, required=True)
    pi.add_argument("--alcance", default="analisis", choices=["analisis", "redaccion", "ambos"])
    pi.add_argument("--titular", help="quién autoriza: el cliente o su representante")
    pi.add_argument("--base", help="base de licitud (por defecto el art. 13 letra e)")
    pi.add_argument("--usuario")
    pi.add_argument("--estudio", type=int)
    pi.set_defaults(func=cmd_ia_autorizar)
    pi = sub_ia.add_parser("revocar", help="revoca las autorizaciones vigentes de la causa")
    pi.add_argument("--causa", type=int, required=True)
    pi.add_argument("--usuario")
    pi.add_argument("--estudio", type=int)
    pi.set_defaults(func=cmd_ia_revocar)
    pi = sub_ia.add_parser("estado", help="autorización, envíos y términos a minimizar")
    pi.add_argument("--causa", type=int, required=True)
    pi.add_argument("--usuario")
    pi.add_argument("--estudio", type=int)
    pi.set_defaults(func=cmd_ia_estado)
    pi = sub_ia.add_parser("redactar", help="minimiza un texto antes de mandarlo al modelo")
    pi.add_argument("--causa", type=int)
    pi.add_argument("--texto")
    pi.add_argument("--archivo")
    pi.add_argument("--termino", action="append", default=[], help="nombre a reemplazar (repetible)")
    pi.add_argument("--usuario")
    pi.add_argument("--estudio", type=int)
    pi.set_defaults(func=cmd_ia_redactar)
    pi = sub_ia.add_parser("registrar", help="registra un envío ya hecho al proveedor")
    pi.add_argument("--causa", type=int, required=True)
    pi.add_argument("--proveedor", required=True, choices=list(ia.PROVEEDORES))
    pi.add_argument("--modelo")
    pi.add_argument("--documentos", help="qué se mandó, en palabras (por defecto la carátula)")
    pi.add_argument("--texto")
    pi.add_argument("--archivo")
    pi.add_argument("--redactado", action="store_true", help="el texto se envió minimizado")
    pi.add_argument("--usuario")
    pi.add_argument("--estudio", type=int)
    pi.set_defaults(func=cmd_ia_registrar)
    pi = sub_ia.add_parser("transferencias", help="bitácora de comunicaciones a proveedores")
    pi.add_argument("--causa", type=int)
    pi.add_argument("--usuario")
    pi.add_argument("--estudio", type=int)
    pi.set_defaults(func=cmd_ia_transferencias)
    pi = sub_ia.add_parser("proveedores", help="qué sabemos de cada proveedor: país, retención, entrenamiento")
    pi.set_defaults(func=cmd_ia_proveedores)

    p = sub.add_parser(
        "ia-proxy",
        help="proxy local (dialectos de OpenAI y de Anthropic): advierte y registra cada uso de IA",
    )
    p.add_argument("--estado", action="store_true", help="configuración vigente y últimos envíos, sin secretos")
    p.add_argument("--usuario", help="email del usuario que consulta el estado")
    p.add_argument("--estudio", type=int)
    p.set_defaults(func=cmd_ia_proxy)

    p = sub.add_parser("mcp", help="servidor MCP por stdio (puente para el agente)")
    p.add_argument("--usuario", help="email del usuario con el que actúa el agente")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("serve", help="panel web del CRM (para el sidebar del harness)")
    p.add_argument("--host", default="127.0.0.1", help="host de escucha (por defecto solo local)")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--token", help="token del panel; si se omite se genera uno")
    p.add_argument("--cert", help="certificado TLS (PEM). Con esto el panel va en HTTPS")
    p.add_argument("--key", help="clave del certificado, si está en otro archivo")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrada del comando. Los errores del dominio se muestran como frase, no como volcado.

    Un abogado que intenta algo que el sistema no permite (desactivar al último socio,
    tocar una causa ajena, un plazo mal formado) tiene que leer **por qué**, no un
    `Traceback`. Los errores que sí son fallas del programa —un `IntegrityError` de la
    base, un `TypeError`— siguen mostrándose completos: esos hay que arreglarlos.
    """
    args = construir_parser().parse_args(argv)
    try:
        args.func(args)
    except PermissionError as exc:
        salida_error(str(exc))
    except ValueError as exc:
        salida_error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
