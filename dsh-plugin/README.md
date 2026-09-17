# @openlegal/dsh-client-ui-crm

Renglón **«CRM Jurídico»** en el sidebar izquierdo de DeepSeek Harness, justo bajo
*New session*, con su panel en el área principal. No se toca ni se forkea el
harness: se usan dos asientos públicos del cliente.

| Asiento | Cardinalidad | Qué ponemos |
|---|---|---|
| `sidebar.panellist` | `list` | la fila: `id: openlegal-crm`, `order: 50`, `label: CRM Jurídico` |
| `main` | `keyed` | el panel, direccionado por el mismo id |

El panel es el CRM local (`openlegal serve`, `127.0.0.1:8899`) dentro de un
iframe. La dirección y el token se configuran desde el propio panel y quedan en el
`localStorage` del navegador: el token no viaja al modelo ni vive en el código.

## Montaje

```bash
# 1. el CRM, que imprime su URL con token
openlegal --db postgresql://legal:CAMBIAR_CLAVE@localhost:55432/estudio serve --port 8899

# 2. el plugin, en un perfil propio del harness
dsh --profile legal --from-default-profile web
dsh --profile legal add link:"$PWD/dsh-plugin"
dsh --profile legal web --port 8799
```

En el perfil, la fila del patch queda así (lo hace `dsh plugin add`, o a mano en
`~/.dsh/profiles/legal/cordis.patch.yml`):

```yaml
- insert:
    - id: ui-openlegal-crm
      name: '@openlegal/dsh-client-ui-crm'
```

## Build

```bash
pnpm install
pnpm build     # lib/index.js (host) + lib/client.js (navegador)
pnpm test      # verifica el contrato del loader en el artefacto
```

`lib/client.js` es un artefacto *closure-factory*: llama
`window.__ModuleLoader__.load({ id, factory: (require) => {…} })` y resuelve React
y los módulos de plataforma por el `require` inyectado, nunca por globals. El
contrato está tomado del preset de cliente de DeepSeek Harness
(`packages/client/tsdown.client.ts`, MIT) — ver `NOTICE.md`. Sin CSS Modules ni
gates de pureza no hace falta ese preset completo: `scripts/build.mjs` arma el
envoltorio con esbuild y **verifica el artefacto al construir**.

## Herramientas para la IA

Este plugin solo dibuja. Las capacidades del agente (crear plazos, calcular Art. 66
CPC, consultar BCN) llegan por MCP: `openlegal mcp` y el `mcp_server.py` de
open-legal-chile, declarados en la capa `cordis.patch.yml` del perfil.
