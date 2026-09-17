/**
 * Mitad host del plugin: corre en el proceso del harness.
 *
 * No aporta herramientas al modelo — esas llegan por MCP (`openlegal mcp`), que
 * es donde el harness espera las capacidades del agente. Este lado existe porque
 * el manifiesto declara una fila de plugin de doble cara: la mitad navegador
 * (lib/client.js) dibuja el renglón y el panel, y esta mitad es el ancla que el
 * perfil carga en el host.
 *
 * Sin importar tipos de `@deepseek-ai/*` a propósito: sus paquetes no son
 * instalables fuera del monorepo del harness (el árbol de dependencias apunta a
 * paquetes internos sin publicar). El contrato se declara aquí, estructural.
 *
 * @module @openlegal/dsh-client-ui-crm
 */

/** Identidad del plugin en el roster del perfil (debe calzar con el patch). */
export const name = 'openlegal-crm';

/** Ruta del panel por defecto: el servicio local del CRM. */
export const URL_POR_DEFECTO = 'http://127.0.0.1:8899';

/** Servicios que el host debe tener listos; ninguno propio por ahora. */
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
  // El CRM vive como servicio local (`openlegal serve`). Si no está arriba, la
  // mitad navegador lo dice en el panel; aquí solo dejamos constancia.
  ctx.logger?.info?.(
    `[openlegal-crm] panel listo. Levanta el CRM con: openlegal serve --port 8899 (${URL_POR_DEFECTO})`,
  );
}
