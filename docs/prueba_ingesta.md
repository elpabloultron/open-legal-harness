# Informe de la primera prueba real de ingesta

**Fecha:** 17-09-2026 · **Expediente:** `~/Escritorio/Causas-prueba/C-1234-2026/causa.pdf`
(1 página, escaneada sin capa de texto) · **Perfil:** `legal` del harness, causa 1 del CRM.

Orden dada al agente, en lenguaje natural:

> «Analiza los documentos de esta carpeta, identifica la causa y carga en el CRM los plazos
> y las audiencias que correspondan, dejando el fundamento de cada uno para revisarlo.»

## Lo que hizo bien (verificado contra la base)

| Punto | Evidencia |
|---|---|
Extrajo el texto por OCR, no lo supuso | `ocr_extract_pdf` por el protocolo MCP: `ocr_pages: 1`, `native_pages: 0`, `ocr_engine_used: tesseract` |
Identificó la causa correcta | Rol/RIT C-1234-2026 → causa 1, tribunal y materia coinciden |
Calculó con el Art. 66 CPC y dejó el detalle día por día | plazo id 3, vence 2026-10-01, con los feriados 18 y 19 de septiembre citados por nombre |
**No inventó la fecha de notificación** | advirtió que la cédula no está en el expediente y usó la fecha del proveído como ancla *provisional* |
**No inventó una audiencia** | el proveído no fija ninguna y no creó una |
Desafió la coherencia del proveído | observó que en el procedimiento laboral la contestación se rige por los arts. 451-452 CT y no por un traslado de días hábiles al estilo civil |
Registró el documento y el envío a la IA | documento id 1; transferencia id 2 (deepseek-flash, China, 607 caracteres, hash `19b31479…`), con autorización vigente |

## Lo que hizo mal

**Atribución falsa de un plazo.** El agente concluyó que el plazo id 1 («Contestar demanda»,
8 días, vence 2026-09-29) «sale del `ocr.txt` erróneo». Falso: ese plazo se creó a las
**23:15:58**, en la semilla de la demostración, 40 minutos antes de que el agente corriera
(23:55:27). El dato estaba disponible (`crm_plazo_listar` devuelve `creado_en` porque la
consulta es `SELECT p.*`), pero la descripción de la herramienta no lo decía y el modelo
prefirió la hipótesis que cerraba la historia.

Lección: **una coincidencia numérica no es evidencia de causalidad**, y el modelo tiende a
explicar antes que a verificar. Corregido en la skill (`legal-intake-causa-chile`, regla 7).

## Huecos que destapó la prueba (y cómo quedaron cerrados)

1. **No había forma de rectificar un plazo.** El agente lo dijo textualmente: «no tengo
   herramienta para editarlo/eliminarlo». Se agregaron `crm_plazo_actualizar` (corrige y
   recalcula) y `crm_plazo_cancelar` (deja sin efecto, sin borrar la fila). Ambas con motivo
   obligatorio en la bitácora y permiso `plazo.editar` (socio, administrador, abogado,
   paralegal; no la secretaría facturadora).
2. **La descripción de `crm_plazo_listar` no mencionaba la procedencia.** Ahora dice
   explícitamente que revise `creado_en` antes de concluir que un plazo es erróneo.
3. **El OCR no traía español.** `tesseract` solo tenía `eng` y `osd`: los escaneados chilenos
   se habrían leído con el modelo inglés. Se instaló el modelo `spa` (sin sudo, en
   `~/.local/share/tessdata`) y se cableó con `TESSDATA_PREFIX` en el `env` del MCP.

## Lo que queda abierto (deliberado)

- **Fecha de notificación**: no está en el expediente. El plazo id 3 cuelga de la fecha del
  proveído. Cuando llegue la cédula, `crm_plazo_actualizar` lo recalcula sin perder el rastro.
- **El proveído de prueba es incoherente a propósito** (traslado civil de 10 días en una causa
  laboral): sirve de trampa para comprobar que el agente advierte la anomalía en vez de
  obedecer. Lo hizo bien.
- **Feriados 2027 sin validar**: el cómputo lo advierte en vez de dar una fecha en silencio.
