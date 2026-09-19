/**
 * Los tipos del CRM tal como los entrega la API, y el mapa de permisos.
 *
 * El mapa de permisos es la parte importante: dice qué permiso del servidor hace falta para
 * cada recurso y cada acción. El menú y las pantallas se arman de acá, y el servidor vuelve
 * a exigir el mismo permiso en cada llamada — esconder un botón es cortesía, no seguridad.
 */

export type Rol = "socio" | "administrador" | "abogado" | "paralegal" | "administrativo" | "cliente";

export interface Usuario {
  id: number;
  nombre: string;
  email: string;
  rol: Rol;
  telefono: string | null;
  activo: number;
  totp_activo: number;
}

export interface Cliente {
  id: number;
  nombre: string;
  rut: string | null;
  email: string | null;
  telefono: string | null;
  direccion?: string | null;
  tipo_persona?: string;
}

export interface Causa {
  id: number;
  caratula: string;
  rol_rit: string | null;
  tribunal: string | null;
  materia: string | null;
  estado_procesal: string;
  cliente_id?: number | null;
  proximo_vencimiento?: { fecha_vencimiento: string; descripcion: string; es_fatal: number } | null;
  equipo?: string[];
}

export interface Plazo {
  id: number;
  causa_id: number;
  caratula: string;
  descripcion: string;
  fecha_notificacion: string | null;
  fecha_vencimiento: string | null;
  dias: number | null;
  es_fatal: number;
  estado: string;
  dias_restantes?: number;
}

export interface Audiencia {
  id: number;
  causa_id: number;
  caratula: string;
  tipo: string;
  fecha: string;
  hora: string | null;
  modalidad: string | null;
  lugar_o_url: string | null;
  minuta: string | null;
  estado: string;
}

export interface Sesion {
  usuario: { id: number; nombre: string; email: string; rol: Rol; estudio_id: number };
  estudio: { id: number; nombre: string; modo: string };
  via: "sesion" | "token";
  permisos: string[];
  version: string;
}

export interface CanalDeAviso {
  canal: string;
  listo: boolean;
  detalle: string;
}

export interface AvisoEnCola {
  id: number;
  canal: string;
  destino: string;
  asunto: string | null;
  estado: string;
  intentos: number;
  ultimo_error: string | null;
  creada_en: string;
  enviada_en: string | null;
}

export interface CuentaSeguridad {
  email: string;
  nombre: string;
  rol: Rol;
  activo: number;
  totp_activo: number;
  esperado: boolean;
  bloqueo?: { bloqueada: boolean; faltan_minutos: number; intentos: number };
}

/** Qué permiso del servidor pide cada recurso y cada acción del panel. */
export const PERMISOS: Record<string, Record<string, string>> = {
  causas: { list: "causa.leer", create: "causa.crear" },
  clientes: { list: "cliente.leer", create: "cliente.editar" },
  plazos: { list: "plazo.leer", create: "plazo.crear", close: "plazo.cerrar" },
  audiencias: { list: "audiencia.leer", create: "audiencia.crear" },
  usuarios: { list: "usuario.gestionar", create: "usuario.gestionar" },
  avisos: { list: "aviso.gestionar" },
  seguridad: { list: "usuario.gestionar" },
  retencion: { list: "usuario.gestionar" },
  titulares: { list: "titular.gestionar" },
  panel: { list: "reporte.panel" },
};

export function permisoDe(recurso: string, accion: string): string | undefined {
  return PERMISOS[recurso]?.[accion];
}

export function fechaLegible(iso?: string | null): string {
  if (!iso) return "—";
  const [a, m, d] = String(iso).slice(0, 10).split("-");
  return d && m && a ? `${d}-${m}-${a}` : String(iso);
}

export function sellosLegibles(iso?: string | null): string {
  if (!iso) return "—";
  return `${fechaLegible(iso)} ${String(iso).slice(11, 16)}`;
}
