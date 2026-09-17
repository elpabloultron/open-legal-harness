/**
 * Build del plugin: emite los dos artefactos que el harness espera.
 *
 *   lib/index.js   -> mitad host (Cordis: `name` + `apply`)
 *   lib/client.js  -> mitad navegador: artefacto closure-factory que llama
 *                     window.__ModuleLoader__.load({ id, factory: (require) => … })
 *                     y resuelve los módulos de plataforma por el `require`
 *                     inyectado (tabla de módulos del loader). El contrato está
 *                     copiado del preset de cliente de DeepSeek Harness
 *                     (packages/client/tsdown.client.ts, MIT) — ver NOTICE.md.
 *
 * No usamos el preset de tsdown a propósito: sin CSS Modules ni gates de pureza,
 * este envoltorio explícito de esbuild es todo lo que hace falta y no arrastra
 * dependencias de build.
 */
import { build } from 'esbuild';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const PAQUETE = '@openlegal/dsh-client-ui-crm';
const RAIZ = path.resolve(import.meta.dirname, '..');

/** Módulos que la shell comparte en la tabla congelada: siempre externos. */
const MODULOS_PLATAFORMA = [
  'react',
  'react/jsx-runtime',
  'react-dom',
  'react-dom/client',
  '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-store',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-ui-dockkit',
];

await mkdir(path.join(RAIZ, 'lib'), { recursive: true });

// ------------------------------------------------------------ mitad host (ESM)
await build({
  entryPoints: [path.join(RAIZ, 'src/index.ts')],
  outfile: path.join(RAIZ, 'lib/index.js'),
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  sourcemap: false,
  logLevel: 'info',
});

// -------------------------------------------------- mitad navegador (closure factory)
await build({
  entryPoints: [path.join(RAIZ, 'src/client/index.tsx')],
  outfile: path.join(RAIZ, 'lib/client.js'),
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  target: 'es2020',
  jsx: 'automatic',
  charset: 'utf8', // sin esto esbuild escapa los acentos del copy (CRM Jurídico -> CRM Jur\u00eddico)
  external: MODULOS_PLATAFORMA,
  sourcemap: true,
  logLevel: 'info',
  banner: {
    js: [
      `window.__ModuleLoader__.load({ id: ${JSON.stringify(PAQUETE)}, factory: (require) => {`,
      'var module = { exports: {} }; var exports = module.exports;',
    ].join('\n'),
  },
  footer: { js: 'return module.exports; } });' },
});

const artefacto = await readFile(path.join(RAIZ, 'lib/client.js'), 'utf8');
const obligatorios = [
  `window.__ModuleLoader__.load({ id: "${PAQUETE}"`,
  'factory: (require) =>',
  'return module.exports;',
  "sidebar.panellist",
  'CRM Jurídico',
];
const faltantes = obligatorios.filter((fragmento) => !artefacto.includes(fragmento));
if (faltantes.length > 0) {
  throw new Error(`lib/client.js no respeta el contrato del loader; falta: ${faltantes.join(' | ')}`);
}
if (!artefacto.includes("require('react')") && !artefacto.includes('require("react")')) {
  throw new Error('lib/client.js no dejó react como externo: el bundle duplicaría el runtime de React');
}
console.log('build ok: lib/index.js + lib/client.js con el contrato del loader verificado');
