/**
 * Build del plugin de marca: emite los dos artefactos que el harness espera.
 *
 *   lib/index.js   -> mitad host (Cordis: `name` + `apply`)
 *   lib/client.js  -> mitad navegador: artefacto closure-factory que llama
 *                     window.__ModuleLoader__.load({ id, factory: (require) => … })
 *
 * Mismo envoltorio explícito de esbuild que el plugin del CRM (ver NOTICE.md):
 * sin el preset de tsdown, porque no hay CSS Modules ni gates de pureza que
 * justifiquen arrastrarlo. Todo lo que el bundle importa es externo (react y el
 * runtime de jsx), así que no hace falta árbol de dependencias para construir.
 */
import { build } from 'esbuild';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const PAQUETE = '@openlegal/dsh-client-ui-brand-legal';
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
  charset: 'utf8', // sin esto esbuild escapa los acentos del copy
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
  'sidebar.brand.mark',
  'sidebar.brand.name',
  'conversation.hero.brand.mark',
  'Open Legal',
];
const faltantes = obligatorios.filter((fragmento) => !artefacto.includes(fragmento));
if (faltantes.length > 0) {
  throw new Error(`lib/client.js no respeta el contrato del loader; falta: ${faltantes.join(' | ')}`);
}
if (!artefacto.includes("require('react/jsx-runtime')") && !artefacto.includes('require("react/jsx-runtime")')) {
  throw new Error('lib/client.js no dejó react/jsx-runtime como externo: el bundle duplicaría el runtime de React');
}
// Guarda contra el error de configuración de arriba: si React se colara dentro del
// bundle, el artefacto pesaría cientos de KB en vez de unos pocos.
if (Buffer.byteLength(artefacto, 'utf8') > 12_000) {
  throw new Error(
    `lib/client.js pesa ${Buffer.byteLength(artefacto, 'utf8')} bytes: algo se bundleó que debía ser externo`,
  );
}
console.log('build ok: lib/index.js + lib/client.js con los tres slots de marca verificados');
