# Matriz de permisos

Fuente en código: `src/openlegal/auth.py` → `PERMISOS`. Reglas efectivas:

- **Estudio**: nadie lee ni escribe fuera de su `estudio_id`. Una causa de otro
  estudio es invisible aunque se conozca su id (probado en `test_nucleo.py`).
- **Causa**: `.todas` = todo el estudio. Sin `.todas`, el usuario necesita fila
  vigente en `causa_equipo`.
- **Cliente**: rol de sólo lectura, nunca ve lo interno.

| Permiso | socio | abogado | paralegal | administrativo | cliente |
|---|:--:|:--:|:--:|:--:|:--:|
| Ver todas las causas del estudio | ✅ | — | — | ✅ | — |
| Ver causas asignadas | ✅ | ✅ | ✅ | ✅ | — |
| Ver su propia causa | ✅ | ✅ | ✅ | ✅ | ✅ |
| Crear causa | ✅ | ✅ | — | — | — |
| Editar causa | ✅ | ✅ | — | — | — |
| Asignar abogados a la causa | ✅ | — | — | — | — |
| Leer clientes | ✅ | ✅ | ✅ | ✅ | — |
| Crear/editar clientes | ✅ | ✅ | — | ✅ | — |
| Leer plazos | ✅ | ✅ | ✅ | ✅ | ✅ |
| Crear plazos | ✅ | ✅ | ✅ | — | — |
| Cerrar plazos (marcar cumplido) | ✅ | ✅ | — | — | — |
| Ver audiencias / crear audiencias | ✅ | ✅ | ✅ | ✅ | sólo ver |
| Leer documentos | ✅ | ✅ | ✅ | — | sólo `visibilidad=cliente` |
| Subir documentos | ✅ | ✅ | ✅ | — | — |
| Publicar documento al cliente | ✅ | ✅ | — | — | — |
| Leer honorarios | ✅ | ✅ | — | ✅ | — |
| Editar honorarios | ✅ | — | — | ✅ | — |
| Leer gastos de tramitación | ✅ | ✅ | ✅ | ✅ | — |
| Editar gastos | ✅ | — | — | ✅ | — |
| Gestionar usuarios | ✅ | — | — | — | — |
| Ver bitácora de auditoría | ✅ | — | — | — | — |
| Ver panel/KPIs del estudio | ✅ | ✅ | — | ✅ | — |

## Por qué estos cortes

- **Paralegal no cierra plazos.** Cargar y preparar sí; dar por cumplido un plazo
  fatal es decisión del abogado responsable.
- **Administrativo ve todo pero no redacta ni ve documentos.** Necesita leer
  causas para facturar y cobrar; no necesita el expediente para eso.
- **Abogado no asigna ni ve honorarios ajenos.** La rentabilidad del estudio es
  información de socios; el abogado ve el honorario de sus causas.
- **Cliente sin auditoría, honorarios ni documentos internos.**

Todo intento denegado queda registrado (`permiso.denegado` en `auditoria`), lo que
permite detectar accesos indebidos o configuración mal puesta.

> Ajustar esta matriz es cambiar `PERMISOS` y correr las pruebas: cada fila de la
> tabla tiene al menos una prueba asociada.
