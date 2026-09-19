/**
 * Pruebas del conector con la API: es la capa donde un error se traduce en datos mal
 * mostrados o en un cambio que no debería haber pasado, así que se prueba sin navegador.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorApi, obtener } from "../api/cliente";
import { dataProvider } from "../api/dataProvider";

interface Llamada {
  url: string;
  metodo: string;
  cuerpo: unknown;
}

let llamadas: Llamada[] = [];

function respuesta(cuerpo: unknown, estado = 200, cabeceras: Record<string, string> = {}) {
  return {
    ok: estado < 400,
    status: estado,
    headers: new Headers({ "content-type": "application/json", ...cabeceras }),
    json: async () => cuerpo,
    text: async () => JSON.stringify(cuerpo),
    blob: async () => new Blob([JSON.stringify(cuerpo)]),
  } as unknown as Response;
}

beforeEach(() => {
  llamadas = [];
  vi.stubGlobal("fetch", async (url: string, opciones: RequestInit = {}) => {
    llamadas.push({ url: String(url), metodo: opciones.method ?? "GET", cuerpo: opciones.body ? JSON.parse(String(opciones.body)) : null });
    if (String(url).includes("/api/error")) throw new ErrorApi(403, "tu rol no tiene permiso para esto");
    if (String(url).includes("/api/causas")) {
      return respuesta([{ id: 1, caratula: "Herrera con Fondo del Norte", estado_procesal: "tramitacion" }]);
    }
    if (String(url).includes("/api/usuarios")) {
      // La API entrega los usuarios con los roles y los bloqueos, no como una lista pelada.
      return respuesta({ usuarios: [{ id: 2, nombre: "Ana Pérez", rol: "abogado" }], roles: ["socio", "abogado"], bloqueos: {} });
    }
    if (String(url).includes("/api/plazos")) return respuesta({ id: 7, fecha_vencimiento: "2026-09-22" });
    if (String(url).includes("/api/audiencias")) return respuesta({ id: 9 });
    return respuesta({ id: 5 });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("dataProvider", () => {
  it("trae las causas del endpoint que ya conoce los permisos", async () => {
    const resultado = await dataProvider.getList("causas", {
      filter: {},
      pagination: { page: 1, perPage: 25 },
      sort: { field: "id", order: "DESC" },
    });

    expect(llamadas[0].url).toBe("/api/causas");
    expect(resultado.total).toBe(1);
    expect(resultado.data[0]).toMatchObject({ caratula: "Herrera con Fondo del Norte" });
  });

  it("busca clientes con el texto en la consulta", async () => {
    await dataProvider.getList("clientes", {
      filter: { q: "herrera" },
      pagination: { page: 1, perPage: 25 },
      sort: { field: "id", order: "ASC" },
    });

    expect(llamadas[0].url).toBe("/api/clientes?q=herrera");
  });

  it("cuenta los plazos con los días del filtro", async () => {
    await dataProvider.getList("plazos", {
      filter: { dias: 90 },
      pagination: { page: 1, perPage: 25 },
      sort: { field: "fecha_vencimiento", order: "ASC" },
    });

    expect(llamadas[0].url).toBe("/api/plazos?dias=90");
  });

  it("desenvuelve la lista de usuarios que la API entrega con más datos", async () => {
    const resultado = await dataProvider.getList("usuarios", {
      filter: {},
      pagination: { page: 1, perPage: 25 },
      sort: { field: "nombre", order: "ASC" },
    });

    expect(resultado.data[0]).toMatchObject({ nombre: "Ana Pérez" });
  });

  it("crea un plazo con los días y la fecha de notificación", async () => {
    await dataProvider.create("plazos", {
      data: { causa_id: 1, descripcion: "Contestar traslado", dias: 8, fecha_notificacion: "2026-09-15", es_fatal: true },
    });

    expect(llamadas[0].metodo).toBe("POST");
    expect(llamadas[0].url).toBe("/api/plazos");
    expect(llamadas[0].cuerpo).toMatchObject({ causa_id: 1, dias: 8, notificacion: "2026-09-15", es_fatal: true });
  });

  it("edita un usuario contra su endpoint", async () => {
    await dataProvider.update("usuarios", {
      id: 2,
      data: { rol: "paralegal", telefono: "+56912345678", activo: false },
      previousData: { id: 2 },
    });

    expect(llamadas[0].url).toBe("/api/usuarios/2");
    expect(llamadas[0].cuerpo).toMatchObject({ rol: "paralegal", activo: false });
  });

  it("no borra: el CRM desactiva o anonimiza, y lo dice", async () => {
    await expect(dataProvider.delete("clientes", { id: 1, previousData: { id: 1 } })).rejects.toThrow(
      /no borra: se desactiva o se anonimiza/,
    );
  });

  it("propaga el mensaje que el CRM ya sabe explicar", async () => {
    await expect(obtener("/api/error")).rejects.toThrow("tu rol no tiene permiso para esto");
  });

  it("no borra: lo dice con el motivo del CRM", async () => {
    await expect(dataProvider.deleteMany("causas", { ids: [1, 2] })).rejects.toThrow(/no borra/);
  });
});
