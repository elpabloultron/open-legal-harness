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

/* ---------------------------------------------- honorarios y cuenta de la causa */

export interface Honorario {
  id: number;
  causa_id: number;
  /** fijo | hora | cuota_litis | mixto */
  modalidad: string;
  monto_pactado: number | null;
  monto_bruto: number | null;
  /** La retención que declaró el estudio; null = no la declaró (y el líquido es el bruto). */
  retencion_sii: number | null;
  monto_liquido: number | null;
  monto_pagado: number;
  /** pendiente | parcial | pagado */
  estado_pago: string;
  descripcion: string | null;
  fecha: string | null;
}

export interface Gasto {
  id: number;
  causa_id: number;
  concepto: string;
  monto: number;
  /** 1 = lo adelantó el estudio y se le cuenta al cliente; 0 = lo pagó el cliente. */
  pagado_por_estudio: number;
  reembolsado: number;
  fecha: string | null;
  comprobante: string | null;
}

export interface Pago {
  id: number;
  causa_id: number;
  honorario_id: number | null;
  fecha: string;
  monto: number;
  medio: string;
  referencia: string | null;
  nota: string | null;
  registrado_por_nombre?: string | null;
}

export interface CuentaDividendos {
  estudio: { nombre: string | null; rut: string | null };
  cliente: { nombre: string | null; rut: string | null; direccion: string | null } | null;
  causa: { id: number; caratula: string | null; rol_rit: string | null; tribunal: string | null };
  honorarios: Honorario[];
  gastos: Gasto[];
  pagos: Pago[];
  totales: {
    honorarios_pactado: number;
    honorarios_liquido: number;
    honorarios_pagado: number;
    gastos: number;
    gastos_por_cuenta_del_cliente: number;
    pagos: number;
    saldo: number;
  };
  advertencias: string[];
  generado_en: string;
}

/* ---------------------------------------------- IA y transferencias */

/**
 * Un envío a un proveedor de IA, tal como queda registrado.
 *
 * Son METADATOS: el contenido de lo que se mandó no se guarda en ninguna parte (por eso no
 * hay ningún campo con el texto). El hash es lo que permite demostrar que lo que salió es lo
 * mismo que está en el expediente.
 */
export interface EnvioIA {
  id: number;
  causa_id: number | null;
  autorizacion_id: number | null;
  /** crm | proxy | otro: de dónde salió el envío */
  origen: string;
  /** por ejemplo openai-compat */
  via: string | null;
  proveedor: string;
  modelo: string | null;
  destino_pais: string | null;
  caracteres: number;
  hash_payload: string;
  redactado: number;
  bloqueado: number;
  motivo_bloqueo: string | null;
  creado_en: string;
  caratula: string | null;
  usuario: string | null;
}

export interface AutorizacionIA {
  id: number;
  causa_id: number;
  /** analisis | redaccion | ambos */
  alcance: string;
  base_licitud: string;
  titular: string | null;
  vigente: number;
  creado_en: string;
  revocada_en: string | null;
  caratula: string | null;
  registrado_por_nombre: string | null;
}

/** El estado del proxy local. La api_key nunca llega: se informa «configurada» o «falta». */
export interface EstadoProxyIA {
  archivo: string;
  permisos: string;
  puerto: number;
  /** La dirección del dialecto de OpenAI (`…/v1`). */
  url: string;
  /**
   * La dirección del harness: el proxy a secas, SIN `/v1` ni `/anthropic`, porque el adaptador
   * del protocolo `messages` le agrega `/v1/messages` él mismo.
   */
  url_anthropic: string;
  activo: boolean;
  proveedor: string;
  base_url: string | null;
  /** La dirección aguas arriba del dialecto de Anthropic (`/v1/messages`). */
  base_url_anthropic: string | null;
  modelo_por_defecto: string | null;
  destino_pais: string;
  minimizar: boolean;
  permitir_sin_autorizacion: boolean;
  avisar_escritorio: boolean;
  causa_por_defecto: number | null;
  api_key: string;
  base_de_datos: string;
  /** Cómo se registra un envío del dialecto de OpenAI (`openai-compat`). */
  via: string;
  /** Cómo se registra un envío del dialecto de Anthropic (`anthropic-compat`). */
  via_anthropic: string;
  avisos: string[];
}

export interface ProveedorIA {
  proveedor: string;
  pais: string;
  entrena_con_api: string;
  retencion: string;
  zdr: string;
  nota: string;
}

export interface ModuloIA {
  proxy: EstadoProxyIA;
  envios: EnvioIA[];
  bloqueados: EnvioIA[];
  autorizaciones: AutorizacionIA[];
  proveedores: ProveedorIA[];
  aviso: string;
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
  // Los honorarios se leen con `honorario.leer` (y su `.todas` para el socio); darlos de alta
  // es `honorario.editar`, que es el permiso de finanzas. Los gastos van por su propio permiso,
  // y los pagos son plata de honorarios: los registra quien puede editar honorarios.
  honorarios: { list: "honorario.leer", create: "honorario.editar" },
  gastos: { list: "gasto.leer", create: "gasto.editar" },
  pagos: { list: "honorario.leer", create: "honorario.editar" },
  cuenta: { list: "honorario.leer" },
  // La pantalla de IA pide `ia.leer`, el mismo permiso que cuida el registro de envíos: lo
  // tienen socio y abogado. La administración del CRM (`auditoria.leer`) no alcanza.
  ia: { list: "ia.leer" },
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

/** Montos en pesos, como se escriben acá: $350.000. Sin dato no es cero, es «—». */
export function clp(valor?: number | null): string {
  if (valor === null || valor === undefined) return "—";
  const numero = Math.round(valor);
  const signo = numero < 0 ? "-" : "";
  return `${signo}$${String(Math.abs(numero)).replace(/\B(?=(\d{3})+(?!\d))/g, ".")}`;
}
