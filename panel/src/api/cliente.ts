/**
 * Cliente HTTP del panel: habla con la API local del mismo origen.
 *
 * La sesión es una cookie `httpOnly` que pone el servidor, así que no hay tokens en
 * JavaScript: se pide todo con `credentials: "same-origin"` y el navegador se encarga.
 * Esto no es un detalle de estilo: significa que un error de XSS en la interfaz no se lleva
 * la sesión, y que el token del panel no queda guardado en ningún lado del navegador.
 */

/** Error con el código HTTP y el mensaje que el CRM ya sabe explicar. */
export class ErrorApi extends Error {
  constructor(
    public readonly estado: number,
    mensaje: string,
  ) {
    super(mensaje);
    this.name = "ErrorApi";
  }
}

const MENSAJES: Record<number, string> = {
  401: "se te terminó la sesión: volvé a entrar",
  403: "tu rol no tiene permiso para esto",
  429: "demasiados intentos: la cuenta está bloqueada un rato",
};

/** Lee el mensaje del cuerpo, que el servidor manda en `detail` (texto o lista). */
async function mensajeDe(respuesta: Response): Promise<string> {
  try {
    const cuerpo = await respuesta.json();
    if (typeof cuerpo?.detail === "string") return cuerpo.detail;
    if (Array.isArray(cuerpo?.detail)) {
      return cuerpo.detail.map((d: { msg?: string }) => d?.msg ?? "").filter(Boolean).join("; ");
    }
  } catch {
    /* cuerpo vacío o no es JSON */
  }
  return MENSAJES[respuesta.status] ?? `error ${respuesta.status}`;
}

export async function pedir<T>(ruta: string, opciones: RequestInit = {}): Promise<T> {
  const respuesta = await fetch(ruta, {
    credentials: "same-origin",
    ...opciones,
    headers: {
      ...(opciones.body ? { "Content-Type": "application/json" } : {}),
      ...(opciones.headers ?? {}),
    },
  });
  if (!respuesta.ok) throw new ErrorApi(respuesta.status, await mensajeDe(respuesta));
  if (respuesta.status === 204) return undefined as T;
  const tipo = respuesta.headers.get("content-type") ?? "";
  if (!tipo.includes("json")) return (await respuesta.text()) as unknown as T;
  return (await respuesta.json()) as T;
}

export const obtener = <T>(ruta: string) => pedir<T>(ruta);

export const enviar = <T>(ruta: string, cuerpo?: unknown) =>
  pedir<T>(ruta, { method: "POST", body: JSON.stringify(cuerpo ?? {}) });

/** Descarga un archivo (el expediente del titular) sin perder las cabeceras de respuesta. */
export async function descargar(ruta: string, cuerpo: unknown, nombre: string): Promise<string> {
  const respuesta = await fetch(ruta, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpo),
  });
  if (!respuesta.ok) throw new ErrorApi(respuesta.status, await mensajeDe(respuesta));
  const blob = await respuesta.blob();
  const enlace = document.createElement("a");
  enlace.href = URL.createObjectURL(blob);
  enlace.download = nombre;
  enlace.click();
  URL.revokeObjectURL(enlace.href);
  return respuesta.headers.get("X-OpenLegal-Sha256") ?? "";
}
