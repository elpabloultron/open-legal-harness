/**
 * Verificación del artefacto emitido: se corre después del build (`pnpm test`).
 *
 * Comprueba lo que el loader del navegador exige y lo que rompería sin avisar:
 * el envoltorio closure-factory, React como externo (bundle duplicado = dos
 * runtimes de React en la página) y que el id del manifiesto coincida con el del
 * patch del perfil.
 */
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const RAIZ = path.resolve(import.meta.dirname, '..');
const manifiesto = JSON.parse(await readFile(path.join(RAIZ, 'package.json'), 'utf8'));
const patch = await readFile(path.join(RAIZ, 'cordis.patch.yml'), 'utf8');
const cliente = await readFile(path.join(RAIZ, 'lib/client.js'), 'utf8');
const host = await readFile(path.join(RAIZ, 'lib/index.js'), 'utf8');

const fallos = [];
const exigir = (condicion, mensaje) => {
  if (!condicion) fallos.push(mensaje);
};

exigir(
  cliente.includes(`window.__ModuleLoader__.load({ id: "${manifiesto.name}"`),
  'lib/client.js no abre la fábrica con el id del paquete',
);
exigir(cliente.includes('factory: (require) =>'), 'falta la firma `factory: (require) =>`');
exigir(
  cliente.includes('return module.exports; } });'),
  'la fábrica no cierra devolviendo module.exports',
);
exigir(
  cliente.includes("require('react')") || cliente.includes('require("react")'),
  'React no quedó como externo: se duplicaría el runtime',
);
exigir(
  !cliente.includes('@deepseek-ai/dsh-client-ui-renderer/client'),
  'el bundle importó un paquete de plataforma que el loader no puede resolver',
);
exigir(
  cliente.includes('sidebar.panellist') && cliente.includes('CRM Jurídico'),
  'lib/client.js no registra la fila «CRM Jurídico» en sidebar.panellist',
);
exigir(host.includes('openlegal-crm'), 'la mitad host no declara su identidad esperada');
exigir(
  patch.includes(manifiesto.name),
  'cordis.patch.yml no inserta este paquete: el perfil no lo montaría',
);
exigir(manifiesto.dsh?.client?.platform === 'web', 'el manifiesto no declara la mitad navegador');
exigir(
  manifiesto.dsh?.bundle?.patch === './cordis.patch.yml',
  'el manifiesto no apunta al patch del bundle',
);

if (fallos.length > 0) {
  console.error('artefacto inválido:');
  for (const fallo of fallos) console.error(`  - ${fallo}`);
  process.exit(1);
}
console.log('artefacto verificado: envoltorio, externos, patch y manifiesto en orden');
