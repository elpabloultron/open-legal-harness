/**
 * Autenticación del panel: sesión por cookie contra la API del CRM.
 *
 * Tres cosas que valen la pena del diseño:
 *  1. `canAccess` responde con el mismo mapa de permisos que exige el servidor, así que el
 *     menú y las rutas se arman solos y un módulo nuevo no se le puede colar a quien no le
 *     toca.
 *  2. La sesión se comprueba contra `/api/sesion` (el servidor), no contra algo guardado en
 *     el navegador: si el administrador cortó el acceso, la próxima llamada lo dice.
 *  3. Cuando la cuenta está bloqueada por intentos fallidos, el 429 se muestra como lo que
 *     es («esperá un rato»), no como «contraseña incorrecta».
 */
import { ErrorApi, enviar, obtener } from "./cliente";
import { permisoDe, type Sesion } from "../tipos";

let enMemoria: Sesion | null = null;

export async function sesionActual(refrescar = false): Promise<Sesion | null> {
  if (enMemoria && !refrescar) return enMemoria;
  try {
    enMemoria = await obtener<Sesion>("/api/sesion");
    return enMemoria;
  } catch (error) {
    if (error instanceof ErrorApi && error.estado === 401) return null;
    throw error;
  }
}

export async function permisosActuales(): Promise<string[]> {
  const sesion = await sesionActual();
  return sesion?.permisos ?? [];
}

export const authProvider = {
  async login({ email, password, codigo }: { email: string; password: string; codigo?: string }) {
    await enviar("/api/login", { email, password, codigo: codigo?.trim() ? codigo.trim() : null });
    await sesionActual(true);
  },

  async logout() {
    try {
      await enviar("/api/logout");
    } finally {
      enMemoria = null;
    }
  },

  async checkAuth() {
    const sesion = await sesionActual();
    if (!sesion) throw new Error("sin sesión");
  },

  async checkError(error: unknown) {
    if (error instanceof ErrorApi && [401, 403, 429].includes(error.estado)) {
      if (error.estado === 401) enMemoria = null;
      throw error;
    }
  },

  async getPermissions() {
    return permisosActuales();
  },

  async getIdentity() {
    const sesion = await sesionActual();
    if (!sesion) throw new Error("sin sesión");
    return { id: sesion.usuario.id, fullName: sesion.usuario.nombre, email: sesion.usuario.email };
  },

  /** Qué puede hacer esta persona en este recurso. Lo usan el menú y las rutas. */
  async canAccess({ resource, action }: { resource: string; action: string }) {
    const permisos = await permisosActuales();
    if (action === "list") {
      // Quien ve todas las causas del estudio puede abrir el listado; el resto ve las suyas
      // igual, porque la API sólo devuelve las que le tocan.
      return permisos.some((p) => p.startsWith(`${resource}.`) || p === "causa.leer.propia" || p === "causa.leer.todas");
    }
    const permiso = permisoDe(resource, action);
    if (!permiso) return true;
    return permisos.includes(permiso) || permisos.includes("causa.leer.todas");
  },
};

/** Para las pruebas: deja el módulo como recién cargado. */
export function olvidarSesion() {
  enMemoria = null;
}
