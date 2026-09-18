# Protocolo ante vulneraciones de seguridad de datos

> Qué hace el estudio cuando sospecha o confirma que datos personales se filtraron, se perdieron, se
> alteraron o quedaron accesibles sin autorización.
>
> Base legal: **artículo 14 sexies de la Ley 19.628 en su texto reformado por la Ley 21.719**
> (verificado contra el XML oficial de la BCN, versión de la ley 05-02-2026). Dos precisiones que
> importan: **no existe un plazo de 72 horas** (las 72 horas son del reglamento europeo); la ley
> exige reportar «por los medios más expeditos posibles y **sin dilaciones indebidas**». Y la
> notificación a los titulares **no es siempre**: procede cuando la vulneración afecta datos
> sensibles, datos de niños y niñas menores de catorce años, o datos de obligaciones de carácter
> económico, financiero, bancario o comercial.

## 1. Qué se considera una vulneración reportable

Destrucción, filtración, pérdida o alteración —accidental o ilícita— de datos personales, o
comunicación o acceso no autorizados a ellos, **cuando exista un riesgo razonable para los derechos
y libertades de los titulares**. Ejemplos concretos en un estudio:

- robo o pérdida de un notebook, un disco o un respaldo;
- acceso de un tercero no autorizado a la base o al panel del CRM;
- envío de un expediente a la persona equivocada (correo mal dirigido);
- envío de datos de un cliente a un proveedor o a un modelo de lenguaje sin contrato de encargado;
- alteración o borrado accidental de documentos de una causa;
- credenciales comprometidas de un usuario del sistema.

## 2. Etapas

**1. Detección y aviso interno (de inmediato).** Cualquiera que detecte el hecho lo comunica el
mismo día al responsable de datos [NOMBRE] y al administrador del sistema [NOMBRE]. No se
«investiga primero y se avisa después».

**2. Contención (primeras horas).** Se corta la causa de la filtración: revocar sesiones y
credenciales, aislar el equipo comprometido, retirar el documento publicado, avisar al proveedor
involucrado. Se anota la hora exacta de cada medida.

**3. Evaluación del riesgo.** Se responde por escrito:
- qué datos exactamente (¿hay datos **sensibles**? ¿de **menores de 14**? ¿de **obligaciones
  económicas, financieras, bancarias o comerciales**?);
- cuántos titulares, aproximadamente;
- qué consecuencias concretas puede sufrir una persona (fraude, suplantación, daño reputacional,
  discriminación);
- si el dato estaba cifrado o no.

**Consecuencia de la evaluación:** si hay riesgo razonable → se reporta. Si además caen datos
sensibles, de menores de 14 o económicos → **también se notifica a los titulares**.

**4. Reporte a la Agencia (sin dilaciones indebidas).** Se envía al canal que habilite la Agencia de
Protección de Datos Personales, dejando copia íntegra. El reporte describe, como mínimo:
naturaleza de la vulneración, sus efectos, las categorías de datos, el número aproximado de titulares
afectados y las medidas adoptadas para gestionarla y precaver incidentes futuros.

**5. Notificación a los titulares (cuando corresponde).** Se hace **a cada titular afectado**, en
lenguaje claro y sencillo, singularizando los datos afectados, las posibles consecuencias y las
medidas de solución o resguardo. **Si no es posible llegar a cada uno**, la ley permite hacerlo con
un **aviso difundido en un medio de comunicación social masivo y de alcance nacional**.

**6. Registro interno (obligatorio).** Se asienta la comunicación en un registro que conserve los
cinco elementos que la ley pide: naturaleza, efectos, categorías de datos, número aproximado de
titulares afectados y medidas adoptadas. Ese registro es lo que se muestra si la Agencia pregunta.

**7. Cierre y prevención.** En [•] días se deja escrito qué falló y qué se cambió (contraseñas,
permisos, respaldos, cifrado, contratos con proveedores). Este paso es el que evita la reincidencia,
que es lo que la ley castiga con más dureza.

## 3. Formulario de registro (plantilla)

```
Registro de vulneración  N° ____          Fecha y hora de detección: ____/____/______  __:__
Detectada por: ____________________       Fecha y hora del reporte a la Agencia: ___________
Naturaleza:  [ ] acceso no autorizado  [ ] filtración  [ ] pérdida  [ ] alteración  [ ] destrucción
Efectos: ______________________________________________________________________________
Categorías de datos comprometidos: _____________________________________________________
¿Incluye datos sensibles?  [ ] sí  [ ] no        ¿Datos de menores de 14?  [ ] sí  [ ] no
¿Datos económicos, financieros, bancarios o comerciales?  [ ] sí  [ ] no
Número aproximado de titulares afectados: ______
Medidas de contención (hora y responsable): ____________________________________________
Medidas de solución o resguardo informadas a los titulares: ____________________________
¿Se notificó a los titulares?  [ ] sí, individualmente  [ ] sí, por aviso en medio nacional  [ ] no aplica
¿Se reportó a la Agencia?  [ ] sí, fecha ______  [ ] no, fundamento: ____________________
Medidas para precaver incidentes futuros: ______________________________________________
Responsable del cierre: ____________________   Fecha de cierre: ____/____/______
```

## 4. Lo que hay que definir antes de que ocurra

- [ ] **Quién decide** que existe una vulneración reportable (el responsable de datos; suplente en
      caso de ausencia).
- [ ] **Cómo se detecta**: hoy el CRM deja bitácora de cada acción y de los accesos denegados, lo
      que permite reconstruir qué se vio y cuándo. Falta [PENDIENTE] una **alerta de accesos
      anómalos** (muchos intentos fallidos, consultas masivas por un mismo usuario).
- [ ] **Dónde vive el registro** de vulneraciones (un archivo único, con respaldo cifrado).
- [ ] **A quién se avisa fuera del estudio** si el hecho afecta a un cliente (¿se le avisa siempre,
      aunque el reporte a la Agencia no lo exija? El estudio debe decidirlo por escrito).
- [ ] **Prueba de restauración de respaldos**: sin respaldo probado, una pérdida de datos no se
      repara, solo se declara.
