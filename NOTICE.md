# Aviso de terceros y licencia

**Open Legal Harness** — harness legal chileno con CRM, para abogados en Chile.
Copyright 2026 Pablo Ultrón. Licencia Apache-2.0 (ver `LICENSE`).

## Piezas de terceros

| Pieza | Origen | Licencia | Dónde |
|---|---|---|---|
| Contrato del cargador de plugins del navegador (closure-factory `window.__ModuleLoader__.load`) | DeepSeek Harness (`packages/client/tsdown.client.ts`) | MIT | `dsh-plugin/scripts/build.mjs`, `dsh-plugin/NOTICE.md`, `dsh-brand/scripts/build.mjs` |
| DeepSeek Harness (dsh) | `deepseek-ai/deepseek-harness` | MIT | dependencia de ejecución, no se copia código |
| esbuild | evanw/esbuild | MIT | herramienta de construcción de los plugins |
| `open-legal-chile` | `elpabloultron/open-legal-chile` | Apache-2.0 | servidor MCP que se monta al lado, no se copia código |

Los dos plugins de `dsh` (`dsh-plugin/` y `dsh-brand/`) son de este repositorio y se
construyen con esbuild contra el contrato de cargador que DeepSeek Harness publica; el
detalle de qué se copió y bajo qué licencia está en `dsh-plugin/NOTICE.md`.

## Corpus legal chileno

Las fuentes que el sistema consulta (BCN, PJUD, DT, CGR, SII, CMF, TDLC) se consultan
en línea, en sus propios sitios, y no se redistribuyen aquí. El código que las consulta
vive en `open-legal-chile`; este repositorio es el CRM y su interfaz.
