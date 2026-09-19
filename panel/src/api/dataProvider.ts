/**
 * El conector entre React-Admin y la API del CRM.
 *
 * No existe un conector de FastAPI mantenido, así que está escrito acá y es corto a
 * propósito: cada método es una traducción directa de lo que la API ya hace. Las reglas
 * (permisos, cálculo de plazos con el Art. 66 CPC, avisos, bitácora) siguen del lado del
 * servidor: este archivo no las reimplementa, las llama.
 */
import type { DataProvider, Identifier } from "react-admin";
import { ErrorApi, enviar, obtener } from "./cliente";
import type { Audiencia, Causa, Cliente, Plazo, Usuario } from "../tipos";

interface ParametrosDeLista {
  filter?: Record<string, unknown>;
  pagination?: { page: number; perPage: number };
  sort?: { field: string; order: "ASC" | "DESC" };
}

const conTotal = <T>(filas: T[]) => ({ data: filas, total: filas.length });

/** Cuántos días hacia adelante mira un listado cuando no se pidió otra cosa. */
function diasDesde(params: ParametrosDeLista): number {
  const dias = Number(params.filter?.dias ?? 0);
  if (dias > 0) return dias;
  return Math.max(60, (params.pagination?.perPage ?? 25) * 3);
}

/** Saca los campos que la API no espera y convierte los vacíos en nulos. */
function limpiar(datos: Record<string, unknown>): Record<string, unknown> {
  const salida: Record<string, unknown> = {};
  for (const [clave, valor] of Object.entries(datos)) {
    if (clave === "id" || clave.startsWith("_")) continue;
    salida[clave] = valor === "" ? null : valor;
  }
  return salida;
}

const proveedor = {
  async getList(recurso: string, params: ParametrosDeLista) {
    switch (recurso) {
      case "causas":
        return conTotal(await obtener<Causa[]>("/api/causas"));
      case "plazos":
        return conTotal(await obtener<Plazo[]>(`/api/plazos?dias=${diasDesde(params)}`));
      case "audiencias":
        return conTotal(await obtener<Audiencia[]>(`/api/agenda?dias=${diasDesde(params)}`));
      case "clientes": {
        const texto = String(params.filter?.q ?? "").trim();
        return conTotal(
          await obtener<Cliente[]>(texto ? `/api/clientes?q=${encodeURIComponent(texto)}` : "/api/clientes"),
        );
      }
      case "usuarios": {
        const respuesta = await obtener<{ usuarios: Usuario[] }>("/api/usuarios");
        return conTotal(respuesta.usuarios);
      }
      default:
        throw new Error(`el recurso ${recurso} no tiene listado en el panel`);
    }
  },

  async getOne(recurso: string, params: { id: Identifier }) {
    const { data } = await proveedor.getList(recurso, { pagination: { page: 1, perPage: 500 } });
    const fila = data.find((f) => String((f as { id: Identifier }).id) === String(params.id));
    if (!fila) throw new ErrorApi(404, `no existe ${recurso} ${params.id}`);
    return { data: fila };
  },

  async getMany(recurso: string, params: { ids: Identifier[] }) {
    const { data } = await proveedor.getList(recurso, { pagination: { page: 1, perPage: 500 } });
    const buscados = new Set(params.ids.map(String));
    return { data: data.filter((f) => buscados.has(String((f as { id: Identifier }).id))) };
  },

  async getManyReference(recurso: string, params: { id: Identifier; target: string; filter: Record<string, unknown> }) {
    const { data } = await proveedor.getList(recurso, {
      filter: params.filter,
      pagination: { page: 1, perPage: 200 },
    });
    return { data: data.filter((f) => String((f as unknown as Record<string, unknown>)[params.target]) === String(params.id)) };
  },

  async create(recurso: string, params: { data: Record<string, unknown> }) {
    const datos = params.data;
    switch (recurso) {
      case "plazos": {
        const creado = await enviar<{ id: number; fecha_vencimiento: string }>("/api/plazos", {
          causa_id: Number(datos.causa_id),
          descripcion: datos.descripcion,
          dias: datos.dias ? Number(datos.dias) : null,
          notificacion: datos.fecha_notificacion ?? null,
          es_fatal: datos.es_fatal !== false,
        });
        // Se devuelve el vencimiento que calculó el servidor, con el Art. 66 CPC: el panel no
        // lo recalcula, lo muestra.
        return { data: { ...datos, id: creado.id, fecha_vencimiento: creado.fecha_vencimiento } };
      }
      case "audiencias":
        return { data: { ...datos, id: (await enviar<{ id: number }>("/api/audiencias", limpiar(datos))).id } };
      case "causas":
        return { data: { ...datos, id: (await enviar<{ id: number }>("/api/causas", limpiar(datos))).id } };
      case "clientes":
        return { data: { ...datos, id: (await enviar<{ id: number }>("/api/clientes", limpiar(datos))).id } };
      case "usuarios":
        return { data: { ...datos, id: (await enviar<{ id: number }>("/api/usuarios", limpiar(datos))).id } };
      default:
        throw new Error(`el panel todavía no crea ${recurso}`);
    }
  },

  async update(recurso: string, params: { id: Identifier; data: Record<string, unknown> }) {
    const datos = params.data;
    if (recurso !== "usuarios") throw new Error(`el panel todavía no edita ${recurso}`);
    const actualizado = await enviar<Usuario>(`/api/usuarios/${params.id}`, {
      rol: datos.rol,
      telefono: datos.telefono ?? null,
      activo: datos.activo === undefined ? undefined : Boolean(Number(datos.activo)),
      password: datos.password ? String(datos.password) : null,
    });
    return { data: { ...actualizado, id: params.id } };
  },

  async updateMany() {
    throw new Error("el panel no hace cambios en lote: cada cambio deja su rastro en la bitácora");
  },

  async delete() {
    throw new Error(
      "el CRM no borra: se desactiva o se anonimiza, que es lo que después permite responder por lo que se hizo",
    );
  },

  async deleteMany() {
    throw new Error("el CRM no borra: se desactiva o se anonimiza");
  },
};

// Los métodos devuelven los tipos concretos del CRM (Causa, Plazo, …) y React-Admin espera
// genéricos: se le dice que este objeto cumple el contrato, sin perder el tipado adentro.
export const dataProvider = proveedor as unknown as DataProvider;
