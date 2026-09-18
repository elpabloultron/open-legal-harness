"""CLI del Harness Legal: mismo nucleo para abogado solo y para oficina.

  openlegal init --modo solo
  openlegal estudio crear --nombre "Estudio Perez" --modo oficina
  openlegal usuario crear --email abogado@estudio.cl --nombre "Ana Perez" --rol abogado
  openlegal cliente crear --nombre "Constructora Andes SpA" --rut 76.543.210-K
  openlegal causa crear --caratula "Perez con Andes SpA" --materia laboral
  openlegal causa asignar --causa 1 --usuario 2 --rol-en-causa colaborador
  openlegal plazo crear --causa 1 --descripcion "Contestar demanda" --dias 8 --notificacion 2026-09-17
  openlegal vencimientos --dias 30
  openlegal agenda --dias 15
  openlegal panel
  openlegal auditoria
  openlegal titular exportar --rut 11.111.111-1          # acceso + portabilidad, JSON con hash
  openlegal titular anonimizar --rut 11.111.111-1 --motivo "pide supresión"   # sin --escribir, sólo informa
  openlegal serve --host 127.0.0.1 --port 8899   # panel web (CRM en el sidebar del harness)
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import pathlib
import sys
from typing import NoReturn

from . import auth, ia, plazos, service, titulares
from .db import DB


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


def cmd_usuario_listar(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    _imprimir(
        "usuarios del estudio",
        db.todos(
            "SELECT id, nombre, email, rol, activo FROM usuarios WHERE estudio_id = ? ORDER BY id",
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


def cmd_usuario_clave(args) -> None:
    """Fija la contraseña de un usuario. Se pide dos veces y nunca se escribe en la línea de comandos."""
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
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
    db.ejecutar("UPDATE usuarios SET password_hash = ? WHERE id = ?", (auth.hash_password(primera), usuario["id"]))
    auth.auditar(db, estudio_id, usuario["id"], "usuario.clave", "usuarios", usuario["id"])
    print(f"contrasena actualizada para {args.email} ({usuario['rol']})")


def cmd_usuario_desactivar(args) -> None:
    """Corta el acceso sin borrar el historial: la causa sigue mostrando quién la llevaba."""
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = db.uno(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND email = ?", (estudio_id, args.email.lower())
    )
    if not usuario:
        salida_error(f"no existe el usuario {args.email} en el estudio {estudio_id}")
    db.ejecutar("UPDATE usuarios SET activo = 0 WHERE id = ?", (usuario["id"],))
    db.ejecutar("DELETE FROM sesiones WHERE usuario_id = ?", (usuario["id"],))
    auth.auditar(db, estudio_id, usuario["id"], "usuario.desactivar", "usuarios", usuario["id"])
    print(f"acceso revocado a {args.email}")


def cmd_audiencia_crear(args) -> None:
    db = _ctx(args)
    estudio_id = _estudio_actual(db, args.estudio)
    usuario = _usuario_actual(db, estudio_id, args.usuario)
    audiencia_id = service.crear_audiencia(
        db, usuario, args.causa, args.tipo, args.fecha, args.hora,
        modalidad=args.modalidad, lugar_o_url=args.lugar,
    )
    print(f"audiencia {audiencia_id} creada: {args.tipo} {args.fecha} {args.hora or ''}".strip())


def cmd_mcp(args) -> None:
    """Servidor MCP por stdio: es el puente para que el agente escriba en el CRM."""
    from .mcp import main as mcp_main

    db = getattr(args, "db", None)
    argv = []
    if db:
        argv += ["--db", db]
    if args.usuario:
        argv += ["--usuario", args.usuario]
    mcp_main(argv)


def cmd_serve(args) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError:
        salida_error("falta el servidor web: instala con `pip install 'open-legal-harness[ui]'`")
    from .web import crear_app

    app = crear_app(getattr(args, "db", None), args.token)
    url = f"http://{args.host}:{args.port}/?token={app.state.token}"
    print("\nOpen Legal Harness — CRM Jurídico en marcha")
    print(f"  panel:  {url}")
    print(f"  base:   {app.state.db_url or '(por defecto)'}")
    print("  el token es local; el servicio solo escucha en el host indicado\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


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


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openlegal", description="Harness legal chileno con CRM")
    parser.add_argument("--db", help="URL de la base: sqlite:///ruta.db o postgresql://...")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("init", help="crea el esquema en la base")
    p.set_defaults(func=cmd_init)

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
    pu = sub_u.add_parser("listar")
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_listar)
    pu = sub_u.add_parser("clave", help="fija la contrasena de un usuario (se pide por teclado)")
    pu.add_argument("--email", required=True)
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_clave)
    pu = sub_u.add_parser("desactivar", help="revoca el acceso de un usuario")
    pu.add_argument("--email", required=True)
    pu.add_argument("--estudio", type=int)
    pu.set_defaults(func=cmd_usuario_desactivar)

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

    p = sub.add_parser("mcp", help="servidor MCP por stdio (puente para el agente)")
    p.add_argument("--usuario", help="email del usuario con el que actúa el agente")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("serve", help="panel web del CRM (para el sidebar del harness)")
    p.add_argument("--host", default="127.0.0.1", help="host de escucha (por defecto solo local)")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--token", help="token del panel; si se omite se genera uno")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
