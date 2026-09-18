/* Panel del CRM: JS sin dependencias. Todos los módulos hablan con la API local del mismo
 * origen, que exige sesión (o el token del panel) y comprueba permisos en cada llamada.
 *
 * Reglas de esta interfaz:
 *  - Todo lo que se puede hacer por terminal se puede hacer acá. La terminal queda para
 *    quien la prefiera, no como requisito.
 *  - Ninguna clave se muestra: los formularios de correo y SMS se llenan con lo que ya
 *    está cargado menos los secretos, y si el campo queda vacío se conserva el que había.
 *  - Lo que no se puede deshacer (anonimizar, retención real) pide escribir una palabra
 *    de confirmación, y por defecto se simula.
 */

const estado = { causas: [], clientes: [], usuarios: [], vista: "inicio", permisos: [] };

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];

/* ------------------------------------------------------------------ helpers */

async function pedir(ruta, opciones = {}) {
  const respuesta = await fetch(ruta, { credentials: "same-origin", ...opciones });
  const tipo = respuesta.headers.get("content-type") || "";
  if (!respuesta.ok) {
    let detalle = `HTTP ${respuesta.status}`;
    if (tipo.includes("json")) {
      const datos = await respuesta.json().catch(() => ({}));
      detalle = typeof datos.detail === "string" ? datos.detail : JSON.stringify(datos.detail || detalle);
    }
    throw new Error(detalle);
  }
  return tipo.includes("json") ? respuesta.json() : respuesta;
}

function enviar(ruta, cuerpo) {
  return pedir(ruta, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpo || {}),
  });
}

function escapar(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fecha(iso) {
  if (!iso) return "—";
  const partes = String(iso).slice(0, 10).split("-");
  if (partes.length !== 3) return escapar(iso);
  return `${partes[2]}-${partes[1]}-${partes[0]}`;
}

function fechaLarga(iso) {
  if (!iso) return "—";
  const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  if (isNaN(d)) return escapar(iso);
  return `${DIAS[d.getDay()]} ${d.getDate()} ${MESES[d.getMonth()]} ${d.getFullYear()}`;
}

function sello(iso) {
  if (!iso) return "—";
  return `${fecha(iso)} ${String(iso).slice(11, 16)}`;
}

function tabla(cabeceras, filas) {
  if (!filas.length) return '<p class="vacio">nada que mostrar.</p>';
  return `<table><thead><tr>${cabeceras.map((c) => `<th>${escapar(c)}</th>`).join("")}</tr></thead>
    <tbody>${filas.map((fila) => `<tr>${fila.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function campo({ nombre, etiqueta, tipo = "text", valor = "", requerido = false, ayuda = "", opciones = null, paso = null }) {
  const atributos = [
    `name="${escapar(nombre)}"`,
    tipo === "select" ? "" : `type="${escapar(tipo)}"`,
    requerido ? "required" : "",
    paso ? `step="${escapar(paso)}"` : "",
    valor === null || valor === undefined ? "" : `value="${escapar(valor)}"`,
  ].filter(Boolean).join(" ");
  const control = tipo === "select"
    ? `<select ${atributos}>${opciones.map((o) =>
        `<option value="${escapar(o[0])}"${String(o[0]) === String(valor) ? " selected" : ""}>${escapar(o[1])}</option>`).join("")}</select>`
    : `<input ${atributos} />`;
  return `<label>${escapar(etiqueta)}${control}${ayuda ? `<span class="ayuda">${escapar(ayuda)}</span>` : ""}</label>`;
}

function leer(formulario) {
  const datos = Object.fromEntries(new FormData(formulario).entries());
  Object.keys(datos).forEach((clave) => {
    const valor = datos[clave];
    if ((clave === "dias" || clave.endsWith("_id") || clave === "meses" || clave === "puerto") && valor !== "") {
      datos[clave] = Number(valor);
    }
  });
  return datos;
}

function respuestaCaja(caja, contenido, tipo = "ok") {
  caja.innerHTML = `<div class="aviso ${tipo}">${contenido}</div>`;
}

async function alGuardar(formulario, caja, ruta, construir, alTerminar) {
  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    const boton = formulario.querySelector("button");
    boton.disabled = true;
    caja.innerHTML = '<p class="cargando">guardando…</p>';
    try {
      const resultado = await enviar(ruta, construir(leer(formulario)));
      respuestaCaja(caja, resultado.detalle || "listo.");
      if (alTerminar) alTerminar(resultado);
    } catch (error) {
      respuestaCaja(caja, escapar(error.message), "error");
    } finally {
      boton.disabled = false;
    }
  });
}

async function alTocar(selector, ruta, alTerminar = null, cuerpo = null) {
  document.querySelectorAll(selector).forEach((boton) => {
    boton.addEventListener("click", async () => {
      boton.disabled = true;
      try {
        const resultado = await enviar(ruta(boton.dataset), cuerpo ? cuerpo(boton.dataset) : {});
        if (alTerminar) alTerminar(resultado, boton);
        else pintar(estado.vista);
      } catch (error) {
        boton.disabled = false;
        alert(`no se pudo: ${error.message}`);
      }
    });
  });
}

function cargarCausas() {
  if (estado.causas.length) return Promise.resolve(estado.causas);
  return pedir("/api/causas").then((causas) => {
    estado.causas = causas;
    return causas;
  });
}

function cargarClientes() {
  if (estado.clientes.length) return Promise.resolve(estado.clientes);
  return pedir("/api/clientes").then((clientes) => {
    estado.clientes = clientes;
    return clientes;
  });
}

/* ------------------------------------------------------------------- vistas */

const vistas = {
  /* ---------------------------------------------------------------- inicio */
  inicio: {
    async pintar() {
      const [panel, seguridad, avisos] = await Promise.all([
        pedir("/api/panel"),
        hayPermiso("usuario.gestionar") ? pedir("/api/seguridad") : Promise.resolve(null),
        hayPermiso("aviso.gestionar") ? pedir("/api/avisos") : Promise.resolve(null),
      ]);
      const bloques = [];

      bloques.push(`
        <div class="tarjeta"><h3>Causas por estado</h3>
          ${tabla(["Estado", "Causas"], panel.causas_por_estado.map((f) => [escapar(f.estado_procesal), f.total]))}
        </div>
        <div class="tarjeta"><h3>Carga por abogado</h3>
          ${tabla(["Abogado", "Rol", "Causas"], panel.carga_por_abogado.map((f) => [escapar(f.nombre), escapar(f.rol), f.causas]))}
        </div>`);

      if (seguridad) {
        const sin2fa = seguridad.cuentas.filter((c) => c.esperado && !c.totp_activo);
        const bloqueadas = seguridad.cuentas.filter((c) => c.bloqueo && c.bloqueo.bloqueada);
        bloques.push(`
          <div class="tarjeta"><h3>Seguridad</h3>
            <p class="meta">intentos fallidos en ${seguridad.intentos.ventana_minutos} minutos:
              <strong>${seguridad.intentos.total}</strong>${seguridad.intentos.sospechoso ? ' <span class="etiqueta fatal">pasó el umbral</span>' : ""}</p>
            ${sin2fa.length
              ? `<p class="meta">sin segundo factor: ${sin2fa.map((c) => escapar(c.nombre) + " (" + escapar(c.rol) + ")").join(", ")}</p>
                 <button class="accion" data-vista-ir="seguridad">activar en Seguridad</button>`
              : '<p class="meta">socios y administradores con segundo factor: sí.</p>'}
            ${bloqueadas.length ? `<p class="meta">cuentas bloqueadas ahora: ${bloqueadas.map((c) => escapar(c.email)).join(", ")}</p>` : ""}
          </div>`);
      }

      if (avisos) {
        const pendientes = avisos.estado.conteos.pendiente || 0;
        const fallidas = avisos.estado.conteos.fallida || 0;
        bloques.push(`
          <div class="tarjeta"><h3>Avisos</h3>
            <p class="meta">en cola: <strong>${pendientes}</strong> · enviados: ${avisos.estado.conteos.enviada || 0}`
            + (fallidas ? ` · <span class="etiqueta fatal">fallidos: ${fallidas}</span>` : "") + `</p>
            <p class="meta">ventana de aviso: ${avisos.ajustes.dias_de_aviso} días antes · canales: ${escapar((avisos.ajustes.canales_por_defecto || []).join(", "))}</p>
            ${avisos.estado.ultimas_fallas.length
              ? `<p class="meta">último problema: ${escapar(avisos.estado.ultimas_fallas[0].canal)} a ${escapar(avisos.estado.ultimas_fallas[0].destino)} — ${escapar(avisos.estado.ultimas_fallas[0].ultimo_error)}</p>`
              : ""}
            <button class="accion" data-vista-ir="avisos">abrir Avisos</button>
          </div>`);
      }
      return bloques.join("");
    },
    enlazar() {
      document.querySelectorAll("[data-vista-ir]").forEach((boton) => {
        boton.addEventListener("click", () => irA(boton.dataset.vistaIr));
      });
    },
  },

  /* ---------------------------------------------------------------- plazos */
  vencimientos: {
    async pintar() {
      const plazos = await pedir("/api/plazos?dias=60");
      if (!plazos.length) return '<p class="vacio">Sin plazos pendientes en 60 días.</p>';
      return plazos.map((p) => `
        <div class="tarjeta">
          <h3>${escapar(p.descripcion)}</h3>
          <div class="meta">${escapar(p.caratula)} · RIT ${escapar(p.rol_rit ?? "s/informar")}</div>
          <div class="meta">vence ${fechaLarga(p.fecha_vencimiento)} (${escapar(p.dias || "-")} días hábiles desde ${fecha(p.fecha_notificacion)})</div>
          <div style="margin-top:6px">${etiquetaPlazo(p)}
            <button class="accion" data-cumplido="${p.id}" style="float:right">marcar cumplido</button>
          </div>
        </div>`).join("");
    },
    enlazar(refrescar) {
      document.querySelectorAll("[data-cumplido]").forEach((boton) => {
        boton.addEventListener("click", async () => {
          boton.disabled = true;
          try {
            await enviar(`/api/plazos/${boton.dataset.cumplido}/cumplido`);
            refrescar();
          } catch (error) {
            boton.disabled = false;
            alert(`no se pudo marcar: ${error.message}`);
          }
        });
      });
    },
  },

  /* ---------------------------------------------------------------- agenda */
  agenda: {
    async pintar() {
      const [audiencias, causas] = await Promise.all([pedir("/api/agenda?dias=60"), cargarCausas()]);
      const opciones = causas.map((c) => [c.id, `${c.caratula} — ${c.rol_rit || "s/RIT"}`]);
      const hoy = new Date().toISOString().slice(0, 10);
      const campos = [
        campo({ nombre: "causa_id", etiqueta: "Causa", tipo: "select", opciones, requerido: true }),
        campo({ nombre: "tipo", etiqueta: "Tipo", valor: "preparatoria", requerido: true, ayuda: "preparatoria, juicio, audiencia de prueba…" }),
        campo({ nombre: "fecha", etiqueta: "Fecha", tipo: "date", valor: hoy, requerido: true }),
        campo({ nombre: "hora", etiqueta: "Hora", tipo: "time", valor: "09:00" }),
        campo({ nombre: "modalidad", etiqueta: "Modalidad", tipo: "select",
               opciones: [["presencial", "presencial"], ["remota", "remota"], ["mixta", "mixta"]] }),
        campo({ nombre: "lugar_o_url", etiqueta: "Lugar o enlace", ayuda: "sala, dirección o URL de la videollamada" }),
        campo({ nombre: "minuta", etiqueta: "Minuta", ayuda: "qué hay que llevar o preparar" }),
      ];
      return `
        <div class="rejilla">
          <div class="tarjeta"><h3>Próximas audiencias</h3>
            ${tabla(["Fecha", "Hora", "Causa", "Tipo", "Modalidad"],
              audiencias.map((a) => [fechaLarga(a.fecha), escapar(a.hora ?? "—"), escapar(a.caratula),
                                     escapar(a.tipo), escapar(a.modalidad ?? "—")]))}
          </div>
          <div class="tarjeta"><h3>Agendar audiencia</h3>
            <form id="form-audiencia">${campos.join("")}
              <button class="accion" type="submit">Agendar</button></form>
            <div id="caja-audiencia"></div>
            <p class="meta">Al agendarla, quien quede como responsable recibe el aviso.</p>
          </div>
        </div>`;
    },
    enlazar(refrescar) {
      const formulario = document.getElementById("form-audiencia");
      if (formulario) {
        alGuardar(formulario, document.getElementById("caja-audiencia"), "/api/audiencias",
          (datos) => ({ ...datos, hora: datos.hora || null }), refrescar);
      }
    },
  },

  /* ---------------------------------------------------------------- causas */
  causas: {
    async pintar() {
      const [causas, clientes] = await Promise.all([pedir("/api/causas"), cargarClientes()]);
      const opciones = [["", "— sin cliente —"]].concat(clientes.map((c) => [c.id, `${c.nombre}${c.rut ? " · " + c.rut : ""}`]));
      const campos = [
        campo({ nombre: "caratula", etiqueta: "Carátula", requerido: true, ayuda: "Herrera con Fondo del Norte" }),
        campo({ nombre: "cliente_id", etiqueta: "Cliente", tipo: "select", opciones }),
        campo({ nombre: "rol_rit", etiqueta: "RIT / Rol", ayuda: "C-1234-2026" }),
        campo({ nombre: "tribunal", etiqueta: "Tribunal" }),
        campo({ nombre: "materia", etiqueta: "Materia", ayuda: "civil, laboral, familia, penal…" }),
        campo({ nombre: "observaciones", etiqueta: "Observaciones" }),
      ];
      return `
        <div class="rejilla">
          <div class="tarjeta"><h3>Causas del estudio</h3>
            ${causas.map((c) => `
              <div class="fila">
                <div>
                  <strong>${escapar(c.caratula)}</strong>
                  <div class="meta">${escapar(c.tribunal ?? "tribunal s/informar")} · RIT ${escapar(c.rol_rit ?? "s/informar")} · ${escapar(c.estado_procesal)}</div>
                  <div class="meta">equipo: ${escapar((c.equipo || []).join(", ") || "sin asignar")}</div>
                  ${c.proximo_vencimiento ? `<div class="meta">próximo: ${escapar(c.proximo_vencimiento.descripcion)} — ${fecha(c.proximo_vencimiento.fecha_vencimiento)}</div>` : ""}
                </div>
                <button class="accion" data-nuevo-plazo="${c.id}">+ plazo</button>
              </div>`).join("") || '<p class="vacio">sin causas registradas.</p>'}
          </div>
          <div class="tarjeta"><h3>Nueva causa</h3>
            <form id="form-causa">${campos.join("")}<button class="accion" type="submit">Crear causa</button></form>
            <div id="caja-causa"></div>
          </div>
        </div>`;
    },
    enlazar(refrescar) {
      const formulario = document.getElementById("form-causa");
      if (formulario) {
        alGuardar(formulario, document.getElementById("caja-causa"), "/api/causas",
          (datos) => ({ ...datos, cliente_id: datos.cliente_id || null }), refrescar);
      }
      document.querySelectorAll("[data-nuevo-plazo]").forEach((boton) => {
        boton.addEventListener("click", () => irA("nuevoPlazo", { causa: Number(boton.dataset.nuevoPlazo) }));
      });
    },
  },

  /* -------------------------------------------------------------- clientes */
  clientes: {
    async pintar() {
      const [clientes, causas] = await Promise.all([pedir("/api/clientes"), cargarCausas()]);
      return `
        <div class="rejilla">
          <div class="tarjeta">
            <h3>Clientes</h3>
            <input id="buscar-cliente" placeholder="buscar por nombre, RUT o correo" />
            <div id="lista-clientes">${tabla(["Nombre", "RUT", "Correo", "Teléfono", "Causas"],
              clientes.map((c) => [escapar(c.nombre), escapar(c.rut ?? "—"), escapar(c.email ?? "—"),
                                   escapar(c.telefono ?? "—"),
                                   causas.filter((x) => x.cliente_id === c.id).length]))}</div>
          </div>
          <div class="tarjeta"><h3>Nuevo cliente</h3>
            <form id="form-cliente">
              ${campo({ nombre: "nombre", etiqueta: "Nombre o razón social", requerido: true })}
              ${campo({ nombre: "rut", etiqueta: "RUT", ayuda: "12.345.678-9" })}
              ${campo({ nombre: "tipo_persona", etiqueta: "Tipo", tipo: "select",
                        opciones: [["natural", "persona natural"], ["juridica", "persona jurídica"]] })}
              ${campo({ nombre: "email", etiqueta: "Correo", tipo: "email" })}
              ${campo({ nombre: "telefono", etiqueta: "Teléfono" })}
              ${campo({ nombre: "direccion", etiqueta: "Domicilio", ayuda: "dato personal: sólo lo necesario" })}
              <button class="accion" type="submit">Crear cliente</button>
            </form>
            <div id="caja-cliente"></div>
          </div>
        </div>`;
    },
    enlazar(refrescar) {
      const formulario = document.getElementById("form-cliente");
      if (formulario) {
        alGuardar(formulario, document.getElementById("caja-cliente"), "/api/clientes", (datos) => datos, refrescar);
      }
      const buscador = document.getElementById("buscar-cliente");
      if (buscador) {
        let temporizador = null;
        buscador.addEventListener("input", () => {
          clearTimeout(temporizador);
          temporizador = setTimeout(async () => {
            const lista = document.getElementById("lista-clientes");
            const texto = buscador.value.trim();
            if (!texto) { lista.innerHTML = ""; return; }
            try {
              const encontrados = await pedir(`/api/clientes?q=${encodeURIComponent(texto)}`);
              lista.innerHTML = tabla(["Nombre", "RUT", "Correo", "Teléfono"],
                encontrados.map((c) => [escapar(c.nombre), escapar(c.rut ?? "—"),
                                        escapar(c.email ?? "—"), escapar(c.telefono ?? "—")]));
            } catch (error) {
              lista.innerHTML = `<p class="vacio">${escapar(error.message)}</p>`;
            }
          }, 300);
        });
      }
    },
  },

  /* ---------------------------------------------------------------- avisos */
  avisos: {
    async pintar() {
      const datos = await pedir("/api/avisos");
      const correo = datos.formulario.email || {};
      const sms = datos.formulario.sms || {};
      const cola = datos.cola;
      return `
        <div class="tarjeta">
          <h3>Cómo está el circuito</h3>
          ${tabla(["Canal", "Estado", "Detalle"], datos.canales.map((c) => [
            escapar(c.canal),
            c.listo ? '<span class="etiqueta ok">listo</span>' : '<span class="etiqueta fatal">falta</span>',
            escapar(c.detalle)]))}
          <p class="meta">archivo de configuración: <code>${escapar(datos.archivo)}</code> (permisos 600, las claves no se muestran nunca)</p>
        </div>

        <div class="rejilla">
          <div class="tarjeta"><h3>Correo (SMTP)</h3>
            <form id="form-correo">
              ${campo({ nombre: "host", etiqueta: "Servidor", valor: correo.host || "", ayuda: "smtp.estudio.cl" })}
              ${campo({ nombre: "puerto", etiqueta: "Puerto", tipo: "number", valor: correo.puerto || 587 })}
              ${campo({ nombre: "usuario", etiqueta: "Usuario", valor: correo.usuario || "", ayuda: "avisos@estudio.cl" })}
              ${campo({ nombre: "clave", etiqueta: "Clave", tipo: "password", ayuda: "clave de aplicación del proveedor; si la dejas vacía se conserva la que está guardada" })}
              ${campo({ nombre: "de", etiqueta: "Remitente", valor: correo.de || "", ayuda: "Estudio Soto <avisos@estudio.cl>" })}
              ${campo({ nombre: "seguridad", etiqueta: "Cifrado", tipo: "select", valor: correo.seguridad || "starttls",
                        opciones: [["starttls", "STARTTLS (recomendado)"], ["ninguna", "sin cifrado (sólo relay local)"]] })}
              <button class="accion" type="submit">Guardar correo</button>
            </form>
            <div id="caja-correo"></div>
          </div>

          <div class="tarjeta"><h3>SMS</h3>
            <form id="form-sms">
              ${campo({ nombre: "proveedor", etiqueta: "Proveedor", tipo: "select", valor: sms.proveedor || "consola",
                        opciones: [["consola", "consola (prueba, no envía)"], ["twilio", "Twilio"], ["webhook", "webhook propio"]] })}
              ${campo({ nombre: "cuenta", etiqueta: "Cuenta (Twilio)", valor: sms.cuenta || "" })}
              ${campo({ nombre: "token", etiqueta: "Token", tipo: "password", ayuda: "si lo dejas vacío se conserva el guardado" })}
              ${campo({ nombre: "webhook", etiqueta: "URL del webhook", valor: sms.webhook || "" })}
              ${campo({ nombre: "de", etiqueta: "Número o nombre de origen", valor: sms.de || "" })}
              <button class="accion" type="submit">Guardar SMS</button>
            </form>
            <div id="caja-sms"></div>
          </div>
        </div>

        <div class="tarjeta"><h3>Cuándo avisar</h3>
          <form id="form-ajustes">
            ${campo({ nombre: "dias_de_aviso", etiqueta: "Días de anticipación", tipo: "number",
                      valor: datos.ajustes.dias_de_aviso, ayuda: "con cuántos días antes empieza a avisar un plazo" })}
            ${campo({ nombre: "canales", etiqueta: "Canales por defecto", valor: (datos.ajustes.canales_por_defecto || []).join(","),
                      ayuda: "email, sms, consola — separados por coma" })}
            ${campo({ nombre: "asignaciones", etiqueta: "Avisar cuando le asignan un plazo o audiencia a alguien",
                      tipo: "select", valor: datos.ajustes.avisar_asignaciones ? "si" : "no",
                      opciones: [["si", "sí"], ["no", "no"]] })}
            <button class="accion" type="submit">Guardar ajustes</button>
          </form>
          <div id="caja-ajustes"></div>
        </div>

        <div class="tarjeta"><h3>Probar y despachar</h3>
          <p class="meta">La prueba sale ahora mismo, a tu propio correo o teléfono.</p>
          <button class="accion" data-probar="email">probar correo</button>
          <button class="accion" data-probar="sms">probar SMS</button>
          <button class="accion" data-probar="consola">probar sin enviar</button>
          <button class="accion" id="generar-avisos">generar recordatorios</button>
          <button class="accion" id="despachar-avisos">despachar la cola</button>
          <div id="caja-avisos"></div>
        </div>

        <div class="tarjeta"><h3>Cola de avisos (últimos 25)</h3>
          ${tabla(["#", "Canal", "Destino", "Asunto", "Estado", "Intentos", "Error"],
            cola.map((f) => [f.id, escapar(f.canal), escapar(f.destino), escapar((f.asunto || "").slice(0, 60)),
                             `<span class="etiqueta ${f.estado === "enviada" ? "ok" : f.estado === "fallida" ? "fatal" : "pronto"}">${escapar(f.estado)}</span>`,
                             f.intentos, escapar((f.ultimo_error || "").slice(0, 80))]))}
        </div>`;
    },
    enlazar(refrescar) {
      const caja = (id) => document.getElementById(id);
      const correo = document.getElementById("form-correo");
      alGuardar(correo, caja("caja-correo"), "/api/avisos/config", (datos) => ({
        email: { host: datos.host, puerto: datos.puerto, usuario: datos.usuario, clave: datos.clave,
                 de: datos.de, seguridad: datos.seguridad },
      }), refrescar);

      const sms = document.getElementById("form-sms");
      alGuardar(sms, caja("caja-sms"), "/api/avisos/config", (datos) => ({
        sms: { proveedor: datos.proveedor, cuenta: datos.cuenta, token: datos.token,
               webhook: datos.webhook, de: datos.de },
      }), refrescar);

      const ajustes = document.getElementById("form-ajustes");
      alGuardar(ajustes, caja("caja-ajustes"), "/api/avisos/config", (datos) => ({
        dias_de_aviso: Number(datos.dias_de_aviso),
        canales_por_defecto: String(datos.canales).split(",").map((c) => c.trim()).filter(Boolean),
        avisar_asignaciones: datos.asignaciones === "si",
      }), refrescar);

      document.querySelectorAll("[data-probar]").forEach((boton) => {
        boton.addEventListener("click", async () => {
          boton.disabled = true;
          caja("caja-avisos").innerHTML = '<p class="cargando">probando…</p>';
          try {
            const resultado = await enviar("/api/avisos/probar", { canal: boton.dataset.probar });
            respuestaCaja(caja("caja-avisos"), resultado.enviado
              ? `salió por ${escapar(resultado.canal)} a ${escapar(resultado.destino)}.`
              : `no salió: ${escapar(resultado.error || "")}`,
              resultado.enviado ? "ok" : "error");
          } catch (error) {
            respuestaCaja(caja("caja-avisos"), escapar(error.message), "error");
          } finally {
            boton.disabled = false;
          }
        });
      });

      const generar = document.getElementById("generar-avisos");
      generar.addEventListener("click", async () => {
        try {
          const r = await enviar("/api/avisos/generar");
          respuestaCaja(caja("caja-avisos"),
            `revisé ${r.plazos_revisados} plazo(s) y ${r.audiencias_revisadas} audiencia(s) con ${r.dias_de_aviso} días de anticipación: `
            + `encolados ${r.encoladas}, ya estaban ${r.repetidas}` + (r.sin_destino ? `, sin destino ${r.sin_destino}` : "") + ".");
          refrescar();
        } catch (error) {
          respuestaCaja(caja("caja-avisos"), escapar(error.message), "error");
        }
      });

      const despachar = document.getElementById("despachar-avisos");
      despachar.addEventListener("click", async () => {
        try {
          const r = await enviar("/api/avisos/despachar");
          respuestaCaja(caja("caja-avisos"),
            `revisados ${r.revisadas} · enviados ${r.enviadas} · fallidos ${r.fallidas}`
            + (r.errores.length ? `<br>${r.errores.map((e) => escapar(`${e.canal}: ${e.error}`)).join("<br>")}` : ""));
          refrescar();
        } catch (error) {
          respuestaCaja(caja("caja-avisos"), escapar(error.message), "error");
        }
      });
    },
  },

  /* -------------------------------------------------------------- usuarios */
  usuarios: {
    async pintar() {
      const datos = await pedir("/api/usuarios");
      const opcionesRol = datos.roles.map((r) => [r, r]);
      return `
        <div class="tarjeta"><h3>Personas del estudio</h3>
          ${tabla(["Nombre", "Correo", "Rol", "Teléfono", "Segundo factor", "Acceso", "Bloqueo", ""],
            datos.usuarios.map((u) => {
              const bloqueo = datos.bloqueos[u.email] || {};
              return [
                escapar(u.nombre), escapar(u.email), escapar(u.rol), escapar(u.telefono || "—"),
                u.totp_activo ? '<span class="etiqueta ok">activo</span>' : '<span class="etiqueta pronto">no</span>',
                u.activo ? "sí" : '<span class="etiqueta fatal">inactivo</span>',
                bloqueo.bloqueada ? `<span class="etiqueta fatal">${bloqueo.faltan_minutos} min</span>`
                                  : '<span class="etiqueta ok">—</span>',
                `<button class="accion mini" data-editar="${u.id}">editar</button>`,
              ];
            }))}
        </div>
        <div class="rejilla">
          <div class="tarjeta"><h3>Nueva persona</h3>
            <form id="form-usuario">
              ${campo({ nombre: "nombre", etiqueta: "Nombre", requerido: true })}
              ${campo({ nombre: "email", etiqueta: "Correo", tipo: "email", requerido: true })}
              ${campo({ nombre: "rol", etiqueta: "Rol", tipo: "select", opciones: opcionesRol })}
              ${campo({ nombre: "telefono", etiqueta: "Teléfono", ayuda: "para los avisos por SMS" })}
              ${campo({ nombre: "password", etiqueta: "Contraseña", tipo: "password", requerido: true,
                        ayuda: "al menos 10 caracteres; se guarda cifrada" })}
              <button class="accion" type="submit">Crear usuario</button>
            </form>
            <div id="caja-usuario"></div>
            <p class="meta">Los roles: el socio ve todo y administra; el abogado ve sus causas;
              la secretaría agenda y factura; el cliente sólo su causa.</p>
          </div>
          <div class="tarjeta" id="panel-editar"><h3>Editar una persona</h3>
            <p class="meta">Elige «editar» en la lista de arriba.</p>
          </div>
        </div>`;
    },
    enlazar(refrescar) {
      const formulario = document.getElementById("form-usuario");
      alGuardar(formulario, document.getElementById("caja-usuario"), "/api/usuarios", (datos) => datos, refrescar);

      document.querySelectorAll("[data-editar]").forEach((boton) => {
        boton.addEventListener("click", async () => {
          const id = Number(boton.dataset.editar);
          const datos = await pedir("/api/usuarios");
          const persona = datos.usuarios.find((u) => u.id === id);
          const destino = document.getElementById("panel-editar");
          destino.innerHTML = `
            <h3>${escapar(persona.nombre)}</h3>
            <p class="meta">${escapar(persona.email)}</p>
            <form id="form-editar">
              ${campo({ nombre: "rol", etiqueta: "Rol", tipo: "select", valor: persona.rol,
                        opciones: datos.roles.map((r) => [r, r]) })}
              ${campo({ nombre: "telefono", etiqueta: "Teléfono", valor: persona.telefono || "" })}
              ${campo({ nombre: "password", etiqueta: "Nueva contraseña", tipo: "password",
                        ayuda: "déjala vacía para no cambiarla" })}
              ${campo({ nombre: "activo", etiqueta: "Acceso", tipo: "select", valor: persona.activo ? "si" : "no",
                        opciones: [["si", "puede entrar"], ["no", "acceso cortado"]] })}
              <button class="accion" type="submit">Guardar cambios</button>
            </form>
            <div id="caja-editar"></div>`;
          const editar = document.getElementById("form-editar");
          alGuardar(editar, document.getElementById("caja-editar"), `/api/usuarios/${id}`, (campos) => ({
            rol: campos.rol,
            telefono: campos.telefono,
            password: campos.password || null,
            activo: campos.activo === "si",
          }), refrescar);
        });
      });
    },
  },

  /* ------------------------------------------------------------- seguridad */
  seguridad: {
    async pintar() {
      const datos = await pedir("/api/seguridad");
      const intentos = datos.intentos;
      return `
        <div class="tarjeta"><h3>Accesos</h3>
          <p class="meta">intentos fallidos en los últimos ${intentos.ventana_minutos} minutos: <strong>${intentos.total}</strong>
            ${intentos.sospechoso ? '<span class="etiqueta fatal">pasó el umbral de ' + intentos.umbral + "</span>" : ""}</p>
          ${Object.keys(intentos.por_cuenta).length
            ? tabla(["Cuenta", "Intentos fallidos"], Object.entries(intentos.por_cuenta).map(([c, n]) => [escapar(c), n]))
            : ""}
          <p class="meta">A los ${datos.umbrales.bloqueo_intentos} intentos fallidos, la cuenta queda bloqueada
            ${datos.umbrales.bloqueo_minutos} minutos — también con la contraseña correcta.</p>
        </div>

        <div class="tarjeta"><h3>Segundo factor y bloqueos</h3>
          ${tabla(["Persona", "Rol", "Segundo factor", "Bloqueo", ""],
            datos.cuentas.map((c) => [
              escapar(c.nombre), escapar(c.rol),
              c.totp_activo ? '<span class="etiqueta ok">activo</span>'
                            : (c.esperado ? '<span class="etiqueta fatal">falta</span>' : '<span class="etiqueta pronto">no</span>'),
              c.bloqueo && c.bloqueo.bloqueada
                ? `<span class="etiqueta fatal">${c.bloqueo.faltan_minutos} min</span>` : '<span class="etiqueta ok">—</span>',
              `<span class="acciones">
                 ${!c.totp_activo ? `<button class="accion mini" data-2fa="${escapar(c.email)}">activar</button>` : ""}
                 ${c.totp_activo ? `<button class="accion mini" data-apagar="${escapar(c.email)}">apagar</button>` : ""}
                 ${c.bloqueo && c.bloqueo.bloqueada ? `<button class="accion mini" data-desbloquear="${escapar(c.email)}">destrabar</button>` : ""}
               </span>`,
            ]))}
        </div>
        <div id="panel-2fa"></div>`;
    },
    enlazar(refrescar) {
      const panel = document.getElementById("panel-2fa");

      document.querySelectorAll("[data-2fa]").forEach((boton) => {
        boton.addEventListener("click", async () => {
          try {
            const alta = await enviar("/api/seguridad/2fa/preparar", { email: boton.dataset["2fa"] });
            panel.innerHTML = `
              <div class="tarjeta">
                <h3>Segundo factor para ${escapar(alta.email)}</h3>
                <p class="meta">Que abra su app de autenticación y cargue esta clave (o el enlace). Después hay que confirmar
                  con el código de 6 dígitos que muestre la app: hasta entonces no se activa nada.</p>
                <p class="clave">${escapar((alta.secreto.match(/.{1,4}/g) || []).join(" "))}</p>
                <p class="meta"><code>${escapar(alta.uri)}</code></p>
                <form id="form-2fa">
                  ${campo({ nombre: "codigo", etiqueta: "Código de 6 dígitos", requerido: true })}
                  <button class="accion" type="submit">Confirmar y activar</button>
                </form>
                <div id="caja-2fa"></div>
              </div>`;
            const formulario = document.getElementById("form-2fa");
            alGuardar(formulario, document.getElementById("caja-2fa"), "/api/seguridad/2fa/confirmar",
              (datos) => ({ email: alta.email, codigo: datos.codigo }),
              () => setTimeout(refrescar, 700));
          } catch (error) {
            panel.innerHTML = `<div class="aviso error">${escapar(error.message)}</div>`;
          }
        });
      });

      document.querySelectorAll("[data-apagar]").forEach((boton) => {
        boton.addEventListener("click", async () => {
          const motivo = prompt("¿Por qué se apaga el segundo factor? (queda en la bitácora)");
          if (!motivo) return;
          try {
            await enviar("/api/seguridad/2fa/apagar", { email: boton.dataset.apagar, motivo });
            refrescar();
          } catch (error) {
            alert(`no se pudo: ${error.message}`);
          }
        });
      });

      document.querySelectorAll("[data-desbloquear]").forEach((boton) => {
        boton.addEventListener("click", async () => {
          try {
            await enviar("/api/seguridad/desbloquear", { email: boton.dataset.desbloquear });
            refrescar();
          } catch (error) {
            alert(`no se pudo: ${error.message}`);
          }
        });
      });
    },
  },

  /* ------------------------------------------------------------- retención */
  retencion: {
    async pintar() {
      const datos = await pedir("/api/retencion");
      const politica = datos.politica || {};
      const tipos = datos.tipos || Object.keys(politica);
      const filas = tipos.map((tipo) => {
        const fila = politica[tipo] || {};
        const declarado = fila.origen === "declarada por el estudio";
        return [escapar(tipo), `${fila.meses} mes(es)`, escapar(fila.motivo || "—"),
                declarado ? '<span class="etiqueta ok">declarado</span>' : '<span class="etiqueta pronto">sugerencia</span>'];
      });
      return `
        <div class="tarjeta"><h3>Cuánto se conserva cada dato</h3>
          ${tabla(["Tipo de dato", "Plazo", "Motivo", "Origen"], filas)}
          <p class="meta">Lo que trae el sistema son sugerencias con su anclaje. El estudio declara sus plazos:
            el CRM no inventa obligaciones de borrar.</p>
        </div>

        <div class="rejilla">
          <div class="tarjeta"><h3>Declarar un plazo</h3>
            <form id="form-politica">
              ${campo({ nombre: "tipo", etiqueta: "Tipo de dato", tipo: "select", opciones: tipos.map((t) => [t, t]) })}
              ${campo({ nombre: "meses", etiqueta: "Meses de conservación", tipo: "number", valor: 60, requerido: true })}
              ${campo({ nombre: "motivo", etiqueta: "Motivo", requerido: true,
                        ayuda: "por qué se conserva ese tiempo (queda en la bitácora)" })}
              <button class="accion" type="submit">Guardar plazo</button>
            </form>
            <div id="caja-politica"></div>
          </div>

          <div class="tarjeta"><h3>Qué corresponde hacer</h3>
            <p class="meta">corte: datos sin movimiento desde hace <strong>${escapar(datos.corte_meses)}</strong> meses
              · cumplidos: <strong>${escapar(datos.total)}</strong></p>
            ${datos.cumplidos.length
              ? tabla(["Cliente", "Causas", "Último movimiento", "Días"],
                  datos.cumplidos.map((c) => [escapar(c.nombre), c.causas, fecha(c.ultimo_movimiento), c.dias_sin_movimiento]))
              : '<p class="vacio">Ningún expediente cumplió su plazo todavía.</p>'}
          </div>
        </div>

        <div class="tarjeta"><h3>Ejecutar</h3>
          <p class="meta">Primero mirar (no cambia nada), después ejecutar. Se anonimizan los identificadores:
            los honorarios, los gastos, la bitácora y las pruebas de tratamiento de IA se conservan — son los que
            permiten responder por lo que se hizo.</p>
          <form id="form-retencion">
            ${campo({ nombre: "motivo", etiqueta: "Motivo", requerido: true })}
            ${campo({ nombre: "modo", etiqueta: "Modo", tipo: "select",
                      opciones: [["simular", "ver qué se haría (no cambia nada)"], ["ejecutar", "ejecutar de verdad"]] })}
            ${campo({ nombre: "confirmar", etiqueta: "Para ejecutar de verdad, escribe ANONIMIZAR",
                      ayuda: "si vas a simular, déjalo vacío" })}
            <button class="accion" type="submit">Continuar</button>
          </form>
          <div id="caja-retencion"></div>
        </div>`;
    },
    enlazar(refrescar) {
      alGuardar(document.getElementById("form-politica"), document.getElementById("caja-politica"),
        "/api/retencion", (datos) => ({ tipo: datos.tipo, meses: datos.meses, motivo: datos.motivo }), refrescar);

      const formulario = document.getElementById("form-retencion");
      formulario.addEventListener("submit", async (evento) => {
        evento.preventDefault();
        const datos = leer(formulario);
        const caja = document.getElementById("caja-retencion");
        if (datos.modo === "ejecutar" && datos.confirmar.trim().toUpperCase() !== "ANONIMIZAR") {
          respuestaCaja(caja, "para ejecutar de verdad hay que escribir ANONIMIZAR en la confirmación.", "error");
          return;
        }
        caja.innerHTML = '<p class="cargando">trabajando…</p>';
        try {
          const resultado = await enviar("/api/retencion/ejecutar", {
            motivo: datos.motivo, simular: datos.modo === "simular",
          });
          const hechos = resultado.anonimizados || [];
          respuestaCaja(caja, (resultado.simulado ? "<strong>Simulación</strong> — " : "<strong>Ejecutado</strong> — ")
            + (hechos.length
                ? hechos.map((h) => escapar(h.nombre) + (h.hecho ? " (anonimizado)" : " (se anonimizaría)")).join(", ")
                : "no hay expedientes que hayan cumplido su plazo."));
        } catch (error) {
          respuestaCaja(caja, escapar(error.message), "error");
        }
      });
    },
  },

  /* ------------------------------------------------------- datos del titular */
  datos: {
    async pintar() {
      return `
        <div class="tarjeta"><h3>Derechos del titular</h3>
          <p class="meta">El derecho de acceso (que le digan qué se guardó), el de portabilidad (que se lo lleven en un
            archivo) y el de supresión (que se borren sus identificadores). Queda todo en la bitácora, con el motivo.</p>
          <form id="form-buscar">
            ${campo({ nombre: "texto", etiqueta: "Buscar por nombre, RUT o correo", requerido: true })}
            <button class="accion" type="submit">Buscar</button>
          </form>
          <div id="resultado-titular"></div>
        </div>`;
    },
    enlazar() {
      const formulario = document.getElementById("form-buscar");
      formulario.addEventListener("submit", async (evento) => {
        evento.preventDefault();
        const caja = document.getElementById("resultado-titular");
        caja.innerHTML = '<p class="cargando">buscando…</p>';
        const texto = leer(formulario).texto;
        try {
          const encontrados = await pedir(`/api/titulares?texto=${encodeURIComponent(texto)}`);
          if (!encontrados.length) {
            caja.innerHTML = '<p class="vacio">ningún titular con ese criterio.</p>';
            return;
          }
          caja.innerHTML = encontrados.map((c) => `
            <div class="fila">
              <div>
                <strong>${escapar(c.nombre)}</strong>
                <div class="meta">${escapar(c.rut ?? "sin RUT")} · ${escapar(c.email ?? "sin correo")} · ${escapar(c.telefono ?? "sin teléfono")}</div>
              </div>
              <div class="acciones">
                <button class="accion mini" data-exportar="${escapar(c.rut || c.nombre)}" data-por="${c.rut ? "rut" : "nombre"}">entregar expediente</button>
                <button class="accion mini" data-simular="${escapar(c.rut || c.nombre)}" data-por="${c.rut ? "rut" : "nombre"}">ver anonimización</button>
                <button class="accion mini" data-anonimizar="${escapar(c.rut || c.nombre)}" data-por="${c.rut ? "rut" : "nombre"}">anonimizar</button>
              </div>
            </div>`).join("") + '<div id="caja-titular"></div>';

          const cajaInterna = () => document.getElementById("caja-titular");

          document.querySelectorAll("[data-exportar]").forEach((boton) => {
            boton.addEventListener("click", async () => {
              try {
                const respuesta = await fetch("/api/titulares/exportar", {
                  method: "POST", credentials: "same-origin",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ [boton.dataset.por]: boton.dataset.exportar }),
                });
                if (!respuesta.ok) throw new Error((await respuesta.json()).detail || `HTTP ${respuesta.status}`);
                const blob = await respuesta.blob();
                const enlace = document.createElement("a");
                enlace.href = URL.createObjectURL(blob);
                enlace.download = "expediente-titular.json";
                enlace.click();
                respuestaCaja(cajaInterna(), `expediente entregado · sha256 ${escapar(respuesta.headers.get("X-OpenLegal-Sha256") || "")}`);
              } catch (error) {
                respuestaCaja(cajaInterna(), escapar(error.message), "error");
              }
            });
          });

          document.querySelectorAll("[data-simular]").forEach((boton) => {
            boton.addEventListener("click", async () => {
              const motivo = prompt("Motivo (queda en la bitácora):", "solicitud del titular");
              if (!motivo) return;
              try {
                const resultado = await enviar("/api/titulares/anonimizar", {
                  [boton.dataset.por]: boton.dataset.simular, motivo, simular: true,
                });
                respuestaCaja(cajaInterna(), `se borrarían ${resultado.identificadores_borrados.length} identificador(es) y se redactaría el nombre en ${resultado.textos_redactados.length} texto(s). No se cambió nada.`);
              } catch (error) {
                respuestaCaja(cajaInterna(), escapar(error.message), "error");
              }
            });
          });

          document.querySelectorAll("[data-anonimizar]").forEach((boton) => {
            boton.addEventListener("click", async () => {
              const motivo = prompt("Motivo de la supresión (queda en la bitácora):");
              if (!motivo) return;
              if (prompt("Esto NO se puede deshacer. Escribe ANONIMIZAR para confirmar:") !== "ANONIMIZAR") return;
              try {
                const resultado = await enviar("/api/titulares/anonimizar", {
                  [boton.dataset.por]: boton.dataset.anonimizar, motivo, simular: false, confirmar: "ANONIMIZAR",
                });
                respuestaCaja(cajaInterna(), `anonimizado: ${resultado.identificadores_borrados.length} identificador(es) borrado(s), nombre redactado en ${resultado.textos_redactados.length} texto(s). El expediente y la bitácora quedan.`);
              } catch (error) {
                respuestaCaja(cajaInterna(), escapar(error.message), "error");
              }
            });
          });
        } catch (error) {
          caja.innerHTML = `<p class="vacio">${escapar(error.message)}</p>`;
        }
      });
    },
  },

  /* ------------------------------------------------------- nuevo plazo (form) */
  nuevoPlazo: {
    async pintar(datos = {}) {
      const causas = await cargarCausas();
      const opciones = causas.map((c) => [c.id, `${c.caratula} — ${c.rol_rit || "s/RIT"}`]);
      const hoy = new Date().toISOString().slice(0, 10);
      return `
        <div class="tarjeta">
          <h3>Nuevo plazo</h3>
          <form id="form-plazo">
            ${campo({ nombre: "causa_id", etiqueta: "Causa", tipo: "select", opciones, valor: datos.causa ?? "" })}
            ${campo({ nombre: "descripcion", etiqueta: "Descripción", requerido: true, ayuda: "Contestar traslado" })}
            ${campo({ nombre: "notificacion", etiqueta: "Fecha de notificación", tipo: "date", valor: hoy, requerido: true })}
            ${campo({ nombre: "dias", etiqueta: "Días hábiles", tipo: "number", valor: 8, requerido: true })}
            ${campo({ nombre: "es_fatal", etiqueta: "¿Fatal?", tipo: "select", opciones: [["si", "sí"], ["no", "no"]] })}
            <button class="accion" type="submit">Calcular y guardar</button>
          </form>
          <div id="resultado-plazo"></div>
        </div>`;
    },
    enlazar() {
      const formulario = document.getElementById("form-plazo");
      formulario.addEventListener("submit", async (evento) => {
        evento.preventDefault();
        const datos = leer(formulario);
        const caja = document.getElementById("resultado-plazo");
        caja.innerHTML = '<p class="cargando">calculando…</p>';
        try {
          const calculo = await pedir(`/api/calculo?notificacion=${datos.notificacion}&dias=${datos.dias}`);
          await enviar("/api/plazos", {
            causa_id: Number(datos.causa_id), descripcion: datos.descripcion,
            dias: Number(datos.dias), notificacion: datos.notificacion, es_fatal: datos.es_fatal === "si",
          });
          caja.innerHTML = `
            ${(calculo.advertencias || []).map((a) => `<div class="aviso">⚠️ ${escapar(a)}</div>`).join("")}
            <div class="resultado">vence el ${fechaLarga(calculo.fecha_vencimiento)}</div>
            ${tabla(["Fecha", "Día", "Cuenta", "Motivo"],
              calculo.detalle.map((d) => [fecha(d.fecha), escapar(d.dia),
                d.habil ? escapar(d.dia_contado) : "—", escapar(d.motivo || "")]))}`;
          estado.causas = [];
        } catch (error) {
          caja.innerHTML = `<p class="vacio">${escapar(error.message)}</p>`;
        }
      });
    },
  },
};

function etiquetaPlazo(plazo) {
  const restantes = plazo.dias_restantes;
  let clase = "ok";
  let texto = `${restantes} días`;
  if (restantes < 0) { clase = "fatal"; texto = `vencido hace ${-restantes} días`; }
  else if (restantes <= 3) { clase = "fatal"; }
  else if (restantes <= 7) { clase = "pronto"; }
  const fatal = plazo.es_fatal ? '<span class="etiqueta fatal">fatal</span>' : "";
  return `${fatal}<span class="etiqueta ${clase}">${escapar(texto)}</span>`;
}

function hayPermiso(permiso) {
  return estado.permisos.includes(permiso);
}

/* --------------------------------------------------------------- navegación */

async function pintar(nombre, datos = {}) {
  estado.vista = nombre;
  const contenedor = document.getElementById("vista");
  contenedor.innerHTML = '<p class="cargando">cargando…</p>';
  const vista = vistas[nombre];
  if (!vista) {
    contenedor.innerHTML = '<p class="vacio">esa vista no existe.</p>';
    return;
  }
  try {
    contenedor.innerHTML = await vista.pintar(datos);
    vista.enlazar(() => pintar(nombre));
  } catch (error) {
    contenedor.innerHTML = `<p class="vacio">${escapar(error.message)}</p>`;
    // El 401 en cualquier módulo significa que la sesión venció, no que el módulo falle.
    if (String(error.message).includes("sin sesión")) vistaLogin("Tu sesión terminó. Entra de nuevo.");
  }
}

function irA(nombre, datos = {}) {
  document.querySelectorAll(".pestana").forEach((b) => b.classList.toggle("activa", b.dataset.vista === nombre));
  pintar(nombre, datos);
}

function mostrarPestanas() {
  document.querySelectorAll(".pestana").forEach((boton) => {
    const permiso = boton.dataset.permiso;
    // Sin el permiso la pestaña no se muestra: es más honesto que un botón que da error.
    boton.style.display = !permiso || hayPermiso(permiso) ? "" : "none";
  });
}

/* ------------------------------------------------------------------- sesión */

function vistaLogin(mensaje) {
  document.getElementById("pestanas").style.display = "none";
  document.getElementById("sesion").innerHTML = "";
  document.getElementById("vista").innerHTML = `
    <form id="form-login" class="tarjeta" style="margin-top:14px">
      <h3>Entrar al CRM</h3>
      <p class="meta">${escapar(mensaje || "Usa tu correo del estudio y tu contraseña.")}</p>
      ${campo({ nombre: "email", etiqueta: "Correo", tipo: "email", requerido: true })}
      ${campo({ nombre: "password", etiqueta: "Contraseña", tipo: "password", requerido: true })}
      ${campo({ nombre: "codigo", etiqueta: "Código de verificación", ayuda: "sólo si tienes segundo factor" })}
      <button class="accion" type="submit">Entrar</button>
      <p class="meta" style="margin:8px 0 0">
        Si trabajas solo, no necesitas contraseña: abre el panel con la URL que imprime «openlegal serve».
      </p>
    </form>`;
  document.getElementById("form-login").addEventListener("submit", async (evento) => {
    evento.preventDefault();
    const formulario = evento.target;
    const datos = leer(formulario);
    const aviso = formulario.querySelector(".meta");
    try {
      const respuesta = await fetch("/api/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos),
      });
      if (!respuesta.ok) {
        const detalle = await respuesta.json().catch(() => ({}));
        throw new Error(detalle.detail || "no se pudo entrar");
      }
      location.reload();
    } catch (error) {
      aviso.textContent = `No se pudo entrar: ${error.message}`;
      aviso.classList.add("error");
    }
  });
}

function pintarSesion(info) {
  const caja = document.getElementById("sesion");
  const quien = info.usuario ? `${info.usuario.nombre} · ${info.usuario.rol}` : "modo token (abogado solo)";
  const salir = info.via === "sesion" ? '<button class="accion mini" id="salir">salir</button>' : "";
  caja.innerHTML = `<span class="meta">${escapar(quien)}</span> ${salir}`;
  const boton = document.getElementById("salir");
  if (boton) {
    boton.addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
      location.reload();
    });
  }
}

/* ------------------------------------------------------------------- arranque */

async function inicio() {
  let info = null;
  try {
    const respuesta = await fetch("/api/sesion", { credentials: "same-origin" });
    if (respuesta.status === 401) {
      vistaLogin();
      return;
    }
    info = await respuesta.json();
  } catch (error) {
    document.getElementById("estudio").textContent = "sin conexión con la API";
    return;
  }
  estado.permisos = info.permisos || [];
  document.getElementById("pestanas").style.display = "";
  document.getElementById("estudio").textContent = `${info.estudio.nombre} · ${info.estudio.modo}`;
  pintarSesion(info);
  mostrarPestanas();
  try {
    const datos = await pedir("/api/estado");
    document.getElementById("pie").textContent =
      `Open Legal Harness v${datos.version} · ${datos.motores} · ${datos.conteos.causas} causas, ${datos.conteos.plazos} plazos`;
  } catch (error) {
    document.getElementById("pie").textContent = "";
  }
  pintar("inicio");
}

document.getElementById("pestanas").addEventListener("click", (evento) => {
  const boton = evento.target.closest(".pestana");
  if (!boton) return;
  irA(boton.dataset.vista);
});

inicio();
