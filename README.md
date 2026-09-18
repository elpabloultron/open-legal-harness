# ⚖️ Open Legal Harness — harness legal chileno con CRM

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

## MCP: el agente escribe en el CRM

`openlegal mcp` expone el CRM como servidor MCP (JSON-RPC 2.0 sobre stdio, mismo patrón
que `open-legal-chile`), con 14 herramientas: `crm_causa_buscar`, `crm_causa_leer`,
`crm_plazo_calcular`, `crm_plazo_crear`, `crm_plazo_listar`, `crm_plazo_cumplido`,
`crm_plazo_actualizar`, `crm_plazo_cancelar`, `crm_audiencia_crear`,
`crm_documento_registrar`, `crm_ia_estado`, `crm_ia_redactar`, `crm_ia_registrar` y
`crm_estudio`.

`crm_plazo_actualizar` y `crm_plazo_cancelar` nacieron de la primera prueba con un
expediente real: el agente detectó un plazo mal cargado y no tenía cómo corregirlo. No
borran la fila —dejan el motivo en la bitácora—, porque un plazo fatal rectificado tiene
que ser explicable después.

Con eso el flujo completo queda: **causa.pdf → análisis con las 64 herramientas de
open-legal-chile → plazos, audiencias y documentos escritos en el CRM**, todo desde el
chat, sin teclear comandos.

En el perfil del harness (`~/.dsh/profiles/legal/cordis.patch.yml`) van dos filas:
`crm` y `open_legal_chile`; las herramientas llegan al modelo como `mcp__crm__*` y
`mcp__open_legal_chile__*`.

## Entregar el expediente a la IA

El flujo está pensado para que análisis con IA sea **demostrable**: autorización
por causa con base de licitud, minimización del texto (RUT, correos y nombres a
marcadores que se quedan en el estudio) y registro de cada comunicación con hash,
proveedor, modelo, país y responsable.

```bash
openlegal ia proveedores                      # qué sabemos de cada proveedor
openlegal ia autorizar --causa 1 --titular "..."
openlegal ia redactar  --causa 1 --archivo demanda.txt
openlegal ia registrar --causa 1 --proveedor anthropic --modelo ... --redactado --archivo demanda_minimizada.txt
openlegal ia transferencias                   # bitácora
```

Detalle legal en [docs/ia_y_transferencia.md](docs/ia_y_transferencia.md).

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

## Usuarios, roles y entrada al panel

Roles: `socio` (dueño, ve todo incluida la rentabilidad), `administrador` (gestiona
usuarios y ve todo el estudio para operar el CRM, pero **no** toca honorarios ni
redacción), `abogado`, `paralegal`, `administrativo` (secretaría: factura y agenda) y
`cliente`. La regla dura: **sólo el socio y el administrador ven todas las causas del
estudio**; abogados y paralegales ven únicamente las causas donde están asignados.
Detalle en [docs/matriz_permisos.md](docs/matriz_permisos.md).

Dos formas de entrar al panel:

1. **Sesión de usuario** (oficina): correo y contraseña. Cada quien ve lo suyo y todo
   queda firmado en la bitácora.
2. **Token del panel** (abogado solo): la URL que imprime `openlegal serve` abre el
   panel como usuario responsable, sin login.

```bash
# las contraseñas se piden por teclado, nunca por línea de comandos ni por chat
openlegal --db "$DB" usuario clave --email admin@estudio.cl
openlegal --db "$DB" usuario desactivar --email luis@estudio.cl   # corta el acceso sin borrar historial
```

## Demostración local (SQLite, sin Docker)

```bash
bash scripts/demo_local.sh --servir   # estudio demo: admin, secretaría y dos abogados
```

## Protección de datos y modo local

Todo corre en la máquina del estudio: la base vive en el disco, el panel escucha sólo
en `127.0.0.1` y no hace ni una petición externa. El mapa completo de obligaciones, la
base de licitud para causas (art. 13 letra e) y —lo más importante— el punto donde la
IA sí puede sacar datos fuera del estudio están en
[docs/proteccion_datos.md](docs/proteccion_datos.md).

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
