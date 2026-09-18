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
6. **Rectifica, no dupliques.** Si un plazo ya cargado está mal, **no crees otro encima**: usa
   `mcp__crm__crm_plazo_actualizar` (corrige y recalcula) o `mcp__crm__crm_plazo_cancelar` con el
   `motivo`. Dos plazos fatales incompatibles en la misma causa es peor que uno equivocado, porque
   el abogado ya no sabe cuál seguir. El motivo queda en la bitácora: escríbelo como se lo
   explicarías a un tercero.
7. **Antes de culpar a un plazo, mira su `creado_en`.** `crm_plazo_listar` devuelve `creado_en`,
   `estado` y `responsable_id`, y `crm_auditoria_leer` dice quién lo creó, cuándo y con qué
   motivo. Un plazo anterior al inicio de tu sesión lo creó otra persona (o una carga de datos):
   no lo atribuyas al documento que estás leyendo. Si la coincidencia entre un plazo viejo y un
   error de lectura te parece sospechosa, es una hipótesis, no un hecho: preséntala como hipótesis,
   o mejor, verifícala con la bitácora antes de escribirla.
8. **Verifica que la resolución sea coherente con el procedimiento.** Si el proveído ordena algo
   que no calza con la materia (por ejemplo, un traslado de días hábiles al estilo civil en un
   procedimiento laboral, donde la contestación se rige por los arts. 451 y 452 del Código del
   Trabajo), **no lo cargues en silencio**: crea el plazo si el documento lo manda, y advierte la
   anomalía aparte, indicando qué norma habría que aplicar. Antes de eso, contrasta con
   `mcp__open_legal_chile__bcn_get_codigo` (ojo: el código se llama `trabajo`, no «Código del
   Trabajo»). Igual con las audiencias: si la resolución no fija ninguna, no inventes fecha —
   pregunta si falta un escrito en el expediente.
9. **Registra el envío a la IA en cada corrida, y cita el registro que acabas de crear.** Si el
   texto del expediente llegó al modelo en esta sesión, `crm_ia_registrar` se llama en esta sesión
   y el informe muestra el *id nuevo*. Nunca presentes el hash de un envío anterior como si fuera
   el de ahora, aunque el texto minimizado sea el mismo y el hash calce: el registro de
   transferencias es la prueba de que el envío ocurrió, no un dato decorativo.
10. **No dupliques un plazo que ya está bien.** Antes de crear, compara con los pendientes de la
    causa: si ya existe uno con los mismos días y la misma fecha de notificación, **no crees otro**
    —dilo («ya estaba cargado y coincide»). Cancelar uno correcto para crear un idéntico deja la
    bitácora llena de ruido y le hace perder tiempo al abogado. Si el plazo que ya existe está
    **mal** (días distintos a los que manda el proveído), no lo dupliques ni lo dejes: rectifícalo
    con `mcp__crm__crm_plazo_actualizar`.
11. **El memo se escribe desde las lecturas de ESTA corrida, no desde tu memoria de la
    conversación.** Toda afirmación sobre el estado del CRM (qué plazos existen, cuáles están
    cancelados, qué ids) tiene que salir de una respuesta de herramienta de esta misma corrida. Si
    venís de turnos anteriores en la misma conversación, **el CRM pudo cambiar en el medio** (otra
    persona, una carga, un reinicio): vuelve a leer antes de escribir. Cierra el memo con una línea
    de control: «Estado del CRM al escribir: N plazos en la causa, leído a las HH:MM»
    (`mcp__crm__crm_plazo_listar` + `mcp__crm__crm_causa_leer`). Un memo sin esa línea es una
    afirmación sin respaldo.
12. **Si una herramienta contradice lo que creías, investiga la contradicción: no la descartes.**
    Caso real: `crm_auditoria_leer` respondió que un plazo no tenía ningún evento, y el agente
    razonó «no bitacorará mis registros, extraño pero no crítico» y siguió escribiendo. Cuando el
    CRM dice que un registro **no existe** y tú creías haberlo creado, la conclusión correcta es
    que **tu recuerdo está desactualizado** —y el memo debe decir eso—, no inventariar registros
    que ya no están. Una contradicción entre lo que crees y lo que devuelve la herramienta es un
    hallazgo para el abogado, no un detalle a racionalizar.
13. **Declara en `terminos` los nombres que aparecen en el documento, y revisa lo que devuelve el
    minimizador.** `crm_ia_redactar` enmascara RUT, correos, teléfonos y los nombres que se le
    declaren: los de la ficha de la causa **y los que están dentro del expediente** (partes,
    representantes, testigos), que el CRM no conoce. Después de minimizar, **lee el resultado**: si
    ves un nombre propio a la vista, agrégalo a `terminos` y vuelve a minimizar. Un `redactado:
    true` sobre un texto que todavía muestra un nombre es un registro falso.
14. **Verifica la regla de días hábiles antes de confiar en la fecha, y nunca la escondas.**
    El cálculo devuelve `regla_dias_habiles` (por defecto: lunes a sábado, con domingos y feriados
    suspendidos). Si el criterio que necesita la causa es otro —el procedimiento administrativo de
    la Ley 19.880 no cuenta sábados—, pásalo en `sabado_habil: false` y **dilo en el informe**: la
    diferencia es de días y puede cambiar el vencimiento. Elegir el criterio es del abogado; tú
    tienes que mostrarle que existe la elección y cuál aplicaste.
15. **No uses una causa real para probar herramientas.** Si quieres comprobar que una herramienta
    funciona, no crees registros de prueba en la causa del cliente: un duplicado que después hay
    que cancelar es trabajo y ruido en la agenda. Pregunta o usa `crm_plazo_calcular`, que no
    escribe nada.

## Pasos

1. **Extrae el texto del expediente.** `mcp__open_legal_chile__ocr_extract_pdf` (el parámetro es
   `pdf_path`) para PDFs escaneados o el lector normal si tiene capa de texto. Si no puedes
   leerlo, dilo en vez de suponer su contenido.
   - **Revisa `ok` y `length` de CADA página antes de seguir.** Una página con `ok: false` o
     `length: 0` no se leyó: pide el documento de nuevo (o una foto mejor) en vez de analizar un
     expediente incompleto.
   - **Fotos y escaneos de baja calidad** (boletas notariales, comprobantes, fotos de teléfono):
     Tesseract rinde 0 caracteres incluso rotando, y devolvía `ok: true` con 0 caracteres en
     versiones anteriores a la 1.5.6. Con el motor RapidOCR las mismas páginas se leen completas.
     Instálalo con `pip install "openlegal-chile[ocr]"` (o en el venv del harness:
     `uv pip install rapidocr-onnxruntime`); el motor lo prefiere solo
     (`available_engines` debe listarlo). Ortografía de nombres, RUT y montos sale bien de ahí.
   - Rotar la imagen NO arregla una foto: la búsqueda de inclinación 0/90/180/270 no rescata texto.
     Si ni con RapidOCR hay texto, el problema es la resolución de la foto, no la orientación.
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

- No inventar la fecha de notificación ni el Rol/RIT. Si el proveído dice «contado desde la
  notificación» y la cédula no está en el expediente, usa la fecha del proveído como ancla
  **provisional** y dilo tal cual.
- No crear plazos fatales «por si acaso»: un plazo fatal de más también hace daño.
- No crear un plazo nuevo encima de uno mal cargado, ni borrar el que estaba: se rectifica o se
  cancela, y siempre con motivo.
- No escribir un memo o informe que afirme el estado del CRM sin haberlo vuelto a leer en esa
  misma corrida.
- No descartar una respuesta de herramienta que contradiga tu relato («raro pero no crítico»):
  o la verificas, o la informas como contradicción.
- No dejar sin declarar un nombre propio que aparezca en el documento: si quedó a la vista del
  modelo, la minimización no se hizo.
- No probar herramientas creando registros en una causa real.
- No mandar el expediente completo si basta el texto minimizado.
- No usar terminología de common law (discovery, at-will, punitive damages): es derecho chileno.
- No cerrar la tarea sin dejar el resumen para la revisión del abogado.
