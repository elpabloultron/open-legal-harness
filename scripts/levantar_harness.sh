#!/usr/bin/env bash
#
# Levanta el harness (perfil `legal`) y deja la dirección con su token a mano.
#
#   bash scripts/levantar_harness.sh
#
# El token del harness rota en cada arranque, así que la dirección de ayer no sirve hoy y
# buscarla en el registro es una pérdida de tiempo. Esto la deja escrita, con permiso 600, en
# ~/.openlegal/url_harness.txt (y la imprime, para el que esté mirando la terminal).

set -euo pipefail

PERFIL="${DSH_PROFILE:-legal}"
PUERTO="${DSH_PORT:-8801}"
REGISTRO="${DSH_LOG:-/tmp/harness_legal.log}"
DESTINO="${OPENLEGAL_URL_HARNESS:-$HOME/.openlegal/url_harness.txt}"
URL=""
YA_CORRIA=0

if curl -s -o /dev/null "http://127.0.0.1:$PUERTO/"; then
    YA_CORRIA=1
    echo "el harness ya está escuchando en el puerto $PUERTO"
else
    echo "levantando el harness (perfil $PERFIL, puerto $PUERTO)…"
    # El modelo no habla directo con el proveedor: pasa por el proxy local del CRM, que avisa
    # y registra cada uso de IA (proveedor, modelo, país, caracteres y hash — nunca el
    # contenido). Si el proxy no está levantado, el harness lo dice al primer mensaje; se
    # arranca con `openlegal ia-proxy`. Para desactivarlo, quitá esta línea.
    # El modelo no habla directo con el proveedor: pasa por el proxy local del CRM, que avisa
    # y registra cada uso de IA (proveedor, modelo, país, caracteres y hash — nunca el
    # contenido). Va SIN /v1: dsh usa el protocolo `messages` y le agrega /v1/messages él
    # mismo; con /v1 quedaría /v1/v1/messages. El proxy se arranca con `openlegal ia-proxy`;
    # para desactivar el paso, comentá esta línea.
    export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-http://127.0.0.1:8790}"
    echo "  los pedidos al modelo pasan por el proxy de IA: $DEEPSEEK_BASE_URL"
    # setsid: el harness no tiene que morir cuando se cierra esta terminal ni la sesión que
    # lo lanzó (es un servicio de la oficina, no un comando de paso).
    setsid dsh --profile "$PERFIL" --no-open --host 127.0.0.1 --port "$PUERTO" \
        > "$REGISTRO" 2>&1 < /dev/null &
fi

# La dirección con el token la imprime el arranque, y puede tardar: hay que esperarla antes
# de darse por vencido (darse por vencido al primer intento fue el error de la primera
# versión de este guion).
for _ in $(seq 1 45); do
    URL="$(grep -o "http://127.0.0.1:$PUERTO/?token=[A-Za-z0-9_-]*" "$REGISTRO" 2>/dev/null | tail -1 || true)"
    [ -n "$URL" ] && break
    sleep 1
done

if [ -z "$URL" ] && [ "$YA_CORRIA" = 1 ] && [ -s "$DESTINO" ]; then
    # Ya estaba en marcha y ya habíamos guardado su dirección: esa sigue valiendo.
    URL="$(cat "$DESTINO")"
    echo "su dirección ya estaba guardada en $DESTINO"
fi

if [ -z "$URL" ]; then
    echo "el harness responde en el puerto $PUERTO, pero no pude leer su dirección de este" >&2
    echo "arranque (el registro quedó de un arranque anterior). Reinicialo así:" >&2
    echo "  pkill -f 'dsh --profile $PERFIL' && bash $0" >&2
    exit 1
fi

mkdir -p "$(dirname "$DESTINO")"
printf '%s' "$URL" > "$DESTINO"
chmod 600 "$DESTINO"

echo
echo "harness en marcha."
echo "  dirección con su token: $URL"
echo "  (la misma quedó en $DESTINO, permiso 600)"
echo
echo "En el sidebar, la fila «CRM Jurídico» pide la dirección del CRM y su token:"
echo "  dirección: http://127.0.0.1:8899"
echo "  token:     el de ~/.openlegal/token_panel.txt"
