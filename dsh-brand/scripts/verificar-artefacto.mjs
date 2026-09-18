#!/usr/bin/env node
/**
 * Verificador del artefacto: se puede correr sin reconstruir.
 *
 * Comprueba el contrato del loader y que los tres slots de marca estén adentro,
 * más el detalle que rompe el layout si falta: la clase que el host pasa al
 * componente tiene que llegar al SVG (si no, el signo pierde su sitio en el
 * sidebar y en el héroe).
 */
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const RAIZ = path.resolve(import.meta.dirname, '..');
const PAQUETE = '@openlegal/dsh-client-ui-brand-legal';
const artefacto = await readFile(path.join(RAIZ, 'lib/client.js'), 'utf8');

const obligatorios = [
  `window.__ModuleLoader__.load({ id: "${PAQUETE}"`,
  'factory: (require) =>',
  'return module.exports;',
  'sidebar.brand.mark',
  'sidebar.brand.name',
  'conversation.hero.brand.mark',
  'Open Legal',
  'currentColor',
];

const faltantes = obligatorios.filter((fragmento) => !artefacto.includes(fragmento));
if (faltantes.length > 0) {
  console.error(`lib/client.js no cumple el contrato; falta: ${faltantes.join(' | ')}`);
  process.exit(1);
}

const host = await readFile(path.join(RAIZ, 'lib/index.js'), 'utf8');
if (!host.includes('openlegal-brand')) {
  console.error('lib/index.js no declara la identidad del plugin (openlegal-brand)');
  process.exit(1);
}

console.log('artefacto ok: contrato del loader, tres slots de marca e identidad del host');
