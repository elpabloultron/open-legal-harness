"""Uso de IA con datos de causas: autorización, minimización y registro.

La gracia del sistema es entregarle el expediente a un modelo para que lo analice
con las herramientas de open-legal-chile. Eso es tratamiento de datos legítimo,
pero deja de ser un acto privado del estudio: hay una **comunicación a un tercero**
(el proveedor del modelo) y, casi siempre, una **transferencia internacional**
(arts. 27 a 29 de la Ley 19.628 en su texto reformado por la Ley 21.719).

Este módulo no lo impide: lo hace demostrable.

  1. `autorizar` deja registrado que la causa puede tratarse con IA, con qué
     alcance y bajo qué base de licitud (art. 13 letra e): formulación, ejercicio
     o defensa de un derecho ante los tribunales).
  2. `redactar` minimiza: reemplaza RUT, correos, teléfonos y los nombres que se le
     indiquen por marcadores. El mapa de reidentificación se queda en el estudio.
  3. `registrar_transferencia` exige autorización vigente y guarda hash, tamaño,
     proveedor, modelo y destino: qué salió, cuándo y quién lo autorizó.

El módulo no llama a ningún proveedor: registra lo que el agente ya hizo. La
llamada la hace el harness (`dsh`), que es donde el usuario configura el modelo.
"""
from __future__ import annotations

import hashlib
import re

from .db import DB

# --------------------------------------------------------------------- proveedores
# Datos verificados en fuentes públicas de cada proveedor (ver docs/ia_y_transferencia.md).
# `entrena_con_api=False` significa: los datos enviados por API no se usan para
# entrenar por defecto, y eso está en sus términos comerciales o DPA.
PROVEEDORES: dict[str, dict] = {
    "deepseek": {
        "pais": "China",
        "entrena_con_api": None,  # sin verificar en fuentes autorizadas
        "retencion": "verificar en los términos de la plataforma",
        "dpa": "verificar",
        "zdr": "verificar",
        "nota": "No se pudo confirmar en esta sesión con fuente autorizada. No usar con "
                "expedientes hasta tener su contrato de encargado y la cláusula de transferencia.",
    },
    "openai": {
        "pais": "Estados Unidos",
        "entrena_con_api": False,
        "retencion": "30 días por defecto (monitoreo de abuso); ZDR a pedido, con aprobación",
        "dpa": "sí, con cláusulas contractuales tipo",
        "zdr": "sí, sujeta a aprobación (típico 1 a 3 semanas)",
        "nota": "ZDR excluye el monitoreo de abuso. Para residencia UE, usar la ruta Azure OpenAI.",
    },
    "anthropic": {
        "pais": "Estados Unidos",
        "entrena_con_api": False,
        "retencion": "7 días por defecto en API (bajó de 30 el 14-09-2025); ZDR a pedido",
        "dpa": "sí, con cláusulas contractuales tipo; ley irlandesa",
        "zdr": "sí, sujeta a aprobación",
        "nota": "La retención por defecto más baja del mercado en API. Consumidor (claude.ai) "
                "es otro régimen: ahí sí puede entrenar con tus datos.",
    },
    "local": {
        "pais": "el del estudio",
        "entrena_con_api": False,
        "retencion": "la que fije el estudio",
        "dpa": "no aplica: no hay encargado",
        "zdr": "no aplica",
        "nota": "Modelo corriendo en la máquina. Sin comunicación a tercero ni transferencia "
                "internacional: es la opción sin papeles que firmar.",
    },
}

# ----------------------------------------------------------------- minimizacion
PATRON_RUT = re.compile(r"\b(\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK])\b")
PATRON_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PATRON_TELEFONO = re.compile(r"\b(?:\+?56)?\s?9\s?\d{4}\s?\d{4}\b")


def _rut_valido(rut: str) -> bool:
    """Módulo 11. La implementación vive en `seguridad.rut_valido`, para tenerla una sola vez."""
    from .seguridad import rut_valido

    return rut_valido(rut)


def redactar(
    texto: str,
    terminos: list[str] | None = None,
    avisos: list[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Devuelve (texto minimizado, mapa para reidentificar localmente).

    Los RUT y correos se reemplazan siempre (identifican directo). Además se
    reemplazan los nombres propios que se declaren en `terminos`: los de la ficha
    de la causa **y los que aparezcan dentro del documento**, que el CRM no conoce
    (partes, representantes, testigos). Pásalos: si no, quedan a la vista.

    Un RUT con dígito verificador inválido se enmascara igual, con `[RUT?·n]` y un
    aviso: puede ser un RUT extranjero, mal escaneado o inventado, y justamente por
    eso el minimizador no puede dejarlo pasar — filtrar un dato personal por un
    dígito mal calculado sería el peor de los fallos posibles.
    """
    mapa: dict[str, str] = {}
    contador = {"RUT": 0, "RUT?": 0, "CORREO": 0, "TELEFONO": 0, "NOMBRE": 0}

    def marcador(tipo: str, valor: str) -> str:
        clave = f"[{tipo}·{len(mapa) + 1}]"
        mapa[clave] = valor
        contador[tipo] = contador.get(tipo, 0) + 1
        return clave

    def reemplazar_rut(coincidencia: re.Match) -> str:
        valor = coincidencia.group(1)
        if _rut_valido(valor):
            return marcador("RUT", valor)
        if avisos is not None:
            avisos.append(
                f"el RUT {valor!r} no valida su dígito verificador (módulo 11): se enmascara "
                f"igual, pero conviene revisarlo — puede venir mal escaneado del expediente"
            )
        return marcador("RUT?", valor)

    texto = PATRON_RUT.sub(reemplazar_rut, texto)
    texto = PATRON_EMAIL.sub(lambda m: marcador("CORREO", m.group(0)), texto)
    texto = PATRON_TELEFONO.sub(lambda m: marcador("TELEFONO", m.group(0)), texto)
    for termino in sorted(terminos or [], key=len, reverse=True):
        if not termino:
            continue
        # Insensible a mayúsculas: en el expediente los nombres aparecen en caja alta
        # ("MARÍA FERNANDA PÉREZ SOTO", "ANDES SpA") y el término viene de la ficha de la
        # causa en caja normal. Con comparación exacta, el nombre se filtraba igual.
        patron = re.compile(re.escape(termino), re.IGNORECASE)
        primera = patron.search(texto)
        if primera:
            clave = marcador("NOMBRE", primera.group(0))
            texto = patron.sub(lambda _: clave, texto)
    return texto, mapa


def reidentificar(texto: str, mapa: dict[str, str]) -> str:
    """Devuelve el texto original a partir de los marcadores (queda en el estudio)."""
    for marcador_, valor in mapa.items():
        texto = texto.replace(marcador_, valor)
    return texto


def hash_payload(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def terminos_de_causa(db: DB, causa_id: int) -> list[str]:
    """Nombres que conviene minimizar antes de mandar una causa a un modelo."""
    causa = db.uno("SELECT caratula, contraparte FROM causas WHERE id = ?", (causa_id,))
    if not causa:
        return []
    terminos: list[str] = []
    if causa.get("contraparte"):
        terminos.append(causa["contraparte"])
    cliente = db.uno(
        "SELECT c.nombre, c.representante_legal FROM clientes c "
        "JOIN causas ca ON ca.cliente_id = c.id WHERE ca.id = ?",
        (causa_id,),
    )
    if cliente:
        terminos += [t for t in (cliente.get("nombre"), cliente.get("representante_legal")) if t]
    equipo = db.todos(
        "SELECT u.nombre FROM causa_equipo e JOIN usuarios u ON u.id = e.usuario_id WHERE e.causa_id = ?",
        (causa_id,),
    )
    terminos += [f["nombre"] for f in equipo]
    return sorted(set(terminos))
