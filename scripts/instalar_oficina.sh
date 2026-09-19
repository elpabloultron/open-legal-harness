#!/usr/bin/env bash
#
# Instala el CRM jurídico como servicio en ESTE equipo, que pasa a ser el del estudio:
# los otros equipos de la oficina entran por el navegador, sin instalar nada.
#
#   bash scripts/instalar_oficina.sh --db sqlite:///home/ana/.openlegal/estudio.db
#   bash scripts/instalar_oficina.sh --solo-generar     # escribe los servicios y no los activa
#
# Deja tres cosas andando con el planificador del sistema (systemd, como servicio del
# usuario): el panel, los avisos cada diez minutos y el respaldo de cada noche.
set -euo pipefail

PUERTO=8899
HOST=0.0.0.0
BASE="sqlite://${HOME}/.openlegal/estudio.db"
SOLO_GENERAR=0
CON_AVISOS=1
CON_RESPALDO=1
CERT=""
CLAVE_CERT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --db) BASE="$2"; shift 2 ;;
    --puerto) PUERTO="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --solo-generar) SOLO_GENERAR=1; shift ;;
    --sin-avisos) CON_AVISOS=0; shift ;;
    --sin-respaldo) CON_RESPALDO=0; shift ;;
    --cert) CERT="$2"; shift 2 ;;
    --key) CLAVE_CERT="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
OPENLEGAL="$(command -v openlegal || true)"
[ -z "$OPENLEGAL" ] && [ -x "$RAIZ/.venv/bin/openlegal" ] && OPENLEGAL="$RAIZ/.venv/bin/openlegal"

echo "=== CRM jurídico: instalación como equipo del estudio ==="

if [ -z "$OPENLEGAL" ]; then
  echo "  ✗ no encuentro el comando 'openlegal'." >&2
  echo "    Instálalo primero:  uv pip install -e '$RAIZ[ui]'   (o activa su entorno virtual)" >&2
  exit 1
fi
echo "  comando: $OPENLEGAL"

# ---------------------------------------------------------------- datos y carpetas
mkdir -p "$HOME/.openlegal"
chmod 700 "$HOME/.openlegal"
echo "  datos:   $BASE"

# La base se crea (o se pone al día) ahora, para que el servicio no falle al primer arranque.
"$OPENLEGAL" --db "$BASE" migraciones >/dev/null
echo "  base creada o al día"

case "$BASE" in
  postgresql://*|postgres://*)
    if ! command -v pg_dump >/dev/null 2>&1; then
      echo "  aviso: el respaldo de PostgreSQL necesita 'pg_dump' (paquete postgresql-client)." >&2
      echo "         Instálalo o el respaldo nocturno va a fallar." >&2
    fi
    ;;
esac

UNIDADES="$HOME/.config/systemd/user"
mkdir -p "$UNIDADES"

servicios=()
escribir() {  # nombre, contenido
  printf '%s\n' "$2" > "$UNIDADES/$1"
  echo "  servicio escrito: $1"
}

# ------------------------------------------------------------------------ el panel
TLS=""
[ -n "$CERT" ] && TLS=" --cert ${CERT}" && [ -n "$CLAVE_CERT" ] && TLS="${TLS} --key ${CLAVE_CERT}"
escribir "openlegal-panel.service" "[Unit]
Description=CRM jurídico (panel del estudio)
After=network-online.target
[Service]
Type=simple
ExecStart=${OPENLEGAL} --db ${BASE} serve --host ${HOST} --port ${PUERTO}${TLS}
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target"
servicios+=("openlegal-panel.service")

# ------------------------------------------------------------------------- los avisos
if [ "$CON_AVISOS" = "1" ]; then
  escribir "openlegal-avisos.service" "[Unit]
Description=Avisos del CRM jurídico: recordatorios de plazos y audiencias
[Service]
Type=oneshot
ExecStart=${OPENLEGAL} --db ${BASE} notificar --generar --enviar"
  escribir "openlegal-avisos.timer" "[Unit]
Description=Avisos del CRM cada 10 minutos
[Timer]
OnBootSec=5min
OnUnitActiveSec=10min
AccuracySec=1min
[Install]
WantedBy=timers.target"
  servicios+=("openlegal-avisos.timer")
fi

# ----------------------------------------------------------------------- el respaldo
if [ "$CON_RESPALDO" = "1" ]; then
  escribir "openlegal-respaldo.service" "[Unit]
Description=Respaldo del CRM jurídico
[Service]
Type=oneshot
ExecStart=/usr/bin/env bash ${RAIZ}/scripts/respaldo.sh --db ${BASE}"
  escribir "openlegal-respaldo.timer" "[Unit]
Description=Respaldo del CRM cada noche
[Timer]
OnCalendar=*-*-* 23:30:00
Persistent=true
[Install]
WantedBy=timers.target"
  servicios+=("openlegal-respaldo.timer")
fi

# ----------------------------------------------------------------------- activación
if [ "$SOLO_GENERAR" = "1" ]; then
  echo "  (--solo-generar: no se activa nada)"
  echo "  para activarlo después:"
  echo "    systemctl --user daemon-reload"
  echo "    systemctl --user enable --now ${servicios[*]}"
else
  systemctl --user daemon-reload
  # shellcheck disable=SC2086
  systemctl --user enable --now ${servicios[*]}
  echo "  servicios activados: ${servicios[*]}"
fi

# ----------------------------------------------------------------- las direcciones
IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || true)"
[ -z "$IP" ] && IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"

echo
ESQUEMA="http"
[ -n "$CERT" ] && ESQUEMA="https"
echo "=== listo: este equipo es el del estudio ==="
echo "  en este equipo:  ${ESQUEMA}://127.0.0.1:${PUERTO}/"
[ -n "$IP" ] && echo "  para los otros:  ${ESQUEMA}://${IP}:${PUERTO}/    ← esta es la dirección que se les da"
if [ "$ESQUEMA" = "http" ]; then
  echo "  aviso: sin certificado (--cert) el panel va en HTTP y las contraseñas viajan en claro"
  echo "         por la red de la oficina. Generá uno con scripts/certificado_local.sh"
fi
echo "  cada persona entra con su correo y su contraseña: cada uno ve lo suyo y todo queda"
echo "  firmado en la bitácora."
echo
echo "=== antes de que lo usen los demás ==="
echo "  1. Que el servicio arranque sin que nadie inicie sesión (una sola vez, con tu clave):"
echo "       sudo loginctl enable-linger $USER"
echo "  2. Segundo factor para socio y administrador:"
echo "       $OPENLEGAL --db $BASE seguridad"
echo "  3. Abrir el puerto ${PUERTO} sólo a la red de la oficina en el cortafuegos del equipo,"
echo "     y NO publicar este puerto en internet: son expedientes de clientes."
echo "  4. Los respaldos quedan en $HOME/.openlegal/respaldos — llévalos además a un disco"
echo "     externo (docs/oficina.md lo explica, con cifrado)."
echo
echo "  ver el estado:   systemctl --user status openlegal-panel"
echo "  ver los avisos:  $OPENLEGAL --db $BASE notificar --estado"
echo "  desinstalar:     systemctl --user disable --now ${servicios[*]}"
