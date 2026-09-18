/**
 * Mitad navegador del plugin de marca: la balanza y el nombre del producto.
 *
 * Ocupa los tres slots de marca que declara la interfaz, los tres de tipo
 * `kind: single` (un solo registro gana, y el externo tiene precedencia):
 *
 *   sidebar.brand.mark            (24 px) el signo junto al nombre del sidebar
 *   sidebar.brand.name                    el nombre del producto
 *   conversation.hero.brand.mark  (34 px) el signo grande de la pantalla inicial,
 *                                         donde la build oficial deja el pez animado
 *                                         como reserva y no registra nada
 *
 * El registro es externo (banda `extension`), así que toma el relevo de la marca
 * integrada sin desactivarla. No importa los primitivos de DeepSeek: la balanza se
 * dibuja aquí, en SVG, con `currentColor` para que herede el tema.
 *
 * @module @openlegal/dsh-client-ui-brand-legal/client
 */
import type { ReactElement } from 'react';

/** Servicio requerido: el registro de slots de la interfaz. */
export const inject = ['slots'];

/** Presentación que el host decide para cada slot. */
interface PropsMarca {
  /** Lado del cuadrado, en píxeles (24 en el sidebar, 34 en el héroe). */
  size?: number;
  /** Clase del host: hay que devolverla aplicada o el slot pierde su sitio. */
  className?: string;
}

/**
 * La balanza: el signo del harness.
 *
 * Trazo simple y simétrico —mástil, base, brazo, cadenas y dos platillos— porque
 * tiene que leerse a 24 px y aguantar el tema oscuro y el claro sin cambiar.
 * @param props presentación del host.
 */
export function Balanza({ size = 24, className }: PropsMarca): ReactElement {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label="Open Legal Harness"
    >
      {/* remate del mástil */}
      <circle cx="12" cy="3" r="1" fill="currentColor" stroke="none" />
      {/* mástil y base */}
      <path d="M12 4.1v15.4" />
      <path d="M8.4 19.9h7.2" />
      {/* brazo: 13,6 de ancho y no 15,4, para que queden 3 unidades de aire a cada
          lado —las mismas que arriba y abajo—; a 24 px la versión ancha se veía
          apretada contra los bordes del sidebar */}
      <path d="M5.2 7h13.6" />
      {/* platillo izquierdo: cadenas en V y la copa */}
      <path d="M5.2 7 3 11.4" />
      <path d="M5.2 7l2.2 4.4" />
      <path d="M3 11.4a2.2 2.2 0 0 0 4.4 0z" fill="currentColor" fillOpacity="0.14" />
      {/* platillo derecho */}
      <path d="M18.8 7l-2.2 4.4" />
      <path d="M18.8 7l2.2 4.4" />
      <path d="M16.6 11.4a2.2 2.2 0 0 0 4.4 0z" fill="currentColor" fillOpacity="0.14" />
    </svg>
  );
}

/**
 * El nombre del producto, sin el signo (que va en su propio slot).
 *
 * Sólo alineación y peso: el tamaño y el color los pone el host en `.brandName`,
 * así que el nombre acompaña al tema en vez de imponerse.
 */
export function NombreProducto(): ReactElement {
  return (
    <span style={{ whiteSpace: 'nowrap', letterSpacing: '0.01em', fontWeight: 600 }}>
      Open Legal <span style={{ opacity: 0.72, fontWeight: 500 }}>Harness</span>
    </span>
  );
}

/** El contexto de cliente que usamos: el registro de slots. */
interface Contexto {
  slots: {
    inject: (nombre: string, cuerpo: () => unknown) => void;
    register: (declaracion: { name: string }, componente: (props: never) => ReactElement) => unknown;
  };
}

/**
 * Cuerpo del plugin en el navegador.
 *
 * Se declara el sidebar como un juego atado (el nombre sólo si hay signo) y el
 * héroe por separado, calcando el patrón del plugin de marca oficial.
 * @param ctx contexto del cliente.
 */
export function apply(ctx: Contexto): void {
  ctx.slots.inject('sidebar.brand.mark', () =>
    ctx.slots.inject('sidebar.brand.name', function* () {
      yield ctx.slots.register({ name: 'sidebar.brand.mark' }, Balanza as never);
      yield ctx.slots.register({ name: 'sidebar.brand.name' }, NombreProducto as never);
    }),
  );

  ctx.slots.inject('conversation.hero.brand.mark', function* () {
    yield ctx.slots.register({ name: 'conversation.hero.brand.mark' }, Balanza as never);
  });
}
