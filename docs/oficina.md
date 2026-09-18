# El CRM en la oficina: un equipo que sirve a los demás

El CRM se instala **en un solo equipo de la oficina**. Ese equipo guarda la base y las
copias; los demás equipos (y los teléfonos, si están en la misma red) entran **por el
navegador**, sin instalar nada. No hay que repartir la base ni sincronizar archivos: cada
quien abre su sesión en la misma dirección y ve lo suyo.

```
        ┌────────────────────────┐
        │  equipo del estudio    │   guarda la base, manda los avisos,
        │  CRM (servicio)        │   hace el respaldo cada noche
        └───────────┬────────────┘
                    │  red local de la oficina (nada sale a internet)
      ┌─────────────┼──────────────┬──────────────┐
   abogada       secretaría      socio        (teléfono)
   navegador     navegador      navegador
```

**Regla que no se rompe**: la base vive en ese equipo. Nadie abre el archivo de la base
desde otro computador ni desde una carpeta compartida — eso corrompe las bases SQLite. Los
otros equipos usan **el navegador**, siempre.

---

## 1. En el equipo del estudio

```bash
# 1) instalar el CRM (una vez)
cd open-legal-harness
uv venv && uv pip install -e ".[ui]"

# 2) dejar el estudio listo
.venv/bin/openlegal --db "sqlite://$HOME/.openlegal/estudio.db" estudio crear --nombre "Estudio ..." --modo oficina
.venv/bin/openlegal --db "sqlite://$HOME/.openlegal/estudio.db" usuario crear --nombre "..." --email ... --rol socio
#    (la contraseña se pide por teclado, nunca por línea de comandos)

# 3) instalar como servicio: panel + avisos + respaldo
bash scripts/instalar_oficina.sh --db "sqlite://$HOME/.openlegal/estudio.db"
```

El instalador deja tres cosas andando con el planificador del sistema y **no pide nada más**:

| Servicio | Qué hace |
|---|---|
| `openlegal-panel` | El CRM, escuchando en el puerto 8899 y arrancando solo si se cae |
| `openlegal-avisos` (cada 10 min) | Encola y manda los avisos de plazos y audiencias |
| `openlegal-respaldo` (23:30) | Copia consistente de la base, con rotación de 14 días |

Con `--solo-generar` deja los servicios escritos y **no** los activa (para revisarlos antes).
Con `--host 127.0.0.1` el CRM queda accesible sólo desde ese equipo, sin red.

### Que arranque sin que nadie inicie sesión

Una vez, con la contraseña de administración del equipo:

```bash
sudo loginctl enable-linger $USER
```

Sin esto, el CRM arranca cuando alguien inicia sesión en ese computador y se apaga al cerrarla.

## 2. Las direcciones que se reparten

El instalador imprime dos:

- **En el equipo del estudio**: `http://127.0.0.1:8899/`
- **Para los demás**: `http://192.168.x.x:8899/` ← ésta es la que se les manda

Conviene darle un nombre en la red (por ejemplo `crm.oficina.local` en el router) para no
depender de una IP que puede cambiar, y dejar la dirección en un acceso directo en el
escritorio de cada equipo.

## 3. Usuarios: cada uno entra con lo suyo

```bash
openlegal --db "$DB" usuario crear  --nombre "Ana Pérez" --email ana@estudio.cl --rol abogado
openlegal --db "$DB" usuario editar --email ana@estudio.cl --telefono +56912345678   # para el SMS
openlegal --db "$DB" usuario listar
openlegal --db "$DB" usuario desactivar --email ana@estudio.cl   # se va alguien: se corta el acceso, no se borra el historial
```

Alguien que se va del estudio **no se borra**: se desactiva. Las causas siguen mostrando quién
las llevaba, que es justamente lo que hay que poder probar.

## 4. Antes de que lo use el equipo: seguridad

1. **Segundo factor (TOTP) en socio y administrador.** Es la diferencia entre una contraseña
   filtrada y una contraseña filtrada que no sirve de nada:
   ```bash
   openlegal --db "$DB" usuario 2fa --email socio@estudio.cl      # enrola y muestra el QR
   openlegal --db "$DB" seguridad                                  # quién tiene y quién no
   ```
2. **Bloqueo de cuentas.** Después de 8 intentos fallidos, la cuenta se bloquea 15 minutos
   (también con la contraseña correcta: si no, el bloqueo no sirve). Se destraba sola, o a mano:
   ```bash
   openlegal --db "$DB" seguridad --desbloquear ana@estudio.cl
   ```
3. **El puerto, sólo a la red de la oficina.** En el cortafuegos del equipo, abrir 8899 a la
   red local y a nada más. **No publicar este puerto en internet**: son expedientes de
   clientes bajo secreto profesional.
4. **Cifrado en reposo.** No es código, es paso de instalación: disco completo (LUKS,
   BitLocker, FileVault) o carpeta cifrada para `~/.openlegal`. Sin eso, quien tenga el equipo
   en las manos tiene los expedientes. Ver `docs/proteccion_datos.md`.
5. **Segundo plano útil**: si el equipo está en un lugar de paso, activar el bloqueo de
   pantalla por inactividad. El CRM no puede proteger una sesión abierta y desatendida.

## 5. Respaldos

El servicio de las 23:30 corre `scripts/respaldo.sh`, que copia la base **de forma
consistente aunque el CRM esté funcionando** (copia con la API de respaldo de SQLite, no un
`cp`), y rota: conserva 14 copias.

```bash
bash scripts/respaldo.sh --destino /media/usb/respaldos --conservar 30
```

Un respaldo que nunca salió del equipo no es un respaldo: hay que llevarlo a un disco externo
o a otro lugar, y **cifrarlo**, porque son datos de clientes:

```bash
# una vez: crear la frase de cifrado en un archivo que sólo lea su dueño
umask 077 && printf '%s' 'frase-larga-y-unica' > ~/.openlegal/frase-respaldo
chmod 600 ~/.openlegal/frase-respaldo

# el respaldo sale cifrado (necesita gpg)
OPENLEGAL_RESPALDO_CLAVE=~/.openlegal/frase-respaldo bash scripts/respaldo.sh --destino /media/usb/respaldos
# para restaurar:  gpg -d estudio-20260918-233000.db.gpg > estudio.db
```

**Probar la restauración es parte del respaldo.** Un respaldo que nunca se abrió no se sabe
si sirve: una vez al mes, ábrelo en un equipo aparte y comprueba que están las causas.

En PostgreSQL (oficina con varios abogados escribiendo a la vez y volumen alto) el respaldo
usa `pg_dump`, y hace falta el paquete `postgresql-client` instalado:

```bash
bash scripts/respaldo.sh --db "postgresql://crm:clave@localhost:5432/estudio"
```

> ¿SQLite o PostgreSQL? Para un estudio de hasta cinco o seis abogados, **SQLite en el equipo
> del estudio alcanza de sobra**: nadie escribe directamente en la base, todos escriben por el
> panel. PostgreSQL se justifica cuando hay varios procesos escribiendo de verdad o cuando las
> causas se cuentan por decenas de miles.

## 6. Actualizar el CRM

```bash
cd open-legal-harness && git pull
uv pip install -e ".[ui]"
openlegal --db "$DB" migraciones        # informa en qué versión está la base y qué falta
openlegal --db "$DB" panel              # cualquier comando aplica lo que falte antes de trabajar
systemctl --user restart openlegal-panel
```

Las migraciones **se aplican al usar el CRM** (cada comando lo hace antes de trabajar, y queda
registrado en la tabla de migraciones), así que no hay un paso aparte que se pueda olvidar. Si
`migraciones` dice que falta algo, corre cualquier comando y quedará al día.

**Haz un respaldo antes de cada actualización** (`bash scripts/respaldo.sh`): las migraciones
se prueban, pero una copia reciente es lo que convierte un problema en una molestia.

## 7. Qué no sale de la oficina

- La base y los expedientes: viven en el equipo del estudio.
- Los avisos: salen por el correo y el SMS que el estudio contrata, y llevan sólo lo
  necesario para avisar (causa, vencimiento, qué hay que hacer) — no el expediente.
- Los servicios públicos que consulta el harness (`open-legal-chile`): son consultas a fuentes
  públicas, sin datos de clientes.
- Las redacciones con IA: sólo con autorización por causa y registro de la transferencia
  (`docs/proteccion_datos.md`).

## 8. Problemas frecuentes

| Síntoma | Qué mirar |
|---|---|
| Los otros equipos no abren la dirección | El cortafuegos del equipo del estudio, que el servicio esté activo (`systemctl --user status openlegal-panel`) y que estén en la misma red |
| «Cuenta bloqueada por intentos fallidos» | `openlegal seguridad --desbloquear correo@estudio.cl` |
| No llegan los avisos | `openlegal notificar --config` (¿qué canal falta?) y `openlegal notificar --estado` (¿qué error da?) |
| El respaldo nocturno falla | `journalctl --user -u openlegal-respaldo`; en PostgreSQL, que esté `pg_dump` |
| El CRM no arranca tras reiniciar el equipo | Falta `sudo loginctl enable-linger $USER` |
| Todo lento con muchos abogados | Es el momento de pasar a PostgreSQL (`docs/modelo_datos.md`) |
