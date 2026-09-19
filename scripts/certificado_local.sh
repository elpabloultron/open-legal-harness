#!/usr/bin/env bash
#
# Genera el certificado del panel para la red de la oficina.
#
#   bash scripts/certificado_local.sh                      # usa la IP de este equipo
#   bash scripts/certificado_local.sh --host crm.oficina.local
#   bash scripts/certificado_local.sh --host 192.168.1.50 --dias 825
#
# Por qué hace falta: el panel en la oficina queda accesible desde los otros equipos, y por
# HTTP las contraseñas y los expedientes viajan en claro dentro de la red. Con este
# certificado el panel va en HTTPS.
#
# No es un certificado de una autoridad (no hace falta: no hay un dominio público de por
# medio). Es un certificado propio de la oficina: cada navegador lo va a marcar como «no
# confiable» la primera vez y hay que aceptarlo — una vez por equipo, y conviene que el
# estudio sepa por qué. Sirve igual para lo que importa: cifrar lo que circula por la red.
set -euo pipefail

HOST=""
DIAS=825
DESTINO="${HOME}/.openlegal/tls"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --dias) DIAS="$2"; shift 2 ;;
    --destino) DESTINO="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$HOST" ]; then
  HOST="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || true)"
  [ -z "$HOST" ] && HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
if [ -z "$HOST" ]; then
  echo "✗ no pude deducir la dirección de este equipo: pasala con --host" >&2
  exit 1
fi

command -v openssl >/dev/null 2>&1 || { echo "✗ falta openssl" >&2; exit 1; }

mkdir -p "$DESTINO"
chmod 700 "$DESTINO"
CERT="$DESTINO/panel.crt"
CLAVE="$DESTINO/panel.key"

if [ -f "$CERT" ]; then
  echo "  ya existe un certificado en $CERT"
  echo "  (se va a reemplazar; si el navegador ya lo aceptó, va a pedir aceptarlo de nuevo)"
fi

# El certificado lleva la dirección en «subjectAltName»: sin eso, el navegador no lo acepta
# para esa dirección, aunque sea el mismo equipo.
openssl req -x509 -nodes -newkey rsa:2048 -days "$DIAS" \
  -keyout "$CLAVE" -out "$CERT" \
  -subj "/CN=${HOST}/O=CRM Juridico/O=Open Legal Harness" \
  -addext "subjectAltName=DNS:${HOST},IP:${HOST},DNS:localhost,IP:127.0.0.1" 2>/dev/null

chmod 600 "$CLAVE"
chmod 644 "$CERT"

echo
echo "=== certificado listo ==="
echo "  para:     $HOST (y localhost)"
echo "  archivo:  $CERT"
echo "  clave:    $CLAVE (permisos 600)"
echo "  vence en: ${DIAS} días"
echo
echo "  levantar el panel con HTTPS:"
echo "    openlegal --db \"\$DB\" serve --host 0.0.0.0 --port 8899 --cert $CERT --key $CLAVE"
echo
echo "  y en el servicio del sistema (scripts/instalar_oficina.sh --cert $CERT):"
echo "    systemctl --user restart openlegal-panel"
echo
echo "  los otros equipos entran en:  https://${HOST}:8899/"
echo "  el navegador va a avisar que el certificado no es de confianza: es el esperado en un"
echo "  certificado propio de la oficina. Se acepta una vez por equipo."
