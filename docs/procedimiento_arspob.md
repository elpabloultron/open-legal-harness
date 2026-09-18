# Procedimiento para solicitudes de los titulares (ARSPOB)

> Qué hace el estudio cuando un cliente, una contraparte, un testigo o un trabajador pide acceder a
> sus datos, corregirlos, borrarlos, oponerse, o llevárselos.
>
> Base legal: **artículo 11 de la Ley 19.628 en su texto reformado por la Ley 21.719** (verificado
> contra el XML oficial de la BCN, versión de la ley 05-02-2026). El plazo, la prórroga, el deber de
> acusar recibo, de responder por escrito, de guardar respaldos y de fundar un rechazo son los que
> esa norma fija, no una política interna.

## 1. Canal habilitado

| Vía | Dato |
|---|---|
| Correo electrónico exclusivo para derechos | [CORREO ARSPOB] |
| Formulario de contacto del sitio | [URL] |
| Domicilio postal | [DIRECCIÓN DEL ESTUDIO] |
| Responsable interno de tramitarlas | [NOMBRE Y CARGO] |
| Suplente | [NOMBRE Y CARGO] |

El canal debe estar publicado en el sitio del estudio y en el aviso de privacidad (art. 14 ter letra
c). Un correo recibido en cualquier otra casilla del estudio **se reenvía el mismo día** a la casilla
ARSPOB: el plazo legal corre desde el ingreso de la solicitud, no desde que alguien la lea.

## 2. Qué debe contener la solicitud (art. 11)

No se puede exigir más de lo que la ley pide:

a) individualización del titular y de su representante o mandatario, con autenticación de identidad;
b) un domicilio o correo donde comunicar la respuesta;
c) identificación de los datos o del tratamiento respecto del cual se ejerce el derecho;
d) en rectificación, las modificaciones precisas y sus antecedentes; en supresión, la causal
   invocada; en oposición, la causal y —si es la del art. 8 letra a)— una fundamentación breve.

**En el derecho de acceso basta con la individualización del titular.** Si la solicitud viene
incompleta, se le indica al titular qué falta **en el mismo acto** del acuse de recibo; la
incompletitud no sirve como excusa para dejar correr el plazo.

## 3. Plazos

| Hito | Plazo |
|---|---|
| Acuse de recibo | Inmediato (el mismo día del ingreso) |
| Respuesta por escrito | **30 días corridos** desde el ingreso |
| Prórroga | **Una sola vez, hasta 30 días corridos más**, comunicada antes del vencimiento |
| Reclamo del titular ante la Agencia | 30 días hábiles si se rechaza; directo si no hay respuesta |

La respuesta se envía **por escrito** al domicilio o correo que el titular fijó, y el estudio
**guarda los respaldos** que permitan demostrar la remisión, su fecha y el contenido íntegro de la
respuesta. Eso es un deber legal expreso: se archiva en la carpeta de la solicitud.

## 4. Cómo se tramita, paso a paso

1. **Registro de ingreso.** Se abre una carpeta `ARSPOB-AAAA-NNN` con la solicitud original, el
   acuse de recibo y la fecha y hora de ingreso. (Fecha y hora determinan el plazo: anotarlas
   siempre.)
2. **Identificación del titular** y del rol con que aparece en el sistema: cliente, contraparte,
   testigo, trabajador. Se consulta en el CRM por nombre y RUT.
3. **Acuse de recibo** por el mismo canal, con la fecha de vencimiento de los 30 días.
4. **Búsqueda y recopilación.** Se reúne todo lo que el sistema tenga del titular: causas donde
   figura, documentos asociados, plazos y audiencias, y filas de auditoría que lo mencionen. La
   bitácora del CRM permite reconstruir quién vio o modificó qué.
5. **Evaluación del derecho invocado**, distinguiendo:
   - **Acceso:** se entrega copia de los datos y del contexto de su tratamiento.
   - **Rectificación:** se corrigen los datos inexactos y se deja registro del cambio.
   - **Supresión:** procede cuando el dato no puede conservarse. **Ojo:** si el dato es necesario
     para la defensa de un derecho en un procedimiento en curso o para cumplir un deber legal de
     conservación, la supresión se rechaza **fundadamente** y se ofrece, si es posible, el bloqueo o
     la restricción de uso.
   - **Oposición:** se evalúa la causal invocada y se suspende el tratamiento mientras se resuelve si
     hay riesgo para el titular.
   - **Portabilidad:** se entrega en un formato estructurado de uso común (JSON o CSV), no en PDF
     maquetado.
   - **Bloqueo:** se marca el dato como restringido, sin borrarlo, cuando hay controversia.
6. **Respuesta por escrito**, firmada por el responsable, con el contenido íntegro archivado.
7. **Denegación total o parcial:** se funda indicando la causa y los antecedentes que la justifican,
   y **se le dice al titular que puede reclamar ante la Agencia dentro de treinta días hábiles**.
8. **Cierre y respaldo**: se guarda todo en la carpeta `ARSPOB-AAAA-NNN` y se deja constancia en la
   bitácora del CRM.

## 5. Cómo se conecta con el CRM

| Necesidad del procedimiento | Con qué se hace hoy |
|---|---|
| Ubicar al titular y sus causas | Búsqueda en el CRM (causa, rol, contraparte; RUT) |
| Ver quién accedió a esos datos | Tabla `auditoria` (incluye los accesos denegados) |
| Rectificar un dato | Edición en el CRM, que deja fila de auditoría |
| Suprimir o anonimizar | [PENDIENTE: falta la operación de borrado/anonimización por titular — ver §6] |
| Portabilidad | [PENDIENTE: falta exportar los datos de un titular en JSON o CSV] |
| Demostrar la respuesta | Carpeta `ARSPOB-AAAA-NNN` con la solicitud, el acuse y la respuesta íntegra |

**Nota honesta:** el CRM permite **encontrar, ver y rectificar** datos y demuestra quién hizo qué,
que cubre la mayor parte del procedimiento. Lo que todavía **no** tiene es una operación de
**borrado o anonimización por titular** ni una **exportación de portabilidad**: hoy eso se hace a
mano sobre la base, y por eso figura como pendiente en `docs/proteccion_datos.md` §4. Mientras no
exista esa operación, la supresión se ejecuta por el responsable con respaldo del administrador del
sistema, dejando registro.

## 6. Lo que hay que escribir antes de recibir datos de clientes reales

- [ ] Operación de **supresión / anonimización por titular** en el CRM, con prueba automatizada.
- [ ] **Exportación de portabilidad** (JSON o CSV) de todo lo que el sistema tenga de un titular.
- [ ] Plantilla de **respuesta tipo** para cada derecho, con el texto de los plazos y de la vía de
      reclamo ante la Agencia ya redactado.
- [ ] Registro de solicitudes (una tabla o carpeta única) que permita responder «cuántas recibimos,
      en qué plazo y con qué resultado».
