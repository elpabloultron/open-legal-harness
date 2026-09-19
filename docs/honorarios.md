# Honorarios, gastos y cuenta de dividendos

El módulo de plata del CRM. Registra lo que se pacta con el cliente, lo que el estudio gasta
en tramitar y lo que el cliente va pagando, y con eso arma la **cuenta de dividendos** de una
causa: el documento que se le presenta al cliente (y al tribunal, cuando el juicio termina y
hay que rendir).

## La regla que manda: la retención no se inventa

La tasa de retención de honorarios cambia y la fija el SII. El CRM **no la calcula, no la
supone y no la tiene escrita**: la declara el estudio, copiada de su boleta, y llega como
dato (`--retencion` en la terminal, el campo en el panel, `retencion_sii` en la base).

Si no se declara, el líquido queda igual al bruto **y la cuenta lo advierte por escrito**,
para que nadie mire un documento con un saldo que no es. Lo mismo con un gasto sin
comprobante (avisa que después es difícil de sostener ante el cliente) y con un pago sin
imputar a un honorario (no cambia el total, pero no se sabe cuál quedó pagado).

No hay integración con el SII ni emisión de documentos tributarios. Ver abajo qué falta.

## Desde la terminal

```sh
# lo pactado con el cliente
openlegal honorario crear --causa 1 --usuario socia@estudio.cl \
    --modalidad fijo --monto 350000 --bruto 350000 --retencion 35000 \
    --descripcion "Demanda de mutuo" --fecha 2026-09-01

# lo que gastó el estudio en tramitar
openlegal gasto crear --causa 1 --usuario socia@estudio.cl \
    --concepto "Certificado de dominio" --monto 25000 --comprobante B-1234

# lo que pagó el cliente
openlegal pago crear --causa 1 --usuario socia@estudio.cl \
    --monto 200000 --medio transferencia --referencia TRF-99887

# y la cuenta
openlegal cuenta --causa 1 --usuario socia@estudio.cl --html cuenta.html
```

Los montos son **enteros en CLP**: `350000` es $350.000. Las modalidades son `fijo`, `hora`,
`cuota_litis` y `mixto` — con una cuota litis no hay monto fijo todavía, así que el pacto se
describe en palabras y el monto se registra cuando se sabe.

## Cómo se calcula

```
saldo = honorarios líquidos + gastos por cuenta del cliente − pagos recibidos
```

El líquido de cada honorario se guarda cuando se registra (bruto − retención declarada) y no
se recalcula después: un líquido que ya se cobró no puede cambiar porque alguien corrija un
bruto histórico. Si hay que corregir, se registra otro movimiento — el CRM no borra.

El pago se imputa a un honorario con `--honorario N`; ahí el CRM recalcula el pagado y deja
el honorario en `pendiente`, `parcial` o `pagado`. Un pago sin imputar se descuenta del saldo
igual, pero el CRM avisa que no puede decir cuál honorario quedó cubierto.

## La cuenta de dividendos

Con `--html` sale un archivo listo para imprimir (A4, todo embebido, sin conexión ni
JavaScript): el estudio, el cliente con su RUT y domicilio, la causa con su Rol/RIT y
tribunal, el detalle de honorarios, gastos y pagos, los totales y el saldo.

Lleva tres notas fijas: que es un estado de cuenta **interno** del estudio, que **no es un
documento tributario** y que no reemplaza la boleta ni la factura de honorarios, y —cuando
corresponde— la advertencia de que hay honorarios sin retención declarada.

## Quién puede qué

| Rol | Honorarios y pagos | Gastos |
|---|---|---|
| socio | registrar y ver | registrar y ver |
| administrativo (la secretaria que factura) | registrar y ver | registrar y ver |
| abogado, administrador | ver | ver |
| paralegal | — | ver (por API; ver la limitación del panel abajo) |

Ninguna escritura es anónima: cada movimiento queda en la bitácora con el usuario que lo
registró, y por eso la cuenta puede mostrar quién anotó cada pago.

## El agente también

Cuatro herramientas MCP: `crm_honorario_registrar`, `crm_gasto_registrar`,
`crm_pago_registrar` y `crm_cuenta_dividendos` (lectura). Las descripciones del servidor le
dicen al modelo que los montos son enteros en CLP, que **la retención la declara el estudio**
y que registrar un pago es un acto con consecuencias: que confirme el monto con quien se lo
pidió antes de anotarlo.

## Lo que todavía no está

- **Boletas de honorarios y facturas electrónicas (DTE)**: no se emiten. Requiere certificado
  digital del SII, folios y ambiente de certificación. Cuando se haga, la vía limpia es la API
  de [LibreDTE](https://www.libredte.cl/) como servicio —su licencia es AGPL y enlazarla
  dentro de este programa obligaría a liberarlo todo bajo AGPL—, no importar su biblioteca.
- **Conciliación bancaria**: los pagos se registran a mano. No hay lectura de cartolas.
- **Notas de crédito y reajustes**: por ahora un error se corrige con otro movimiento.
- **Limitación conocida del panel**: el módulo pide `honorario.leer` para cargar la cuenta,
  así que un `paralegal` (que tiene `gasto.leer` pero no `honorario.leer`) no ve sus gastos en
  la interfaz, aunque la API se los permitiría.
