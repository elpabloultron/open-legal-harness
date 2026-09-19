# Pruebas reales: cómo entrar y qué tocar

Esta guía es para probar el sistema en esta máquina, con datos de ejemplo. No es la
instalación de la oficina (esa está en [`oficina.md`](oficina.md)); acá el CRM corre en tu
propio equipo y el estudio de ejemplo tiene seis usuarios.

## Qué está corriendo

| Pieza | Dónde | Cómo se levanta |
|---|---|---|
| El harness (dsh, perfil `legal`) | `http://127.0.0.1:8801` | `dsh --profile legal --no-open --port 8801` |
| El CRM (panel + API) | `http://127.0.0.1:8899` | `openlegal --db sqlite:///~/.openlegal/demo.db serve --port 8899 --token <token>` |
| La base del estudio de ejemplo | `~/.openlegal/demo.db` | — (la usan el panel **y** el agente: los dos ven lo mismo) |
| El token del panel | `~/.openlegal/token_panel.txt` | permiso 600: se lee, no se pega en el chat |

El token del harness **cambia cada vez que arranca**: la dirección con el token la imprime
al arrancar (`/tmp/harness_legal.log` guarda la última).

## Cómo entrar

1. Abre la dirección del harness que sale en el log (trae el token en la URL).
2. En el sidebar, la fila **«CRM Jurídico»** abre el panel. La primera vez pide la dirección
   del CRM y el token:
   - dirección: `http://127.0.0.1:8899`
   - token: el contenido de `~/.openlegal/token_panel.txt`
   Queda guardado en el navegador y no lo vuelve a pedir. El panel muestra si el CRM está
   levantado o si el token no sirve, que son dos problemas distintos.
3. Para entrar al CRM **sin** el harness: `http://127.0.0.1:8899/?token=<token>`.

## Qué probar (en este orden)

1. **El panel**: los módulos del menú — Inicio (tablero), Causas, Plazos, Agenda, Clientes,
   Usuarios, Avisos, Seguridad, Retención, Titulares. El menú se arma con tus permisos: si
   entras como paralegal, desaparecen los que ese rol no puede usar.
2. **Los plazos**: crea uno y mira el vencimiento. El CRM calcula los días hábiles con el
   Art. 66 CPC (no cuenta domingos ni feriados, el sábado es hábil) y guarda el detalle día
   por día. Un plazo fatal se ve en rojo.
3. **Los avisos**: en «Avisos», prueba el envío de prueba. Sin credenciales de correo
   configuradas, el canal queda sin destino y el sistema **lo dice** en vez de simular que
   envió. Las credenciales van en `~/.openlegal/notificaciones.json` (permiso 600) — no las
   pegues en el chat.
4. **El agente escribe en el CRM**: en el chat del harness, dictale una causa completa:
   > «Tengo la causa C-00001-2026 del cliente Lucía Herrera, RUT 11.111.111-1, teléfono
   > +56 9 8765 4321, contra Fondo del Norte SpA. Cargala con un plazo de 10 días para
   > contestar el traslado, notificado el 15 de septiembre, y agéndame la audiencia
   > preparatoria el 5 de octubre a las 9:00 en la Sala 3.»

   Después abre el panel: el cliente, la causa, el plazo y la audiencia tienen que estar, con
   el vencimiento calculado por el CRM. Todo queda en la bitácora con quién lo pidió.
5. **La cuenta de dividendos**: en el módulo Honorarios, por causa: el pacto, los gastos de
   tramitación, los abonos y el saldo, con el botón para imprimir la cuenta.
6. **La seguridad**: en «Seguridad» puedes dar de alta el segundo factor (TOTP) de un usuario
   en dos pasos: el CRM muestra el secreto una vez y pide un código para confirmarlo.

## Los usuarios del estudio de ejemplo

| Correo | Rol | Qué puede |
|---|---|---|
| `socia@estudio.cl` | socio | todo, incluidas las finanzas y los usuarios |
| `admin@estudio.cl` | administrador | casi todo, sin redacción ni finanzas |
| `ana@estudio.cl`, `luis@estudio.cl` | abogado | sus causas, plazos, agendas; ven honorarios, no los editan |
| `secretaria@estudio.cl` | administrativo | todo el estudio para operar y facturar; sin documentos |
| `carga@estudio.cl` | paralegal | agenda y plazos, sin finanzas ni usuarios |

Para ponerle contraseña a un usuario (la clave se pide por teclado y **nunca** pasa por el
chat ni queda en el historial):

```sh
cd "/home/pablo/Escritorio/Legal Harness"
.venv/bin/openlegal --db sqlite:///~/.openlegal/demo.db usuario clave --usuario socia@estudio.cl
```

## Lo que el agente no hace, a propósito

Configurar el correo o el SMS (son credenciales), anonimizar datos o ejecutar la retención
(no tienen vuelta atrás) y sacar el expediente de un cliente del sistema (es salida de datos
personales). Eso se hace desde el panel, con una persona apretando el botón.

## Lo que todavía no está

- **El módulo de plata pide `honorario.leer`**: un paralegal (que sí tiene `gasto.leer`) no ve
  sus gastos en la interfaz aunque la API se los permitiría. Está anotado como limitación.
- **SII, boletas y facturas electrónicas**: el módulo registra honorarios, gastos y pagos, y
  la cuenta de dividendos. La emisión de boletas y los DTE no están, y la tasa de retención
  **no se inventa**: la declara el estudio, copiada de su boleta.
- **Segundo factor obligatorio para socio y administrador**: el alta está lista, falta
  exigirlo al entrar.
- **Impresión de la cuenta de dividendos**: el botón abre una página imprimible; el PDF sale
  con el diálogo de impresión del navegador (Ctrl+P).
