---
name: legal-intake-causa-chile
description: "Ingesta de un expediente chileno: del PDF a los plazos y audiencias en el CRM."
whenToUse: "El usuario entrega un expediente, demanda, proveído o PDF judicial chileno y pide analizarlo, cargarlo, o saber qué plazos corren."
metadata:
  owner: openlegal-crm
  tags: [chile, legal, plazos, art-66-cpc, crm]
---

# Ingesta de un expediente chileno al CRM

Procedimiento para pasar de un expediente (normalmente un PDF) a los plazos, audiencias y
documentos cargados en el CRM, usando las herramientas MCP disponibles. Ejecuta los pasos en
orden y **no inventes fechas ni números de causa: todo dato duro sale del documento o de una
herramienta**.

## Reglas duras

1. **Autorización antes de mandar el expediente a un modelo.** Llama `mcp__crm__crm_ia_estado`
   con el `causa_id`. Si no está autorizada, **detente y pídele al abogado** que autorice
   (`openlegal ia autorizar --causa N --titular "..."`) o que confirme que se puede minimizar.
   Nunca registres un envío sin autorización: el CRM lo rechaza y queda en la bitácora.
2. **Minimiza por defecto.** Antes de razonar sobre texto que va al modelo, pasa el contenido por
   `mcp__crm__crm_ia_redactar` (reemplaza RUT, correos, teléfonos y los nombres de la causa).
   El mapa de reidentificación se queda en el estudio: úsalo para leer la respuesta, no lo
   devuelvas al modelo.
3. **Los plazos se calculan con el Art. 66 CPC, nunca a mano.** Usa `mcp__crm__crm_plazo_calcular`
   (o `crm_plazo_crear`, que calcula y guarda) y muestra el detalle día por día.
4. **Avisa si el año no está validado.** El cómputo devuelve `feriados_pendientes_de_validacion`.
   Si viene en `true`, dilo explícitamente: los feriados de ese año no están validados contra el
   calendario oficial y el vencimiento **no** debe usarse en juicio sin revisarlo.
5. **Propone y confirma.** Crea los plazos y audiencias, pero cierra siempre con un resumen para
   que el abogado firme: qué se creó, con qué fundamento (fecha de notificación + días + detalle)
   y qué quedó dudoso. La responsabilidad de la fecha es del abogado.

## Pasos

1. **Extrae el texto del expediente.** `mcp__open_legal_chile__ocr_extract_pdf` para PDFs
   escaneados (conserva el formato por fojas) o el lector normal si tiene capa de texto. Si no
   puedes leerlo, dilo en vez de suponer su contenido.
2. **Ubica la causa en el CRM.** `mcp__crm__crm_causa_buscar` por carátula, Rol/RIT, tribunal o
   contraparte. Si no existe, créala con `openlegal causa crear` y avisa que lo hiciste. Nunca
   mezcles dos causas parecidas: confirma el Rol/RIT.
3. **Lee el estado actual.** `mcp__crm__crm_causa_leer` para ver plazos y audiencias ya cargados
   y no duplicar.
4. **Interpreta los proveídos y resoluciones.** `mcp__open_legal_chile__pjud_interpretar_proveido`
   para proveídos del OJV y `pjud_analizar_sentencia` para sentencias (expositiva, considerativa,
   resolutiva). Identifica: fecha de notificación, tipo de resolución y qué plazo abre.
5. **Contrasta el derecho aplicable cuando aporte.** `mcp__open_legal_chile__bcn_get_ley` /
   `bcn_get_codigo` (texto vigente) y, si es laboral, `dt_search_doctrina`. Cita en el formato
   `[BCN - Código del Trabajo, Art. 161]`.
6. **Crea cada plazo.** `mcp__crm__crm_plazo_crear` con `causa_id`, descripción («Contestar
   demanda», «Apelar sentencia»), `dias`, `notificacion` (la fecha real del documento) y
   `es_fatal`. La descripción debe explicar el fundamento: qué resolución y qué artículo abre el
   plazo.
7. **Agenda lo que tenga hora.** `mcp__crm__crm_audiencia_crear` (tipo, fecha, hora, modalidad,
   URL si es remota).
8. **Registra los documentos.** `mcp__crm__crm_documento_registrar` con `visibilidad: interno`
   (nunca `cliente` salvo que el abogado lo pida).
9. **Deja la constancia del envío.** Si mandaste texto a un modelo, cierra con
   `mcp__crm__crm_ia_registrar` (causa, proveedor, modelo, documentos, `redactado: true/false`).
10. **Resume y advierte.** Lista lo creado con sus fechas y el detalle del cómputo; separa lo
    verificado de lo dudoso (fechas ilegibles, feriados sin validar, plazos que dependen de una
    notificación que no aparece en el documento).

## Lo que NO debes hacer

- No inventar la fecha de notificación ni el Rol/RIT.
- No crear plazos fatales «por si acaso»: un plazo fatal de más también hace daño.
- No mandar el expediente completo si basta el texto minimizado.
- No usar terminología de common law (discovery, at-will, punitive damages): es derecho chileno.
- No cerrar la tarea sin dejar el resumen para la revisión del abogado.
