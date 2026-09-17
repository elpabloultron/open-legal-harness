/**
 * Mitad navegador: pone «CRM Jurídico» bajo «New session» y su panel en el área
 * principal, sin tocar el código del harness.
 *
 * Dos asientos públicos, los mismos que usan ui-settings y ui-workspace:
 *
 *   sidebar.panellist  (list)  -> la fila con ícono: id, order, label
 *   main               (keyed) -> el panel que se abre al elegir esa fila,
 *                                 direccionado por el mismo id
 *
 * El contenido es el CRM local (`openlegal serve`) en un iframe: el servicio
 * corre en 127.0.0.1 con token, y el token se guarda en el navegador — nunca se
 * escribe en el código ni viaja al modelo.
 *
 * @module @openlegal/dsh-client-ui-crm/client
 */
import { useCallback, useEffect, useState } from 'react';

/**
 * Contrato mínimo y estructural de los dos asientos que usamos y del ciclo de
 * vida Cordis. No se importan los paquetes de tipos del harness a propósito: su
 * árbol de dependencias apunta a paquetes internos sin publicar, así que no son
 * instalables fuera de su monorepo. Lo que importa —nombre del asiento,
 * cardinalidad, y el `{id | key}` que direcciona el panel— está en el README.
 */
export interface ContextoCliente {
  slots: {
    register(
      declaracion: {
        name: string;
        id?: string;
        key?: string;
        order?: number;
        label?: string;
      },
      componente: () => JSX.Element | null,
    ): () => void;
    inject(clave: string, cuerpo: () => () => void): () => void;
  };
  effect(cuerpo: () => unknown, etiqueta?: string): void;
}

/** Identificador de la fila y clave del panel: el mismo en los dos asientos. */
export const FILA_ID = 'openlegal-crm';
const LOGO = 'dsh-openlegal';
const ALMACEN = 'openlegal:crm:config';

interface ConfigCrm {
  url: string;
  token: string;
}

const CONFIG_INICIAL: ConfigCrm = { url: 'http://127.0.0.1:8899', token: '' };

function leerConfig(): ConfigCrm {
  try {
    const crudo = globalThis.localStorage?.getItem(ALMACEN);
    return crudo ? { ...CONFIG_INICIAL, ...(JSON.parse(crudo) as Partial<ConfigCrm>) } : CONFIG_INICIAL;
  } catch {
    return CONFIG_INICIAL;
  }
}

/** Ícono de la fila: una balanza, en el trazo del sidebar. */
function IconoBalanza() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M12 3v18M5 21h14M7 7h10M7 7l-3 6a3 3 0 0 0 6 0ZM17 7l3 6a3 3 0 0 1-6 0Z" />
    </svg>
  );
}

/** Fila del sidebar. El texto lo aporta `label`; aquí solo va el ícono. */
export function FilaCrm() {
  return <IconoBalanza />;
}

/** Panel principal: el CRM, o el formulario para conectarlo la primera vez. */
export function PanelCrm() {
  const [config, setConfig] = useState<ConfigCrm>(() => leerConfig());
  const [borrador, setBorrador] = useState<ConfigCrm>(() => leerConfig());
  const [cargando, setCargando] = useState(true);

  const guardar = useCallback((siguiente: ConfigCrm) => {
    globalThis.localStorage?.setItem(ALMACEN, JSON.stringify(siguiente));
    setConfig(siguiente);
  }, []);

  // Sondeo de disponibilidad: distinguir «el CRM no está levantado» de «token malo».
  const [estado, setEstado] = useState<'desconocido' | 'ok' | 'sin-servicio' | 'sin-token'>('desconocido');
  useEffect(() => {
    if (!config.token) {
      setEstado('desconocido');
      setCargando(false);
      return;
    }
    let vigente = true;
    setCargando(true);
    fetch(`${config.url}/api/estado`, { headers: { 'X-OpenLegal-Token': config.token } })
      .then((respuesta) => vigente && setEstado(respuesta.ok ? 'ok' : 'sin-token'))
      .catch(() => vigente && setEstado('sin-servicio'))
      .finally(() => vigente && setCargando(false));
    return () => {
      vigente = false;
    };
  }, [config]);

  const marco = { border: 0, width: '100%', height: '100%', background: 'transparent' } as const;

  if (estado === 'ok') {
    return (
      <iframe
        title="CRM Jurídico"
        src={`${config.url}/?token=${encodeURIComponent(config.token)}`}
        style={marco}
        referrerPolicy="no-referrer"
      />
    );
  }

  const aviso = !config.token
    ? 'Falta conectar: pega abajo el token que imprime «openlegal serve».'
    : estado === 'sin-servicio'
      ? 'No responde el servicio del CRM. Levántalo con: openlegal serve --port 8899'
      : estado === 'sin-token'
        ? 'El servicio rechazó el token: debe ser el de esta ejecución del CRM.'
        : 'Comprobando el servicio del CRM…';

  return (
    <div style={{ padding: '18px', font: '13px/1.6 system-ui, sans-serif', color: 'var(--foreground, #e8ecf3)', maxWidth: '560px' }}>
      <h2 style={{ margin: '0 0 6px', fontSize: '15px' }}>CRM Jurídico</h2>
      <p style={{ margin: '0 0 14px', color: 'var(--muted-foreground, #98a2b8)' }}>
        {cargando ? 'Comprobando el servicio del CRM…' : aviso}
      </p>
      <p style={{ margin: '0 0 14px', color: 'var(--muted-foreground, #98a2b8)' }}>
        Arranca el CRM (te imprime la URL con su token) y pégalo aquí. El token queda en tu navegador.
      </p>
      <label style={{ display: 'block', marginBottom: '8px' }}>
        Dirección del CRM
        <input
          value={borrador.url}
          onChange={(evento) => setBorrador({ ...borrador, url: evento.target.value })}
          style={{ display: 'block', width: '100%', marginTop: '4px', padding: '6px 8px' }}
        />
      </label>
      <label style={{ display: 'block', marginBottom: '14px' }}>
        Token
        <input
          value={borrador.token}
          onChange={(evento) => setBorrador({ ...borrador, token: evento.target.value })}
          style={{ display: 'block', width: '100%', marginTop: '4px', padding: '6px 8px' }}
        />
      </label>
      <button type="button" onClick={() => guardar(borrador)} style={{ padding: '6px 12px', cursor: 'pointer' }}>
        Conectar el CRM
      </button>
    </div>
  );
}

/** Servicios del cliente que este plugin necesita. */
export const inject = ['slots'];

/**
 * Cuerpo del plugin en el navegador: registra la fila y el panel.
 * @param ctx contexto raíz del cliente.
 */
export function apply(ctx: ContextoCliente): void {
  // Fila bajo «New session». `order` la ubica entre el navegador de sesiones y
  // los ajustes; los ids de otros plugins quedan libres para ordenarse solos.
  ctx.effect(
    () =>
      ctx.slots.inject('sidebar.panellist', () =>
        ctx.slots.register({ name: 'sidebar.panellist', id: FILA_ID, order: 50, label: 'CRM Jurídico' }, FilaCrm),
      ),
    `${LOGO}: fila del sidebar`,
  );

  // Panel del área principal, direccionado por el mismo id.
  ctx.effect(
    () => ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: FILA_ID }, PanelCrm)),
    `${LOGO}: panel principal`,
  );
}
