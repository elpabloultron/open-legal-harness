# Avisos por correo y SMS

El CRM avisa por dos caminos distintos, y conviene no confundirlos:

| Aviso | Cuándo sale | Para qué |
|---|---|---|
| **Asignación** | En el momento en que se crea un plazo o una audiencia a nombre de otra persona | «Te tocó esto» |
| **Recordatorio** | Todos los días, mientras el plazo siga pendiente y esté dentro de la ventana de días (`dias_de_aviso`, 3 por defecto) | «Esto vence» |

Ninguno de los dos se manda en el instante en que se crea: los dos **se encolan** en la
base del estudio. Un servidor de correo lento o caído no puede dejar el panel esperando, un
aviso que falla se reintenta sin perderse, y queda registro de qué se avisó, a quién,
cuándo y por qué canal — que es lo que después permite responder «sí, se le avisó el día 12».

**Un aviso no se repite.** Cada aviso lleva una huella (plazo, canal, persona y día) que es
única en la base: dos corridas del mismo día no mandan dos veces lo mismo. Si un plazo no
se marca como cumplido, vuelve a avisar **al día siguiente**; cuando se marca, los avisos paran.

**Un aviso que falla no se pierde**: suma su intento, guarda el error y sigue en la cola
hasta cinco intentos, cuando queda como `fallida` con el error a la vista. Nunca se borra.

---

## 1. Configurar el correo

Se necesita una cuenta de correo para que el estudio avise (por ejemplo
`avisos@estudio.cl`). La configuración va en un archivo que **sólo puede leer su dueño**:

```bash
mkdir -p ~/.openlegal && chmod 700 ~/.openlegal
umask 077 && nano ~/.openlegal/notificaciones.json
chmod 600 ~/.openlegal/notificaciones.json
```

```json
{
  "email": {
    "host": "smtp.estudio.cl",
    "puerto": 587,
    "usuario": "avisos@estudio.cl",
    "clave": "la-clave-de-aplicación",
    "de": "Estudio Soto <avisos@estudio.cl>",
    "seguridad": "starttls"
  },
  "canales_por_defecto": ["email"],
  "dias_de_aviso": 3,
  "avisar_asignaciones": true
}
```

- **`seguridad`**: `starttls` (por defecto) cifra la conexión. `ninguna` existe sólo para un
  relay local de la propia oficina (`127.0.0.1`): contra un servidor de afuera mandaría la
  clave en claro.
- **`clave`**: si el proveedor es Gmail, Microsoft 365 o similar, hay que generar una
  **clave de aplicación** — nunca usar la clave personal de la cuenta.
- **No escribas credenciales en el chat** ni en un ticket: van sólo en este archivo.

También se puede configurar por variables de entorno, que mandan sobre el archivo:
`OPENLEGAL_SMTP_HOST`, `OPENLEGAL_SMTP_PUERTO`, `OPENLEGAL_SMTP_USUARIO`,
`OPENLEGAL_SMTP_CLAVE`, `OPENLEGAL_SMTP_DE`, `OPENLEGAL_AVISO_DIAS`,
`OPENLEGAL_CANALES`, `OPENLEGAL_CONFIG`.

**Comprobar qué está configurado** (y qué falta), sin que la clave se muestre:

```bash
openlegal notificar --config
```

## 2. Configurar el SMS

El SMS necesita una cuenta de un proveedor: no hay forma de mandarlo gratis desde el propio
servidor. Se soportan dos, y el canal queda declarado como no disponible si falta la cuenta
(mejor eso que fallar en silencio).

**Twilio**:

```json
{
  "sms": {
    "proveedor": "twilio",
    "cuenta": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "token": "el-token",
    "de": "+56900000000"
  },
  "canales_por_defecto": ["email", "sms"]
}
```

**Cualquier otro proveedor con webhook** (recibe un `POST` con `{"to", "text", "from"}` y,
si se configura, un `Authorization: Bearer`):

```json
{
  "sms": {"proveedor": "webhook", "webhook": "https://su-proveedor.cl/enviar", "token": "…", "de": "Estudio"}
}
```

**Para probar sin gastar ni contratar nada**: `"proveedor": "consola"` deja el aviso en
`~/.openlegal/avisos.log` en vez de mandarlo. Es también lo que conviene usar el primer día,
para ver qué textos saldrían y a quién.

Los teléfonos se cargan por usuario (`openlegal usuario editar --email … --telefono +56…`); sin
teléfono cargado, el aviso por SMS no se inventa: se informa como «sin destino».

El SMS lleva un texto **corto** (el asunto y, si cabe, el detalle esencial, dentro de dos
segmentos): un SMS se cobra por segmento de 160 caracteres, y el cuerpo completo del correo
sería carísimo en una pantalla de teléfono. El correo lleva el detalle completo.

## 3. Despachar la cola

En una sola línea, cada diez minutos, en la máquina del estudio:

```bash
openlegal notificar --generar --enviar
```

- `--generar` revisa plazos y audiencias y encola lo que corresponda (es idempotente: correrlo
  cada diez minutos no genera avisos repetidos — la huella incluye el día).
- `--enviar` saca la cola por correo y SMS y deja el resultado de cada intento.

Como servicio, con el planificador del sistema (`systemctl --user edit --force --full avisos-crm`):

```ini
[Unit]
Description=Avisos del CRM jurídico
[Service]
Type=oneshot
ExecStart=%h/.local/bin/openlegal notificar --generar --enviar
```

Y el temporizador (`avisos-crm.timer`):

```ini
[Unit]
Description=Avisos del CRM cada 10 minutos
[Timer]
OnBootSec=5min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now avisos-crm.timer
```

Con `cron` de toda la vida: `*/10 * * * * /home/usuario/.local/bin/openlegal notificar --generar --enviar >> ~/.openlegal/avisos-cron.log 2>&1`

## 4. Ver qué pasó

```bash
openlegal notificar --estado      # cuántos pendientes, enviados, fallidos, y qué está fallando
openlegal notificar --config      # qué canal está listo y cuál falta
```

- **No sale ningún correo**: `openlegal notificar --config` dice qué falta; el error concreto
  de cada aviso queda en `openlegal notificar --estado`.
- **Un aviso no se encoló y no hay registro**: revisar `~/.openlegal/errores.log`. Si el aviso
  no se pudo ni guardar, queda anotado ahí (por diseño, un aviso nunca puede impedir que se
  cree un plazo).

## 5. Lo que este circuito NO hace, a propósito

- **No le avisa al cliente.** Los avisos son internos del estudio. Comunicarle a un cliente
  que su plazo vence es un acto profesional con responsabilidad, y no se automatiza por
  accidente: si se quiere, se hace como una acción explícita y firmada.
- **No borra nada.** Un aviso fallido o cumplido se conserva: es parte del registro de la
  causa, y en una discusión sobre responsabilidad profesional lo primero que se pide es
  justamente eso.
