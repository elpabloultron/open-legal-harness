# Retención y borrado

Cuánto se conserva cada cosa, quién lo decide y qué hace el CRM cuando el plazo se cumple.

## La postura, primero

**El sistema no inventa plazos.** Trae dos sugerencias —con su anclaje y su motivo— y el
estudio declara los suyos. La decisión vive en la base, con su fecha y su motivo escritos,
para que dentro de tres años se pueda saber **con qué criterio** se anonimizó algo:

```
openlegal retencion --definir datos_de_persona --meses 60 \
  --motivo "criterio del estudio: 5 años desde el último movimiento"
```

**Sólo sabe anonimizar, y no borra lo que hay que conservar.** La contabilidad, la bitácora
y las pruebas de tratamiento de IA no se tocan: son justamente las que permiten responder
por el tratamiento ante el titular o ante la Agencia. El informe dice qué conserva y por
qué, fila por fila, en vez de cumplir un plazo borrando la prueba.

## Cómo se mide el tiempo

La tabla de causas **no tiene fecha de cierre**, así que el corte no se calcula con una
fecha que no existe: se calcula por **último movimiento** del expediente (el rastro más
reciente entre sus plazos, audiencias, documentos, su bitácora y su propia creación). Es,
además, como lo piensa un estudio: «este expediente no se toca desde…».

## Cómo se usa

```bash
openlegal retencion                       # la política vigente y qué está cumplido
openlegal retencion --aplicar             # qué haría (simulación, no escribe nada)
openlegal retencion --aplicar --escribir --motivo "retención cumplida"
```

La simulación es el modo por defecto, como en los derechos del titular: una operación que
reescribe el expediente no se dispara por un descuido de tipeo. Aplicar de verdad deja
entrada en la bitácora (`retencion.aplicar`) con el motivo, y cada persona anonimizada
queda registrada por el camino de `titulares.anonimizar`.

## Lo que el sistema NO puede hacer por ti

- **No detecta menciones indirectas.** La anonimización va por coincidencia del nombre
  (con una pasada sin acentos): iniciales, roles, «la señora del 4B» o el texto dentro de
  un PDF no los ve. La revisión humana de la lista es parte del procedimiento, no un
  detalle.
- **No borra los archivos.** Anonimiza el registro; los PDF y las imágenes viven en disco
  fuera de la base y hay que borrarlos ahí. El CRM los lista con su ruta, tamaño y hash
  justamente para poder hacerlo sin perder el rastro de qué había.
- **No decide el plazo.** Esa es la política del estudio, y queda firmada con su motivo.

## Lo que se conserva, y por qué

| Tabla | Motivo |
|---|---|
| `honorarios`, `gastos` | registro contable del estudio |
| `auditoria` | bitácora: prueba del tratamiento y del acceso |
| `transferencias_ia` | prueba de licitud de las comunicaciones a modelos |
| `autorizaciones_ia` | papel que respalda el tratamiento con IA |

Ninguna de esas tablas guarda el nombre de la persona anonimizada más allá de lo que ya
estaba en el detalle de la bitácora —que es precisamente lo que hay que poder mostrar—,
así que el plazo cumplido no obliga a borrarlas.
