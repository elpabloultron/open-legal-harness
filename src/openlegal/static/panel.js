/* Panel del CRM: JS sin dependencias. Habla con la API local en el mismo origen. */

const estado = { causas: [], vista: "vencimientos" };

async function pedir(ruta, opciones) {
  const respuesta = await fetch(ruta, { credentials: "same-origin", ...opciones });
  if (!respuesta.ok) throw new Error(`${ruta}: HTTP ${respuesta.status}`);
  return respuesta.json();
}

function escapar(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function fecha(iso) {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}-${m}-${a}`;
}

function etiquetaPlazo(plazo) {
  const restantes = plazo.dias_restantes;
  let clase = "ok";
  let texto = `${restantes} días`;
  if (restantes < 0) { clase = "fatal"; texto = `vencido hace ${-restantes} días`; }
  else if (restantes <= 3) { clase = "fatal"; }
  else if (restantes <= 7) { clase = "pronto"; }
  const fatal = plazo.es_fatal ? '<span class="etiqueta fatal">fatal</span>' : "";
  const vencido = restantes < 0 ? '<span class="etiqueta fatal">vencido</span>' : "";
  return `${fatal}${vencido}<span class="etiqueta ${clase}">${escapar(texto)}</span>`;
}

const vistas = {
  async vencimientos() {
    const plazos = await pedir("/api/plazos?dias=60");
    if (!plazos.length) return '<p class="vacio">Sin plazos pendientes en 60 días.</p>';
    return plazos.map((p) => `
      <div class="tarjeta">
        <h3>${escapar(p.descripcion)}</h3>
        <div class="meta">${escapar(p.caratula)} · RIT ${escapar(p.rol_rit ?? "s/informar")}</div>
        <div class="meta">vence ${fecha(p.fecha_vencimiento)} (${escapar(p.dias || "-")} días hábiles desde ${fecha(p.fecha_notificacion)})</div>
        <div style="margin-top:5px">${etiquetaPlazo(p)}
          <button class="accion" data-cumplido="${p.id}" style="float:right">marcar cumplido</button>
        </div>
      </div>`).join("");
  },

  async causas() {
    const causas = await pedir("/api/causas");
    if (!causas.length) return '<p class="vacio">Sin causas registradas.</p>';
    return causas.map((c) => {
      const proximo = c.proximo_vencimiento
        ? `<div class="meta">próximo: ${escapar(c.proximo_vencimiento.descripcion)} — ${fecha(c.proximo_vencimiento.fecha_vencimiento)}</div>`
        : "";
      return `
      <div class="tarjeta">
        <h3>${escapar(c.caratula)}</h3>
        <div class="meta">${escapar(c.tribunal ?? "tribunal s/informar")} · RIT ${escapar(c.rol_rit ?? "s/informar")}</div>
        <div class="meta">${escapar(c.materia ?? "s/materia")} · ${escapar(c.estado_procesal)}</div>
        ${proximo}
        <div class="meta">equipo: ${escapar((c.equipo || []).join(", ") || "sin asignar")}</div>
      </div>`;
    }).join("");
  },

  async agenda() {
    const audiencias = await pedir("/api/agenda?dias=60");
    if (!audiencias.length) return '<p class="vacio">Sin audiencias en 60 días.</p>';
    return `<table><thead><tr><th>Fecha</th><th>Hora</th><th>Causa</th><th>Tipo</th></tr></thead><tbody>
      ${audiencias.map((a) => `<tr>
        <td>${fecha(a.fecha)}</td><td>${escapar(a.hora ?? "—")}</td>
        <td>${escapar(a.caratula)}</td><td>${escapar(a.tipo)}</td></tr>`).join("")}
    </tbody></table>`;
  },

  async panel() {
    const datos = await pedir("/api/panel");
    const filas = (lista, columnas) => `<table><tbody>${lista.map((f) =>
      `<tr>${columnas.map((c) => `<td>${escapar(f[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    return `
      <div class="tarjeta"><h3>Causas por estado</h3>
        ${filas(datos.causas_por_estado, ["estado_procesal", "total"])}</div>
      <div class="tarjeta"><h3>Carga por abogado</h3>
        ${filas(datos.carga_por_abogado, ["nombre", "rol", "causas"])}</div>
      <div class="tarjeta"><h3>Plazos pendientes</h3>
        <div class="resultado">${escapar(datos.plazos_pendientes)}</div></div>`;
  },

  async nuevo() {
    estado.causas = estado.causas.length ? estado.causas : await pedir("/api/causas");
    const opciones = estado.causas.map((c) =>
      `<option value="${c.id}">${escapar(c.caratula)} — ${escapar(c.rol_rit ?? "s/RIT")}</option>`).join("");
    const hoy = new Date().toISOString().slice(0, 10);
    return `
      <form id="form-plazo">
        <label>Causa<select name="causa_id" required>${opciones}</select></label>
        <label>Descripción<input name="descripcion" required placeholder="Contestar demanda" /></label>
        <label>Notificación (AAAA-MM-DD)<input name="notificacion" value="${hoy}" required /></label>
        <label>Días hábiles<input name="dias" type="number" min="1" value="8" required /></label>
        <label>¿Fatal?<select name="es_fatal"><option value="si">sí</option><option value="no">no</option></select></label>
        <button class="accion" type="submit">Calcular y guardar</button>
      </form>
      <div id="resultado-plazo"></div>`;
  },
};

async function pintar(nombre) {
  estado.vista = nombre;
  const contenedor = document.getElementById("vista");
  contenedor.innerHTML = '<p class="cargando">cargando…</p>';
  try {
    contenedor.innerHTML = await vistas[nombre]();
    enlazar(nombre);
  } catch (error) {
    contenedor.innerHTML = `<p class="vacio">error: ${escapar(error.message)}</p>`;
  }
}

function enlazar(nombre) {
  document.querySelectorAll("[data-cumplido]").forEach((boton) => {
    boton.addEventListener("click", async () => {
      await pedir(`/api/plazos/${boton.dataset.cumplido}/cumplido`, { method: "POST" });
      pintar(nombre);
    });
  });
  const formulario = document.getElementById("form-plazo");
  if (formulario) formulario.addEventListener("submit", guardarPlazo);
}

async function guardarPlazo(evento) {
  evento.preventDefault();
  const datos = Object.fromEntries(new FormData(evento.target).entries());
  const caja = document.getElementById("resultado-plazo");
  caja.innerHTML = '<p class="cargando">guardando…</p>';
  try {
    const previo = await pedir(`/api/calculo?notificacion=${datos.notificacion}&dias=${datos.dias}`);
    await pedir("/api/plazos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        causa_id: Number(datos.causa_id),
        descripcion: datos.descripcion,
        dias: Number(datos.dias),
        notificacion: datos.notificacion,
        es_fatal: datos.es_fatal === "si",
      }),
    });
    caja.innerHTML = `
      ${(previo.advertencias || []).map((a) => `<div class="aviso">⚠️ ${escapar(a)}</div>`).join("")}
      <div class="resultado">vence el ${fecha(previo.fecha_vencimiento)}</div>
      <table class="detalle"><thead><tr><th>Fecha</th><th>Día</th><th>Cuenta</th><th>Motivo</th></tr></thead>
      <tbody>${previo.detalle.map((d) => `<tr class="${d.habil ? "" : "festivo"}">
        <td>${fecha(d.fecha)}</td><td>${escapar(d.dia)}</td>
        <td>${d.habil ? escapar(d.dia_contado) : "—"}</td><td>${escapar(d.motivo || "")}</td></tr>`).join("")}
      </tbody></table>`;
  } catch (error) {
    caja.innerHTML = `<p class="vacio">error: ${escapar(error.message)}</p>`;
  }
}

function vistaLogin(mensaje) {
  document.getElementById("pestanas").style.display = "none";
  document.getElementById("sesion").innerHTML = "";
  document.getElementById("vista").innerHTML = `
    <form id="form-login" class="tarjeta" style="margin-top:14px">
      <h3>Entrar al CRM</h3>
      <p class="meta">${escapar(mensaje || "Usa tu correo del estudio y tu contraseña.")}</p>
      <label>Correo<input name="email" type="email" required autocomplete="username" /></label>
      <label>Contraseña<input name="password" type="password" required autocomplete="current-password" /></label>
      <button class="accion" type="submit">Entrar</button>
      <p class="meta" style="margin:8px 0 0">
        Si trabajas solo, no necesitas contraseña: abre el panel con la URL que imprime
        «openlegal serve» (esa trae el token).
      </p>
    </form>`;
  document.getElementById("form-login").addEventListener("submit", async (evento) => {
    evento.preventDefault();
    const datos = Object.fromEntries(new FormData(evento.target).entries());
    try {
      const respuesta = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos),
      });
      if (!respuesta.ok) {
        const detalle = await respuesta.json().catch(() => ({}));
        throw new Error(detalle.detail || "no se pudo entrar");
      }
      location.reload();
    } catch (error) {
      document.getElementById("vista").querySelector(".meta").textContent = `No se pudo entrar: ${error.message}`;
    }
  });
}

function pintarSesion(info) {
  const caja = document.getElementById("sesion");
  const quien = info.usuario ? `${info.usuario.nombre} · ${info.usuario.rol}` : "modo token (abogado solo)";
  const salir = info.via === "sesion"
    ? '<button class="accion" id="salir">salir</button>'
    : "";
  caja.innerHTML = `<span class="meta">${escapar(quien)}</span> ${salir}`;
  const boton = document.getElementById("salir");
  if (boton) {
    boton.addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST" });
      location.reload();
    });
  }
}

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
  document.getElementById("pestanas").style.display = "";
  document.getElementById("estudio").textContent = `${info.estudio.nombre} · ${info.estudio.modo}`;
  pintarSesion(info);
  try {
    const estado = await pedir("/api/estado");
    document.getElementById("pie").textContent =
      `Open Legal CRM v${estado.version} · ${estado.motores} · ${estado.conteos.causas} causas, ${estado.conteos.plazos} plazos`;
  } catch (error) {
    document.getElementById("pie").textContent = "";
  }
  pintar("vencimientos");
}

document.getElementById("pestanas").addEventListener("click", (evento) => {
  const boton = evento.target.closest(".pestana");
  if (!boton) return;
  document.querySelectorAll(".pestana").forEach((b) => b.classList.toggle("activa", b === boton));
  pintar(boton.dataset.vista);
});

inicio();
