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
| 2. CRM local servido por `openlegal serve` | ✅ verificado (106 pruebas, datos reales en SQLite y PostgreSQL) |
| 3. Fila «CRM Jurídico» bajo *New session* + su panel | ✅ verificado en la GUI real (ver abajo) |
| 4. MCP: las 18 herramientas del CRM + las 69 de open-legal-chile | ✅ verificado por stdio y en vivo (el perfil arranca los dos servidores) |
| 5. Perfil `legal` con las cuatro piezas y la marca propia | ✅ verificado (`--dump-config` las muestra; lo monta `scripts/montar_en_dsh.sh`) |

### Montarlo en un paso (`scripts/montar_en_dsh.sh`)

```bash
scripts/montar_en_dsh.sh                        # perfil `legal`, puerto 8801
scripts/montar_en_dsh.sh --perfil otro --puerto 8890
scripts/montar_en_dsh.sh --sin-biblioteca       # sólo el CRM, sin open-legal-chile
```

Hace cuatro cosas y después las verifica con `--dump-config`:

1. comprueba que `dsh` esté instalado (y si no, dice el comando exacto, con el flag
   `--allow-scripts` que no es opcional);
2. deja el CRM instalado en `.venv` del repo, con su base y su esquema al día;
3. monta los dos plugins de interfaz (la fila del CRM y la marca);
4. escribe la capa de perfil con un servidor MCP por cada fuente de herramientas.

Es idempotente: si algo ya estaba, no lo duplica. Y es un script y no un archivo para
copiar por una razón concreta: **las rutas de los servidores MCP son absolutas y
distintas en cada equipo**, y el archivo de perfil no admite variables. El script las
resuelve en la máquina donde corre.

La primera vez puede tardar unos minutos: crear el perfil dispara un `pnpm install` de
la pila de plugins del harness.

### La marca (`dsh-brand/`)

Paquete `@openlegal/dsh-client-ui-brand-legal`: reemplaza el signo del harness (el pez)
y su nombre por **la balanza** y **«Open Legal Harness»**, en los tres lugares donde la
interfaz declara marca (el signo y el nombre del sidebar, y el signo grande del héroe).
Los tres slots son de tipo `single`, y un plugin externo tiene precedencia: por eso la
marca toma el relevo **sin desactivar** la oficial — si el plugin se desmonta, el pez
vuelve solo. El signo es SVG con `currentColor`, así que hereda el tema claro u oscuro;
el mismo trazo está suelto en `dsh-brand/assets/balanza.svg` para favicón, cartas y
documentos.

### Cómo se actualiza esto (y por qué la marca no se pierde)

Acá conviven tres cosas de dueños distintos, y eso explica el resto:

| | Quién | Cómo se actualiza |
|---|---|---|
| **DeepSeek Harness** (`dsh`) | DeepSeek, MIT | `npm install -g ... @deepseek-ai/dsh` y reiniciar el perfil |
| **Este repo** (CRM + los dos plugins) | este proyecto, Apache-2.0 | `git pull` |
| **open-legal-chile** (biblioteca jurídica) | este proyecto, Apache-2.0 | `pip install -U openlegal-chile` |

`dsh` **no se forkea**: se instala oficial y se engancha por los puntos de extensión que
publica. La consecuencia práctica es que **las actualizaciones del harness principal
siguen llegando y la marca propia no se pierde**: los plugins no dependen de cómo es
`dsh` por dentro, sino de los *slots* que declara; al actualizar, el harness mejora por
debajo y la balanza sigue arriba. Los dos plugins declaran su límite de compatibilidad
(`dsh >=0.1.5-rc.1`) para que un cambio de contrato se note en el acto, y el build falla
si el artefacto no cumple el contrato del cargador.

El único riesgo real: `dsh` está en *release candidate* (0.1.5-rc.2), así que esos puntos
pueden moverse. Si una versión futura renombra o elimina un slot, el signo desaparece y
vuelve el pez; se detecta en un minuto (`node dsh-brand/scripts/verificar-artefacto.mjs`)
y el arreglo es en nuestro plugin, nunca en un fork.

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

## Avisos por correo y SMS

El CRM avisa sin quedarse esperando a nadie: los avisos **se encolan** en la base del
estudio y salen después, así un servidor de correo caído no deja el panel colgado ni pierde
el aviso.

| Aviso | Cuándo sale |
|---|---|
| **Asignación** | En el momento en que a alguien le asignan un plazo o una audiencia |
| **Recordatorio** | Todos los días, mientras el plazo siga pendiente y esté dentro de la ventana de días (3 por defecto) |

Un aviso no se repite el mismo día (cada uno lleva su huella, única en la base) y uno que
falla se reintenta con su error a la vista en vez de perderse.

```bash
openlegal notificar --config                      # qué canal está listo y cuál falta (sin mostrar claves)
openlegal notificar --generar --enviar            # lo que conviene correr cada 10 minutos
openlegal notificar --estado                      # pendientes, enviados, fallidos y por qué
openlegal usuario editar --email ana@estudio.cl --telefono +56912345678   # para el SMS
```

Correo por SMTP (con STARTTLS) y SMS por Twilio, por un webhook propio o en modo de prueba
sin salir a la red. Las credenciales van en `~/.openlegal/notificaciones.json` con permisos
600 — **nunca** en el chat ni en el repositorio. Configuración, pruebas y detalles en
[docs/notificaciones.md](docs/notificaciones.md); el circuito completo se puede probar de
punta a punta con `python3 scripts/e2e_avisos.py` (levanta un servidor de correo de prueba).

**No le avisa al cliente**: los avisos son internos del estudio. Comunicarle a un cliente que
su plazo vence es un acto profesional responsable, y no se automatiza por accidente.

## El CRM en la oficina

El CRM se instala **en un equipo del estudio** y los demás entran por el navegador: no hay que
repartir la base ni sincronizar archivos. Un comando deja el panel, los avisos y el respaldo
nocturno andando como servicios:

```bash
bash scripts/instalar_oficina.sh --db "sqlite://$HOME/.openlegal/estudio.db"
```

Guía completa —direcciones que se reparten, seguridad antes de que lo use el equipo, respaldos
con cifrado y problemas frecuentes— en [docs/oficina.md](docs/oficina.md).

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
