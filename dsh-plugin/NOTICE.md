# Aviso de terceros

## Contrato del artefacto cliente (DeepSeek Harness)

`scripts/build.mjs` reproduce el envoltorio *closure-factory* que emite el preset
de build de cliente de DeepSeek Harness:

- Origen: https://github.com/deepseek-ai/deepseek-harness — `packages/client/tsdown.client.ts`
- Licencia: MIT (ver `LICENSE` del repositorio original)
- Qué se reutiliza: únicamente el contrato de salida observable —
  `window.__ModuleLoader__.load({ id, factory: (require) => … })`, formato CJS,
  externos = tabla de módulos de la plataforma (`react`, `react/jsx-runtime`,
  `react-dom`, `react-dom/client`, `@deepseek-ai/cordis`,
  `@deepseek-ai/dsh-client-store`, `@deepseek-ai/dsh-client-ui-slots`,
  `@deepseek-ai/dsh-client-ui-primitives`, `@deepseek-ai/dsh-client-ui-dockkit`),
  y la definición de `module`/`exports` dentro de la fábrica.
- Qué NO se reutiliza: el preset completo (CSS Modules con lightningcss, gates de
  pureza, wiring de watch). Este plugin no usa CSS Modules ni importa valores de
  otros plugins, así que el build se arma con esbuild.

## La plantilla de estructura de plugin

La forma del paquete (bloque `dsh` en `package.json`, `cordis.patch.yml`, mitad
host + mitad `./client`) sigue el patrón de los plugins externos de la comunidad,
en particular `zhu1090093659/dsh-web` (Apache-2.0), sin copiar su código.
