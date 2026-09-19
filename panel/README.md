# El panel del CRM (React + React-Admin)

Este directorio es la interfaz del CRM: una aplicación React con React-Admin que habla con
la API local del CRM (`openlegal serve`). Se compila a `../src/openlegal/static/app/`, que es
lo que el servidor sirve en la raíz — así que el navegador habla con el mismo origen y la
sesión viaja como cookie, sin CORS ni tokens en JavaScript.

## Por qué esta estructura

- **React-Admin** trae hechas las piezas de un panel de gestión: listados con filtros y
  paginación, formularios, validación, notificaciones, autenticación y permisos. Escribir
  eso a mano (como en el panel clásico de `static/panel.js`) es lo que se vuelve caro de
  mantener; éste es el estándar que cualquier desarrollador reconoce.
- **Las reglas viven en el servidor.** El `dataProvider` no reimplementa nada: llama a la
  API, que es la que exige permisos, calcula los plazos con el Art. 66 CPC, encola los avisos
  y deja la bitácora. Esconder un botón acá es cortesía; la seguridad está del otro lado.
- **El menú se arma con los permisos** que devuelve `/api/sesion`: un módulo que la persona
  no puede usar no aparece, y si mañana se cambia un rol, cambia solo.
- **La sesión es una cookie `httpOnly`**: no hay token guardado en el navegador, así que un
  error en la interfaz no se lleva la sesión.

## Cómo se trabaja acá

```bash
cd panel
pnpm install          # una vez
pnpm run dev          # desarrollo: abre en :5173 y pasa /api al CRM local (:8791)
pnpm run test         # pruebas del conector y de los permisos
pnpm run build        # tipos + compilación a ../src/openlegal/static/app
```

Para desarrollar contra datos reales, levantá el CRM en el puerto que espera el proxy:

```bash
openlegal --db "sqlite://$HOME/.openlegal/estudio.db" serve --port 8791
```

**El resultado compilado se versiona** (está en `src/openlegal/static/app/`): así el paquete
de Python sirve el panel sin necesitar node, y la CI comprueba en cada push que lo commiteado
coincide con el código — si alguien cambia un componente y no compila, la compuerta falla.

## Cómo está organizado

```
src/
  api/
    cliente.ts        fetch con cookie, y los errores del CRM traducidos a texto
    authProvider.ts   entrar, salir, permisos y `canAccess`
    dataProvider.ts   el conector con la API (listar, crear, editar; borrar no se puede)
  layout/
    Barra.tsx         la barra del estudio (nombre, modo, quién está trabajando)
    Menu.tsx          el menú, armado con los permisos del servidor
  modulos/
    Inicio.tsx        el tablero
    recursos.tsx      causas, plazos, agenda, clientes y usuarios
    Avisos.tsx        correo y SMS: configuración, pruebas, cola y despacho
    Seguridad.tsx     segundo factor, intentos fallidos y bloqueos
    Retencion.tsx     cuánto se conserva y qué corresponde hacer
    Titulares.tsx     acceso, portabilidad y supresión
  tipos.ts            los tipos del CRM y el mapa de permisos
  tema.ts             el tema (sobrio, con contraste, pensado para imprimir)
```

## Lo que falta

- **Playwright** para probar los recorridos completos en un navegador de verdad, dentro de la
  CI (hoy se prueban el conector y los permisos, y el recorrido por HTTP de la API).
- **División del paquete** (code splitting): hoy los 1,05 MB del bundle viajan juntos. En la
  red de una oficina es aceptable, pero se puede partir por módulo.
- Migrar lo que queda del panel clásico (`/clasico`) y retirarlo.
