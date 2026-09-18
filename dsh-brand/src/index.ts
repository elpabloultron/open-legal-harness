/**
 * Mitad host del plugin de marca: corre en el proceso del harness.
 *
 * No aporta capacidades al modelo. Existe porque el manifiesto declara un plugin
 * de doble cara: la mitad navegador (lib/client.js) dibuja la balanza y el nombre
 * del producto, y esta mitad es el ancla que el perfil carga en el host.
 *
 * Sin importar tipos de `@deepseek-ai/*` a propósito: esos paquetes no son
 * instalables fuera del monorepo del harness (su árbol apunta a paquetes internos
 * sin publicar). El contrato se declara aquí, estructural.
 *
 * @module @openlegal/dsh-client-ui-brand-legal
 */

/** Identidad del plugin en el roster del perfil (debe calzar con el patch). */
export const name = 'openlegal-brand';

/** Servicios que el host debe tener listos; ninguno propio. */
export const inject: string[] = [];

/** Lo único que usamos del contexto Cordis del host. */
export interface ContextoHost {
  logger?: { info?: (mensaje: string) => void };
}

/**
 * Cuerpo del plugin en el host.
 * @param ctx contexto Cordis del perfil.
 */
export function apply(ctx: ContextoHost): void {
  // La marca no tiene servicio detrás: son tres slots `single` de la interfaz.
  // Si algo no calza, se ve en el sidebar y en el héroe, no aquí.
  ctx.logger?.info?.('[openlegal-brand] marca «Open Legal Harness» montada (sidebar + héroe)');
}
