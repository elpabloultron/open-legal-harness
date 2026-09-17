# Modelo de datos

Un solo modelo sirve a los dos modos. En modo `solo` hay un estudio y un usuario;
en modo `oficina` hay varios de cada uno. Ninguna consulta de la aplicación puede
escribirse sin `estudio_id`: ese es el límite duro de aislamiento.

```
estudios ─┬─ usuarios ──── sesiones
          │      └── causa_equipo ──┐
          ├─ clientes ── causas ────┼─ plazos
          │                         ├─ audiencias
          │                         ├─ documentos
          │                         ├─ honorarios
          │                         └─ gastos
          └─ auditoria
```

## Tablas

| Tabla | Qué guarda | Campos clave |
|---|---|---|
| `estudios` | el estudio o la oficina. `modo` = `solo` \| `oficina` | `nombre`, `rut`, `modo` |
| `usuarios` | abogados, socios, paralegales, administrativos y clientes con acceso | `rol`, `password_hash` (scrypt), `activo` |
| `sesiones` | tokens de sesión con expiración | `token`, `expira_en` |
| `clientes` | personas naturales o jurídicas (SpA, Ltda.) | `rut`, `tipo_persona` |
| `causas` | el expediente: carátula, Rol/RIT, tribunal, materia, estado | `rol_rit`, `contraparte`, `cuantia_clp` |
| `causa_equipo` | quién trabaja cada causa y con qué rol procesal | `rol_en_causa`, `desde`, `hasta` |
| `plazos` | plazos judiciales, administrativos e internos | `dias`, `fecha_notificacion`, `fecha_vencimiento`, `es_fatal` |
| `audiencias` | agenda: tipo, fecha, modalidad (presencial/remota), minuta | `lugar_o_url`, `minuta` |
| `documentos` | escritos y pruebas, con visibilidad interna o publicable | `visibilidad` = `interno` \| `cliente` |
| `honorarios` | pacto de honorarios y su estado de pago | `modalidad`, `monto_bruto`, `retencion_sii`, `monto_liquido` |
| `gastos` | gastos de tramitación y su reembolso | `pagado_por_estudio`, `reembolsado` |
| `auditoria` | bitácora: quién, qué, sobre qué y cuándo | `accion`, `entidad`, `entidad_id` |

## Decisiones de diseño

1. **El acceso se concede por causa, no por materia.** La tabla `causa_equipo` es
   la fuente de verdad: si el usuario no está ahí (`hasta IS NULL`), no ve la
   causa. Así un estudio con 8 abogados no expone todos los expedientes a todos.
2. **Visibilidad explícita en documentos.** Lo que se carga como `interno` nunca
   se publica al cliente; lo que se marca `cliente` es lo único que el portal
   puede mostrar. Es minimización de datos aplicada, no una promesa.
3. **Plazos guardan el cálculo, no sólo el resultado.** `dias` +
   `fecha_notificacion` + `fecha_vencimiento` permiten reconstruir y auditar el
   cómputo del Art. 66 CPC si alguien lo cuestiona (Art. 170 CPC y
   responsabilidad civil del abogado de por medio).
4. **Honorarios separan bruto, retención y líquido.** La retención SII cambia por
   ley cada año (Ley 21.133): se guarda el monto retenido efectivo, no una tasa
   recalculada a posteriori.
5. **Toda escritura deja fila en `auditoria`.** En una oficina, la bitácora es la
   que permite responder «¿quién cambió esta fecha?».

## Compatibilidad

`src/openlegal/schema.sql` está en dialecto SQLite y `db.py` lo traduce a
PostgreSQL al vuelo (`INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`,
marcadores `?` → `%s`). Las consultas de la aplicación usan `?` siempre.

> Pendiente F1: migraciones versionadas (hoy `migrar()` es idempotente, no
> versionado). Necesario antes de tocar datos reales de un estudio.
