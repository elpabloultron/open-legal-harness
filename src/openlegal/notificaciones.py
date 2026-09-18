"""Avisos del estudio por correo y SMS: en cola, sin repetir y con registro.

Funciona en tres pasos, y el orden importa:

1. `generar_recordatorios()` mira los plazos por vencer y las audiencias próximas y
   ENCOLA un aviso por cada uno y cada destinatario, con una huella que impide repetirlo.
2. El CRM **nunca manda nada mientras atiende a un abogado**: la cola queda en la base.
   Un servidor de correo lento o caído no puede dejar el panel esperando.
3. `enviar_pendientes()` saca la cola por correo (SMTP) o SMS (proveedor configurable) y
   deja cada intento con su resultado. Se corre a mano o desde cron cada pocos minutos.

Nada de credenciales en el código: van en `~/.openlegal/notificaciones.json` (permisos
600) o en variables de entorno, y `resumen_config()` las informa **sin mostrar la clave**.
Un aviso que falla suma su intento y guarda el error; después de varios intentos queda
como `fallida` y deja de reintentarse, pero no se borra: queda el registro.

Sobre el SMS: necesita una cuenta de un proveedor (Twilio, o cualquier servicio con un
webhook). Sin eso configurado, el canal queda declarado como no disponible y el aviso
sale por correo — que es mejor que perderlo o fallar en silencio.
"""
from __future__ import annotations

import datetime as dt
import email.message
import json
import os
import pathlib
import smtplib
import urllib.parse
import urllib.request

from . import auth
from .db import DB

#: Permiso para generar y despachar avisos. No lo tiene el rol `cliente`: la maquinaria
#: de avisos es del estudio, no del cliente.
PERMISO = "aviso.gestionar"

#: Cuántos días antes se avisa de un plazo o de una audiencia, si no se configura otra cosa.
DIAS_DE_AVISO = 3

#: Intentos antes de dar un aviso por fallido (no se borra: queda con su error).
MAX_INTENTOS = 5

#: Un SMS se cobra por segmento de 160 caracteres: el aviso corto entra en dos.
LIMITE_SMS = 320

#: Archivo de configuración por defecto, y variables de entorno equivalentes.
RUTA_CONFIG = pathlib.Path.home() / ".openlegal" / "notificaciones.json"


def ruta_config() -> pathlib.Path:
    """Dónde está el archivo de configuración, resolviéndolo en el momento.

    Se resuelve al usarlo y no al importar el módulo, para que la variable de entorno
    valga aunque se defina después, y para que las pruebas puedan apuntar a un archivo
    temporal sin tocar la configuración real del estudio.
    """
    return pathlib.Path(os.environ.get("OPENLEGAL_CONFIG") or RUTA_CONFIG)

#: Canales que existen. `consola` no manda nada afuera: deja el aviso en un archivo y sirve
#: para probar el circuito completo sin cuentas ni credenciales.
CANALES = ("email", "sms", "consola")


def cargar_config() -> dict:
    """La configuración vigente: archivo + variables de entorno (que mandan sobre el archivo)."""
    config: dict = {"email": {}, "sms": {}, "canales_por_defecto": ["email"], "dias_de_aviso": DIAS_DE_AVISO}
    archivo = ruta_config()
    if archivo.is_file():
        try:
            guardado = json.loads(archivo.read_text(encoding="utf-8"))
            if isinstance(guardado, dict):
                for clave, valor in guardado.items():
                    if isinstance(valor, dict) and isinstance(config.get(clave), dict):
                        config[clave].update(valor)
                    else:
                        config[clave] = valor
                config["email"] = dict(guardado.get("email") or {})
                config["sms"] = dict(guardado.get("sms") or {})
        except json.JSONDecodeError as exc:
            raise ValueError(f"{archivo} no es JSON válido: {exc}") from exc

    equivalencias = {
        "email": {"SMTP_HOST": "host", "SMTP_PUERTO": "puerto", "SMTP_USUARIO": "usuario",
                  "SMTP_CLAVE": "clave", "SMTP_DE": "de"},
        "sms": {"SMS_PROVEEDOR": "proveedor", "SMS_CUENTA": "cuenta", "SMS_TOKEN": "token",
                "SMS_DE": "de", "SMS_WEBHOOK": "webhook"},
    }
    for canal, mapa in equivalencias.items():
        for sufijo, campo in mapa.items():
            valor = os.environ.get(f"OPENLEGAL_{sufijo}")
            if valor:
                config[canal][campo] = int(valor) if campo == "puerto" else valor
    if os.environ.get("OPENLEGAL_AVISO_DIAS"):
        config["dias_de_aviso"] = int(os.environ["OPENLEGAL_AVISO_DIAS"])
    if os.environ.get("OPENLEGAL_CANALES"):
        config["canales_por_defecto"] = [c.strip() for c in os.environ["OPENLEGAL_CANALES"].split(",") if c.strip()]
    return config


def resumen_config(config: dict | None = None) -> list[dict]:
    """Qué está configurado y qué falta, canal por canal. Nunca incluye la clave."""
    config = config or cargar_config()
    filas = []
    correo = config.get("email") or {}
    faltan = [c for c in ("host", "puerto", "usuario", "clave", "de") if not correo.get(c)]
    cifrado = str(correo.get("seguridad", "starttls")).lower()
    filas.append({
        "canal": "email",
        "listo": not faltan,
        "detalle": (
            f"{correo.get('host')}:{correo.get('puerto')} como {correo.get('usuario')} "
            f"({'sin cifrado: sólo para un relay local' if cifrado == 'ninguna' else 'con STARTTLS'})"
            if not faltan else f"falta configurar: {', '.join(faltan)}"
        ),
    })
    sms = config.get("sms") or {}
    proveedor = (sms.get("proveedor") or "").lower()
    if proveedor == "twilio":
        faltan_sms = [c for c in ("cuenta", "token", "de") if not sms.get(c)]
    elif proveedor == "webhook":
        faltan_sms = [c for c in ("webhook",) if not sms.get(c)]
    elif proveedor == "consola":
        faltan_sms = []
    else:
        faltan_sms = ["proveedor (twilio, webhook o consola)"]
    filas.append({
        "canal": "sms",
        "listo": not faltan_sms,
        "detalle": (f"proveedor {proveedor}" if not faltan_sms else f"falta configurar: {', '.join(faltan_sms)}"),
    })
    filas.append({"canal": "consola", "listo": True, "detalle": "deja el aviso en un archivo; sirve para probar"})
    filas.append({"canal": "canales por defecto", "listo": True, "detalle": ", ".join(config.get("canales_por_defecto") or [])})
    filas.append({"canal": "días de aviso", "listo": True, "detalle": str(config.get("dias_de_aviso", DIAS_DE_AVISO))})
    return filas


# ------------------------------------------------------------------- la cola
def cuerpo_del_canal(canal: str, asunto: str, completo: str) -> str:
    """El texto que se manda según el canal.

    Un SMS se cobra por segmento de 160 caracteres: mandarle el cuerpo largo del correo
    sería carísimo y absurdo en una pantalla de teléfono. Va el asunto y, si cabe, las
    primeras líneas del detalle.
    """
    if canal != "sms":
        return completo
    corto = asunto.strip()
    for linea in completo.splitlines()[2:]:
        if not linea.strip():
            continue
        if len(corto) + len(linea) + 3 > LIMITE_SMS:
            break
        corto += " · " + linea.strip()
    return corto[:LIMITE_SMS]


def encolar(
    db: DB,
    estudio_id: int,
    canal: str,
    destino: str,
    cuerpo: str,
    *,
    clave: str,
    asunto: str | None = None,
    usuario_id: int | None = None,
    causa_id: int | None = None,
    plazo_id: int | None = None,
    audiencia_id: int | None = None,
    prioridad: str = "normal",
    programada_para: str | None = None,
) -> int | None:
    """Pone un aviso en la cola. Devuelve None si esa misma clave ya estaba (no se repite).

    La `clave` es la huella del aviso (por ejemplo `plazo:12:email:2026-09-20`): la
    columna es única en la base, así que dos corridas del mismo día no mandan dos veces
    lo mismo, aunque las dos hayan visto el mismo plazo por vencer.
    """
    if canal not in CANALES:
        raise ValueError(f"canal desconocido: {canal} (conocidos: {', '.join(CANALES)})")
    if not destino:
        raise ValueError("el aviso necesita un destino (correo o teléfono)")
    if not cuerpo or not cuerpo.strip():
        raise ValueError("el aviso necesita cuerpo")
    existente = db.uno("SELECT id FROM notificaciones WHERE clave = ?", (clave,))
    if existente:
        return None
    return db.insertar(
        "notificaciones",
        {
            "estudio_id": estudio_id,
            "usuario_id": usuario_id,
            "causa_id": causa_id,
            "plazo_id": plazo_id,
            "audiencia_id": audiencia_id,
            "canal": canal,
            "destino": destino,
            "asunto": asunto,
            "cuerpo": cuerpo.strip(),
            "prioridad": prioridad,
            "programada_para": programada_para,
            "clave": clave,
        },
    )


def pendientes(db: DB, limite: int = 50, ahora: str | None = None) -> list[dict]:
    """Los avisos que ya se pueden mandar (los programados para más adelante, no)."""
    momento = ahora or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return db.todos(
        "SELECT * FROM notificaciones WHERE estado = 'pendiente' "
        "AND (programada_para IS NULL OR programada_para <= ?) "
        "ORDER BY CASE prioridad WHEN 'alta' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id LIMIT ?",
        (momento, limite),
    )


def estado(db: DB) -> dict:
    """Cuántos avisos hay en cada estado, y qué está fallando ahora mismo.

    `ultimas_fallas` lista los avisos con error registrado, estén todavía en la cola o ya
    dados por fallidos: lo útil para el estudio es ver qué no está saliendo **hoy**, no
    sólo lo que se rindió.
    """
    conteos: dict[str, int] = {}
    for fila in db.todos("SELECT estado, COUNT(*) AS total FROM notificaciones GROUP BY estado"):
        conteos[fila["estado"]] = fila["total"]
    ultimos = db.todos(
        "SELECT canal, destino, estado, intentos, ultimo_error, creada_en FROM notificaciones "
        "WHERE ultimo_error IS NOT NULL ORDER BY id DESC LIMIT 5"
    )
    return {"conteos": conteos, "ultimas_fallas": ultimos, "max_intentos": MAX_INTENTOS}


# -------------------------------------------------------------- los transportes
def armar_correo(config: dict, destino: str, asunto: str | None, cuerpo: str) -> email.message.EmailMessage:
    """El mensaje listo para mandar (aparte de mandarlo, para poder probarlo sin SMTP)."""
    mensaje = email.message.EmailMessage()
    correo = config.get("email") or {}
    mensaje["From"] = correo.get("de") or correo.get("usuario") or "avisos@localhost"
    mensaje["To"] = destino
    mensaje["Subject"] = asunto or "Aviso del estudio"
    mensaje.set_content(cuerpo)
    return mensaje


def armar_peticion_sms(config: dict, destino: str, cuerpo: str) -> tuple[str, dict, bytes]:
    """(url, cabeceras, cuerpo) de la petición al proveedor de SMS.

    Se arma aparte del envío por la misma razón que el correo: probar el contenido sin
    salir a la red.
    """
    sms = config.get("sms") or {}
    proveedor = (sms.get("proveedor") or "").lower()
    if proveedor == "twilio":
        cuenta = sms.get("cuenta", "")
        import base64

        credencial = base64.b64encode(f"{cuenta}:{sms.get('token', '')}".encode()).decode()
        url = f"https://api.twilio.com/2010-04-01/Accounts/{cuenta}/Messages.json"
        cabeceras = {
            "Authorization": f"Basic {credencial}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        datos = urllib.parse.urlencode({"From": sms.get("de", ""), "To": destino, "Body": cuerpo}).encode()
        return url, cabeceras, datos
    if proveedor == "webhook":
        url = str(sms.get("webhook", ""))
        cabeceras = {"Content-Type": "application/json"}
        if sms.get("token"):
            cabeceras["Authorization"] = f"Bearer {sms['token']}"
        datos = json.dumps({"to": destino, "text": cuerpo, "from": sms.get("de", "")}).encode("utf-8")
        return url, cabeceras, datos
    raise ValueError(f"proveedor de SMS no soportado: {proveedor or '(ninguno)'}")


def _enviar_correo(config: dict, destino: str, asunto: str | None, cuerpo: str) -> None:
    correo = config.get("email") or {}
    faltan = [c for c in ("host", "puerto", "usuario", "clave", "de") if not correo.get(c)]
    if faltan:
        raise ValueError(f"el correo no está configurado: falta {', '.join(faltan)}")
    mensaje = armar_correo(config, destino, asunto, cuerpo)
    # Por defecto se cifra la conexión. `"seguridad": "ninguna"` existe sólo para un relay
    # local de la propia oficina (localhost), que no habla TLS; usarlo contra un servidor
    # de afuera mandaría la clave en claro.
    with smtplib.SMTP(correo["host"], int(correo["puerto"]), timeout=30) as servidor:
        servidor.ehlo()
        if str(correo.get("seguridad", "starttls")).lower() != "ninguna":
            servidor.starttls()
            servidor.ehlo()
        servidor.login(correo["usuario"], correo["clave"])
        servidor.send_message(mensaje)


def _enviar_sms(config: dict, destino: str, cuerpo: str) -> None:
    sms = config.get("sms") or {}
    if (sms.get("proveedor") or "").lower() == "consola":
        # Modo de prueba documentado: no sale a la red, deja el aviso en el archivo.
        _enviar_consola(config, destino, None, cuerpo)
        return
    url, cabeceras, datos = armar_peticion_sms(config, destino, cuerpo)
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras)
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:
        respuesta.read()


def _enviar_consola(config: dict, destino: str, asunto: str | None, cuerpo: str) -> None:
    """Deja el aviso en un archivo del estudio. No sale a la red: sirve para probar."""
    archivo = pathlib.Path(os.environ.get("OPENLEGAL_AVISOS_LOG", pathlib.Path.home() / ".openlegal" / "avisos.log"))
    archivo.parent.mkdir(parents=True, exist_ok=True)
    with archivo.open("a", encoding="utf-8") as salida:
        salida.write(f"--- {dt.datetime.now().isoformat(timespec='seconds')} · para {destino} ---\n")
        if asunto:
            salida.write(f"{asunto}\n")
        salida.write(f"{cuerpo}\n")


def enviar_pendientes(db: DB, usuario: dict, limite: int = 50, config: dict | None = None) -> dict:
    """Saca la cola: manda lo pendiente y deja el resultado de cada intento.

    Un aviso que falla no se pierde: suma su intento, guarda el error y sigue pendiente
    hasta `MAX_INTENTOS`, cuando queda como `fallida` (con el error a la vista, sin
    borrarse). El resumen va a la bitácora: quién despachó, cuánto salió y cuánto falló.
    """
    auth.exigir(db, usuario, PERMISO)
    config = config if config is not None else cargar_config()
    filas = pendientes(db, limite)
    enviadas, fallidas, errores = 0, 0, []
    for fila in filas:
        canal = fila["canal"]
        try:
            if canal == "email":
                _enviar_correo(config, fila["destino"], fila["asunto"], fila["cuerpo"])
            elif canal == "sms":
                _enviar_sms(config, fila["destino"], fila["cuerpo"])
            else:
                _enviar_consola(config, fila["destino"], fila["asunto"], fila["cuerpo"])
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del transporte se registra, no tumba la corrida
            intentos = int(fila["intentos"] or 0) + 1
            nuevo_estado = "fallida" if intentos >= MAX_INTENTOS else "pendiente"
            db.ejecutar(
                "UPDATE notificaciones SET intentos = ?, estado = ?, ultimo_error = ? WHERE id = ?",
                (intentos, nuevo_estado, f"{type(exc).__name__}: {str(exc)[:200]}", fila["id"]),
            )
            fallidas += 1
            errores.append({"id": fila["id"], "canal": canal, "error": str(exc)[:120]})
            continue
        db.ejecutar(
            "UPDATE notificaciones SET estado = 'enviada', enviada_en = ?, intentos = intentos + 1, ultimo_error = NULL "
            "WHERE id = ?",
            (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fila["id"]),
        )
        enviadas += 1
    if filas:
        auth.auditar(
            db, usuario["estudio_id"], usuario["id"], "notificacion.despachar", "notificaciones", None,
            f"enviadas={enviadas} fallidas={fallidas} de {len(filas)} en cola",
        )
    return {"revisadas": len(filas), "enviadas": enviadas, "fallidas": fallidas, "errores": errores[:5]}


def _anotar_error(exc: BaseException) -> None:
    """Deja constancia de un aviso que no se pudo ni encolar.

    `avisar_asignacion` no puede lanzar (la creación del plazo ya ocurrió), pero tragarse
    el motivo lo volvería indiagnosticable: si algo se rompe acá, queda escrito.
    """
    try:
        archivo = pathlib.Path(
            os.environ.get("OPENLEGAL_ERRORES_LOG", pathlib.Path.home() / ".openlegal" / "errores.log")
        )
        archivo.parent.mkdir(parents=True, exist_ok=True)
        with archivo.open("a", encoding="utf-8") as salida:
            salida.write(f"--- {dt.datetime.now().isoformat(timespec='seconds')} · aviso no encolado ---\n")
            salida.write(f"{type(exc).__name__}: {exc}\n")
    except Exception:  # noqa: BLE001 - si hasta esto falla, no hay nada que hacer
        pass


def avisar_asignacion(
    db: DB,
    usuario: dict,
    tipo: str,
    objeto_id: int,
    descripcion: str,
    *,
    causa_id: int | None = None,
    responsable_id: int | None = None,
    cuando: str | None = None,
    plazo_id: int | None = None,
    audiencia_id: int | None = None,
) -> int | None:
    """Avisa al responsable cuando le asignan un plazo o una audiencia.

    Es el aviso del momento, distinto del recordatorio de vencimiento: «te tocó esto».
    No avisa si el responsable es quien lo creó (nadie necesita un correo de lo que acaba
    de escribir) y se puede apagar con `avisar_asignaciones: false` en la configuración.

    **Nunca lanza**: si el aviso no se puede encolar, la creación del plazo ya ocurrió y
    no se puede deshacer por un correo. Devuelve None y sigue.
    """
    try:
        if responsable_id is None or responsable_id == usuario["id"]:
            return None
        config = cargar_config()
        if config.get("avisar_asignaciones") is False:
            return None
        persona = db.uno("SELECT * FROM usuarios WHERE id = ? AND activo = 1", (responsable_id,))
        if not persona:
            return None
        canales = list(config.get("canales_por_defecto") or ["email"])
        creadas = 0
        for canal in canales:
            destino = (persona.get("email") if canal == "email" else persona.get("telefono")) or ""
            if not destino:
                continue
            asunto = f"Te asignaron {'un plazo' if tipo == 'plazo' else 'una audiencia'}: {descripcion}"
            cuerpo = f"{asunto}\n\n{cuando or ''}\n".strip() + (
                "\nEntra al CRM del estudio para ver el expediente completo.\n"
            )
            if encolar(
                db, usuario["estudio_id"], canal, destino, cuerpo_del_canal(canal, asunto, cuerpo),
                clave=f"{tipo}:{objeto_id}:{canal}:{persona['id']}:asignacion",
                asunto=asunto, usuario_id=persona["id"], causa_id=causa_id,
                plazo_id=plazo_id, audiencia_id=audiencia_id,
            ):
                creadas += 1
        return creadas
    except Exception as exc:  # noqa: BLE001 - un aviso no puede tumbar la creación del plazo
        _anotar_error(exc)
        return None


# ------------------------------------------------------- recordatorios del día
def _destinatarios(db: DB, usuario: dict, plazo: dict | None, audiencia: dict | None) -> list[dict]:
    """A quién se le avisa: al responsable, y si no hay, a los abogados del estudio.

    Que un plazo se quede sin responsable en el CRM no puede significar que nadie se
    entere de que vence: en ese caso el aviso va a los socios y abogados activos.
    """
    responsable_id = (plazo or audiencia or {}).get("responsable_id")
    if responsable_id:
        fila = db.uno("SELECT * FROM usuarios WHERE id = ? AND activo = 1", (responsable_id,))
        if fila:
            return [fila]
    filas = db.todos(
        "SELECT * FROM usuarios WHERE estudio_id = ? AND activo = 1 AND rol IN ('socio','abogado') ORDER BY id",
        (usuario["estudio_id"],),
    )
    return filas[:5]


def generar_recordatorios(
    db: DB,
    usuario: dict,
    dias: int | None = None,
    canales: list[str] | None = None,
    hoy: dt.date | None = None,
) -> dict:
    """Encola avisos para los plazos por vencer y las audiencias próximas.

    La clave incluye el día, así que el mismo plazo avisa **una vez por día** mientras
    siga pendiente: si no lo marcas como cumplido, te vuelve a avisar mañana; y si lo
    marcas, los avisos se acabaron. No hay avisos repetidos dentro del mismo día.
    """
    auth.exigir(db, usuario, PERMISO)
    config = cargar_config()
    dias = int(dias if dias is not None else config.get("dias_de_aviso", DIAS_DE_AVISO))
    canales = canales or list(config.get("canales_por_defecto") or ["email"])
    base = hoy or dt.date.today()
    limite = (base + dt.timedelta(days=dias)).isoformat()
    encoladas, repetidas, sin_destino = 0, 0, 0

    plazos = db.todos(
        "SELECT p.*, c.caratula FROM plazos p JOIN causas c ON c.id = p.causa_id "
        "WHERE p.estado = 'pendiente' AND p.fecha_vencimiento IS NOT NULL AND p.fecha_vencimiento <= ? "
        "ORDER BY p.fecha_vencimiento",
        (limite,),
    )
    for plazo in plazos:
        vence = dt.date.fromisoformat(str(plazo["fecha_vencimiento"])[:10])
        faltan = (vence - base).days
        if faltan == 0:
            cuando = "vence hoy"
        elif faltan < 0:
            cuando = f"vencido hace {-faltan} día(s)"
        else:
            cuando = f"vence en {faltan} día(s)"
        urgente = "FATAL · " if plazo.get("es_fatal") else ""
        asunto = f"{urgente}{cuando}: {plazo['descripcion']}"
        cuerpo = (
            f"{asunto}\n\n"
            f"Causa: {plazo['caratula']}\n"
            f"Vencimiento: {plazo['fecha_vencimiento']}\n"
            f"Estado: {plazo['estado']}\n\n"
            "Márcalo como cumplido en el CRM cuando esté presentado, así los avisos paran."
        )
        for persona in _destinatarios(db, usuario, plazo, None):
            for canal in canales:
                destino = (persona.get("email") if canal == "email" else persona.get("telefono")) or ""
                if not destino:
                    sin_destino += 1
                    continue
                creada = encolar(
                    db, usuario["estudio_id"], canal, destino, cuerpo_del_canal(canal, asunto, cuerpo),
                    # La huella incluye al destinatario: si no, el segundo avisado del mismo
                    # plazo quedaría comido como «repetido» y nunca se enteraría.
                    clave=f"plazo:{plazo['id']}:{canal}:{persona['id']}:{base.isoformat()}",
                    asunto=asunto, usuario_id=persona["id"], causa_id=plazo["causa_id"], plazo_id=plazo["id"],
                    prioridad="alta" if (plazo.get("es_fatal") or faltan <= 0) else "normal",
                )
                encoladas += 1 if creada else 0
                repetidas += 0 if creada else 1

    proximas = db.todos(
        "SELECT a.*, c.caratula FROM audiencias a JOIN causas c ON c.id = a.causa_id "
        "WHERE a.estado = 'programada' AND a.fecha >= ? AND a.fecha <= ? ORDER BY a.fecha",
        (base.isoformat(), limite),
    )
    for audiencia in proximas:
        dia = dt.date.fromisoformat(str(audiencia["fecha"])[:10])
        faltan = (dia - base).days
        asunto = f"Audiencia en {faltan} día(s): {audiencia['tipo']}"
        cuerpo = (
            f"{asunto}\n\n"
            f"Causa: {audiencia['caratula']}\n"
            f"Fecha: {audiencia['fecha']} {audiencia.get('hora') or ''}\n"
            f"Modalidad: {audiencia.get('modalidad')}\n"
            f"Lugar o enlace: {audiencia.get('lugar_o_url') or '(sin dato)'}\n"
        )
        for persona in _destinatarios(db, usuario, None, audiencia):
            for canal in canales:
                destino = (persona.get("email") if canal == "email" else persona.get("telefono")) or ""
                if not destino:
                    sin_destino += 1
                    continue
                creada = encolar(
                    db, usuario["estudio_id"], canal, destino, cuerpo_del_canal(canal, asunto, cuerpo),
                    clave=f"audiencia:{audiencia['id']}:{canal}:{persona['id']}:{base.isoformat()}",
                    asunto=asunto, usuario_id=persona["id"], causa_id=audiencia["causa_id"],
                    audiencia_id=audiencia["id"],
                )
                encoladas += 1 if creada else 0
                repetidas += 0 if creada else 1

    return {
        "plazos_revisados": len(plazos),
        "audiencias_revisadas": len(proximas),
        "dias_de_aviso": dias,
        "canales": canales,
        "encoladas": encoladas,
        "repetidas": repetidas,
        "sin_destino": sin_destino,
    }
