# ⚖️ Open Legal CRM — Harness legal chileno con CRM

Un arnés agéntico con CRM para el ejercicio de la abogacía en Chile. Un mismo
núcleo sirve a dos realidades:

| Modo | Para quién | Cómo corre |
|---|---|---|
| **solo** | abogado que trabaja solo | SQLite local en `~/.openlegal/openlegal.db`, sin servidor, sin configuración |
| **oficina** | estudio con varios abogados | PostgreSQL + usuarios, roles, asignación de causas y bitácora de auditoría |

> Estado: **v0.1.0 — núcleo de dominio funcionando** (17/17 pruebas). Sin interfaz
> gráfica todavía; hoy se opera por CLI. Ver Hoja de Ruta.

## Instalación

```bash
# núcleo, sólo librería estándar de Python 3.10+
pip install -e .

# driver para el modo oficina (PostgreSQL)
pip install -e '.[postgres]'

# base de demostración lista en 3 comandos
openlegal init
openlegal estudio crear --nombre "Mi Estudio" --modo solo
openlegal usuario crear --nombre "Ana Pérez" --email ana@estudio.cl --rol socio
```

## Uso

```bash
openlegal cliente crear --nombre "Constructora Andes SpA" --rut 76.543.210-K --tipo juridica
openlegal causa crear --caratula "Pérez con Andes SpA" --cliente 1 \
  --rol-rit C-1234-2026 --tribunal "1° Juzgado del Trabajo de Santiago" --materia laboral
openlegal causa asignar --causa 1 --a-usuario 2 --rol-en-causa responsable
openlegal plazo crear --causa 1 --descripcion "Contestar demanda" --dias 8 --notificacion 2026-09-17
openlegal vencimientos --dias 30
openlegal panel
openlegal auditoria
```

Base de datos explícita (por ejemplo, un Postgres de oficina):

```bash
openlegal --db postgresql://legal:clave@localhost:5432/estudio init
```

## Cómputo de plazos (Art. 66 CPC)

Los plazos de días son **de días hábiles**: se cuentan desde el día siguiente a la
notificación y se suspenden los domingos y feriados. El sábado se computa como
hábil y esto es configurable (`sabado_habil`).

Los feriados **no están adivinados en el código**: viven en
`src/openlegal/feriados_cl.json` y cada año aparece marcado como
`_pendiente_validacion`. **Hay que validarlos contra el calendario oficial
(BCN / Dirección del Trabajo) antes de usarlos en juicio**: un feriado mal
cargado cambia un plazo fatal.

```
$ openlegal plazo simular --notificacion 2026-09-17 --dias 8
AVISO: los feriados de 2026 estan marcados como PENDIENTES DE VALIDACION
VENCE: 2026-09-29
```

## Roles

`socio`, `abogado`, `paralegal`, `administrativo`, `cliente`. La regla dura:
**sólo el socio ve todas las causas del estudio**; abogados y paralegales ven
únicamente las causas donde están asignados; el administrativo ve todo para
facturar pero no redacta ni accede a documentos; el cliente ve su causa y sólo lo
marcado como visible. Detalle en [docs/matriz_permisos.md](docs/matriz_permisos.md).

## Privacidad y secreto profesional

Diseño *local-first*: en modo solo ningún dato sale del computador del abogado.
El modelo aísla todo por estudio (`estudio_id`), registra cada acción en
`auditoria` y separa lo interno de lo publicable al cliente. Alineado con la Ley
19.628 sobre protección de la vida privada, reformada por la Ley 21.719.

## Qué falta (hoja de ruta)

- **F1** — autenticación con sesiones en la API, Postgres de oficina con migraciones versionadas, empaquetado y tests de integración.
- **F2** — agente y servidor MCP `crm_*` para que los agentes (vigilante de proveídos, laboral, litigios) lean y escriban el CRM con trazabilidad.
- **F3** — interfaz: panel del socio, agenda compartida y vista del cliente.
- **F4** — honorarios: boletas de honorarios, retención SII según la tabla vigente de la Ley 21.133 (a validar), pactos y gastos de tramitación reembolsables.
- **F5** — integración con PJUD/OJV y las herramientas de `open-legal-chile`.

## Pruebas

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Licencia

Apache 2.0.

> ⚖️ **Compuerta de Revisión Jurídica:** este software organiza información y
> calcula plazos, pero no sustituye el juicio de un abogado. Todo escrito debe ser
> validado por un abogado habilitado antes de su firma e ingreso en la Oficina
> Judicial Virtual (OJV) o notificación a contrapartes.
