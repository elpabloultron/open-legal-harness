/** El mapa de permisos: si esto se equivoca, el menú muestra módulos que el servidor niega. */
import { describe, expect, it } from "vitest";

import { fechaLegible, permisoDe, sellosLegibles } from "../tipos";

describe("permisos del panel", () => {
  it("sabe qué permiso pide cada módulo", () => {
    expect(permisoDe("usuarios", "list")).toBe("usuario.gestionar");
    expect(permisoDe("avisos", "list")).toBe("aviso.gestionar");
    expect(permisoDe("retencion", "list")).toBe("usuario.gestionar");
    expect(permisoDe("titulares", "list")).toBe("titular.gestionar");
    expect(permisoDe("causas", "create")).toBe("causa.crear");
    // Crear un cliente es `cliente.editar` en el CRM, no un permiso aparte.
    expect(permisoDe("clientes", "create")).toBe("cliente.editar");
  });

  it("no inventa permisos para módulos que no conoce", () => {
    expect(permisoDe("inventado", "list")).toBeUndefined();
  });
});

describe("fechas", () => {
  it("muestra las fechas como se escriben acá", () => {
    expect(fechaLegible("2026-09-20")).toBe("20-09-2026");
    expect(fechaLegible(null)).toBe("—");
    expect(sellosLegibles("2026-09-18 21:25:23")).toBe("18-09-2026 21:25");
  });
});
