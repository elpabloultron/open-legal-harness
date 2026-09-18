#!/usr/bin/env bash
#
# Respaldo del CRM jurídico. Corre solo cada noche (ver scripts/instalar_oficina.sh) y
# también a mano cuando haga falta.
#
#   bash scripts/respaldo.sh                        # la base por defecto
#   bash scripts/respaldo.sh --db sqlite:///ruta    # una base concreta
#   bash scripts/respaldo.sh --destino /media/usb/respaldos
#
# En SQLite el respaldo usa la API de respaldo, así que se puede hacer **con el CRM
# funcionando** y queda consistente (copiar el archivo con `cp` mientras se escribe puede
# dejarlo roto). En PostgreSQL usa `pg_dump`. Los respaldos viejos se van rotando.
#
# Si hay `gpg` y OPENLEGAL_RESPALDO_CLAVE apunta a un archivo con la frase de cifrado, el
# respaldo sale cifrado; si no, avisa que la copia queda en claro — son datos de clientes.
set -euo pipefail

BASE="sqlite://${HOME}/.openlegal/estudio.db"
DESTINO="${HOME}/.openlegal/respaldos"
CONSERVAR=14

while [ $# -gt 0 ]; do
  case "$1" in
    --db) BASE="$2"; shift 2 ;;
    --destino) DESTINO="$2"; shift 2 ;;
    --conservar) CONSERVAR="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DESTINO"
SELLO="$(date +%Y%m%d-%H%M%S)"

respaldo_sqlite() {
  local origen="$1" destino="$2"
  python3 - "$origen" "$destino" <<'PY'
import pathlib, sqlite3, sys

origen, destino = sys.argv[1], sys.argv[2]
# `file://` con la ruta bien armada (tres barras en una ruta absoluta): así el respaldo
# abre la base en sólo lectura incluso si el CRM la tiene abierta.
uri = pathlib.Path(origen).resolve().as_uri() + "?mode=ro"
con = sqlite3.connect(uri, uri=True)
with sqlite3.connect(destino) as salida:
    con.backup(salida)          # consistente aunque el CRM esté escribiendo
con.close()
PY
}

case "$BASE" in
  sqlite://*)
    RUTA="${BASE#sqlite://}"
    if [ ! -f "$RUTA" ]; then
      echo "✗ no encuentro la base: $RUTA" >&2
      exit 1
    fi
    ARCHIVO="$DESTINO/estudio-$SELLO.db"
    respaldo_sqlite "$RUTA" "$ARCHIVO"
    ;;
  postgresql://*|postgres://*)
    command -v pg_dump >/dev/null 2>&1 || { echo "✗ falta pg_dump (paquete postgresql-client)" >&2; exit 1; }
    ARCHIVO="$DESTINO/estudio-$SELLO.sql"
    pg_dump --no-owner --clean --if-exists "$BASE" > "$ARCHIVO"
    ;;
  *)
    echo "✗ no entiendo la base: $BASE" >&2
    exit 2
    ;;
esac

TAMANO="$(du -h "$ARCHIVO" | cut -f1)"

# ------------------------------------------------------------------------ cifrado
if command -v gpg >/dev/null 2>&1 && [ -n "${OPENLEGAL_RESPALDO_CLAVE:-}" ] && [ -f "${OPENLEGAL_RESPALDO_CLAVE}" ]; then
  gpg --batch --yes --symmetric --cipher-algo AES256 \
      --passphrase-file "$OPENLEGAL_RESPALDO_CLAVE" -o "$ARCHIVO.gpg" "$ARCHIVO"
  rm -f "$ARCHIVO"
  ARCHIVO="$ARCHIVO.gpg"
  TAMANO="$(du -h "$ARCHIVO" | cut -f1)"
  echo "respaldo cifrado: $ARCHIVO ($TAMANO)"
elif command -v gpg >/dev/null 2>&1; then
  echo "respaldo: $ARCHIVO ($TAMANO)"
  echo "  aviso: quedó SIN cifrar. Son datos de clientes: si el respaldo sale de este equipo,"
  echo "         define OPENLEGAL_RESPALDO_CLAVE con un archivo de frase de cifrado (docs/oficina.md)."
else
  echo "respaldo: $ARCHIVO ($TAMANO)"
  echo "  aviso: no hay gpg instalado; la copia queda en claro."
fi

# ----------------------------------------------------------------------- rotación
mapfile -t VIEJOS < <(ls -1t "$DESTINO"/estudio-* 2>/dev/null | tail -n "+$((CONSERVAR + 1))")
if [ "${#VIEJOS[@]}" -gt 0 ]; then
  for viejo in "${VIEJOS[@]}"; do rm -f "$viejo"; done
  echo "  rotación: se borraron ${#VIEJOS[@]} respaldo(s) viejo(s), se conservan $CONSERVAR"
fi

echo "  quedan $(ls -1 "$DESTINO"/estudio-* 2>/dev/null | wc -l) respaldo(s) en $DESTINO"
