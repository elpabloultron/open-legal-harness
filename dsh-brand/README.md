# Marca de Open Legal Harness para DeepSeek Harness

Reemplaza el signo por defecto de la interfaz (el pez) y el nombre del producto por
**la balanza** y **«Open Legal Harness»**, en los tres lugares donde la interfaz
declara marca:

| Slot | Tipo | Dónde se ve |
|---|---|---|
| `sidebar.brand.mark` | `single` | el signo junto al nombre, arriba del sidebar (24 px) |
| `sidebar.brand.name` | `single` | el nombre del producto, en el sidebar |
| `conversation.hero.brand.mark` | `single` | el signo grande de la pantalla inicial (34 px), donde el build oficial deja el pez animado de reserva |

## Por qué es un plugin y no un parche al harness

Los tres slots son `kind: single`: un solo registro gana, y un plugin **externo**
tiene precedencia sobre los integrados (banda `extension`). Por eso este paquete
ocupa la marca sin desactivar ni tocar `@deepseek-ai/dsh-client-ui-brand-official`,
que sigue instalado — y si este plugin se desmonta del perfil, el pez vuelve solo.

La balanza se dibuja acá, en SVG, con `currentColor`: adopta el tema claro u oscuro
del harness sin cambiar una línea.

## Construir y montar

```sh
# node_modules es un enlace al del plugin del CRM, que ya trae esbuild y tsc
ln -s ../dsh-plugin/node_modules node_modules
npm run build          # emite lib/index.js y lib/client.js, y verifica el contrato
npm test               # verifica el artefacto ya construido, sin reconstruir

# montar en el perfil `legal` (la mitad navegador se sirve en /plugins/<id>/client.js)
dsh plugin --profile legal add link:/ruta/a/Legal Harness/dsh-brand
dsh --profile legal --dump-config | grep -A2 openlegal-brand
```

El harness carga los plugins al arrancar: hasta reiniciar el perfil, la interfaz sigue
mostrando el pez.
