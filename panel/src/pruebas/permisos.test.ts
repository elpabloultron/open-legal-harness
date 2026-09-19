/** El mapa de permisos: si esto se equivoca, el menú muestra módulos que el servidor niega. */
import { describe, expect, it } from "vitest";

import { clp, fechaLegible, permisoDe, sellosLegibles } from "../tipos";

describe("permisos del panel", () => {
  it("sabe qué permiso pide cada módulo", () => {
    expect(permisoDe("usuarios", "list")).toBe("usuario.gestionar");
    expect(permisoDe("avisos", "list")).toBe("aviso.gestionar");
    expect(permisoDe("retencion", "list")).toBe("usuario.gestionar");
    expect(permisoDe("titulares", "list")).toBe("titular.gestionar");
    expect(permisoDe("causas", "create")).toBe("causa.crear");
    // Crear un cliente es `cliente.editar` en el CRM, no un permiso aparte.
    expect(permisoDe("clientes", "create")).toBe("cliente.editar");
    // Honorarios: leerlos es `honorario.leer`; darlos de alta (y registrar abonos) es
    // `honorario.editar`, que es el permiso de finanzas.
    expect(permisoDe("honorarios", "list")).toBe("honorario.leer");
    expect(permisoDe("honorarios", "create")).toBe("honorario.editar");
    expect(permisoDe("cuenta", "list")).toBe("honorario.leer");
    expect(permisoDe("gastos", "create")).toBe("gasto.editar");
    expect(permisoDe("pagos", "create")).toBe("honorario.editar");
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

describe("montos en pesos", () => {
  it("escribe los CLP enteros como se escriben acá", () => {
    expect(clp(350000)).toBe("$350.000");
    expect(clp(25000)).toBe("$25.000");
    expect(clp(0)).toBe("$0");
    expect(clp(-50000)).toBe("-$50.000");
  });

  it("no confunde «sin dato» con cero", () => {
    // Un monto nulo es un dato que falta (p. ej. el líquido sin boleta), no un cero.
    expect(clp(null)).toBe("—");
    expect(clp(undefined)).toBe("—");
  });
});
