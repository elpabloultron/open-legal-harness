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

Base de datos explícita (por ejemplo, el Postgres de oficina del
`docker-compose.yml`, que por defecto publica el puerto **55432** para no chocar
con un 5432 ya ocupado):

```bash
docker compose up -d
openlegal --db postgresql://legal:CAMBIAR_CLAVE@localhost:55432/estudio init
```

## Panel web (el CRM que va en el sidebar derecho del harness)

```bash
openlegal serve --port 8899                      # imprime la URL con token
openlegal --db postgresql://legal:CAMBIAR_CLAVE@localhost:55432/estudio serve --port 8899
```

Escucha solo en `127.0.0.1` y exige token (401 sin él), igual que el harness. Pestañas:
**Plazos** (con días restantes y marca de fatal), **Causas** (equipo y próximo vencimiento),
**Agenda**, **Panel** del socio y **+ Plazo**, que calcula el vencimiento por Art. 66 CPC y
muestra el detalle día a día antes de guardar.

## Integración con DeepSeek Harness (`dsh`)

Base: [`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness) (MIT,
CLI `dsh`, «Everything is a Plugin»). No se hace fork: se instala el harness oficial y se
enchufa el CRM por sus puntos de extensión públicos.

| Paso | Estado |
|---|---|
| 1. `dsh` instalado y sirviendo la UI | ✅ verificado (`npm install -g --allow-scripts=... @deepseek-ai/dsh`, `dsh web --port 8799`) |
| 2. CRM local servido por `openlegal serve` | ✅ verificado (25/25 pruebas, datos reales en SQLite y PostgreSQL) |
| 3. Fila «CRM Jurídico» bajo *New session* + su panel | ✅ verificado en la GUI real (ver abajo) |
| 4. MCP: `crm_*` propio + los 64 tools de open-legal-chile | pendiente |
| 5. Perfil `legal` con todo montado y patch layer | ✅ el perfil monta el plugin; falta sumarle los MCP |

### El plugin del sidebar (`dsh-plugin/`)

Paquete `@openlegal/dsh-client-ui-crm`: fila con ícono en `sidebar.panellist` y panel
propio en el asiento `main`, direccionados por el mismo id. Se monta sin tocar el
harness:

```bash
dsh --profile legal --from-default-profile web
dsh plugin --profile legal add link:"$PWD/dsh-plugin"
dsh --profile legal --no-open --port 8801     # ojo: `web` no acepta --profile
```

Comprobado en un navegador real contra el perfil `legal`: el sidebar queda

```
New Session
CRM Jurídico      ← nuestra fila
Workspaces
No sessions yet
Settings
```

y al pulsarla el área principal cambia al panel del CRM, que sondea el servicio
local y pide el token la primera vez (queda en el `localStorage` del navegador; el
token nunca viaja al modelo). El artefacto se construye con esbuild y el build
**falla si el envoltorio del loader, los externos de React o el patch del perfil no
están en orden** (`pnpm build && pnpm test`).

Las APIs del paso 3 y 4, leídas del código de `dsh` (no supuestas):

```ts
// tipo de pestaña en el sidebar derecho (banda `extension`, la de mayor prioridad)
ctx.sidebarRightTabs.register({ id, kind: 'crm', title, guide })
ctx.slots.register({ name: 'sidebar.right.pane.tab', key: id }, Body)
ctx.sidebarRight.openTab('crm', { params: { url } })
```

```yaml
# cordis.patch.yml del perfil: un servidor MCP stdio por cada fuente de herramientas
- id: mcp-crm
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    command: openlegal
    args: [mcp]
```

Los plugins cliente externos se instalan con `dsh plugin --profile <perfil> add <paquete>`, y
el perfil se crea desde la plantilla oficial:
`dsh --profile legal --from-default-profile web`.

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
