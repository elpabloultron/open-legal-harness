#!/usr/bin/env bash
# Demo local del CRM: un estudio de ejemplo con administrador, secretaria y dos
# abogados, corriendo sobre SQLite (sin Docker y sin salir de la máquina).
#
#   bash scripts/demo_local.sh            # crea/recarga la demo
#   bash scripts/demo_local.sh --servir   # además levanta el panel
#   OPENLEGAL_DEMO_DB="sqlite:////tmp/otra.db" bash scripts/demo_local.sh   # en otra base
#
# Ojo: recarga la base (la borra y la vuelve a crear). No hay bandera --db:
# para apuntar a otra base se usa la variable OPENLEGAL_DEMO_DB.
#
# Después, para que los usuarios puedan entrar con correo y contraseña:
#   openlegal --db "$DB" usuario clave --email admin@estudio.cl
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${OPENLEGAL_DEMO_DB:-sqlite:///$HOME/.openlegal/demo.db}"
ARCHIVO="${DB#sqlite:///}"
OPENLEGAL="$AQUI/.venv/bin/openlegal"

[[ -x "$OPENLEGAL" ]] || { echo "falta el entorno: corre antes  uv venv .venv && uv pip install -e '.[ui]'" >&2; exit 1; }

echo "== recreando la base de demostración en $ARCHIVO"
mkdir -p "$(dirname "$ARCHIVO")"
rm -f "$ARCHIVO" "$ARCHIVO-wal" "$ARCHIVO-shm"

run() { "$OPENLEGAL" --db "$DB" "$@"; }

run init >/dev/null
run estudio crear  --nombre "Estudio Demo" --rut 76.111.222-3 --modo oficina
run usuario crear  --nombre "Sofía Soto"  --email socia@estudio.cl      --rol socio          --sin-password
run usuario crear  --nombre "Marta Rivas" --email admin@estudio.cl      --rol administrador  --sin-password
run usuario crear  --nombre "Ana Pérez"   --email ana@estudio.cl        --rol abogado        --sin-password
run usuario crear  --nombre "Luis Rojas"  --email luis@estudio.cl       --rol abogado        --sin-password
run usuario crear  --nombre "Carmen Díaz" --email secretaria@estudio.cl --rol administrativo --sin-password
# Usuario propio de la carga inicial: los registros sembrados quedan firmados por él y no por
# la socia. Sin esto, el agente (que corre como socia@estudio.cl) no puede distinguir un plazo
# que ya existía de uno que creó él, y termina atribuyendo plazos viejos al documento que lee.
run usuario crear  --nombre "Carga inicial (importación)" --email carga@estudio.cl --rol paralegal --sin-password
run cliente crear  --nombre "Constructora Andes SpA"        --rut 76.543.210-3 --tipo juridica --representante "Jorge Fuentes"
run cliente crear  --nombre "Inmobiliaria del Sur Ltda."    --rut 77.222.333-1 --tipo juridica --representante "Marcela Ruiz"
run causa crear    --caratula "Pérez con Andes SpA" --cliente 1 --rol-rit "C-1234-2026" \
                   --tribunal "1° Juzgado del Trabajo de Santiago" --materia laboral \
                   --contraparte "Andes SpA" --cuantia 8500000
run causa crear    --caratula "Rojas con Inmobiliaria del Sur Ltda." --cliente 2 --rol-rit "C-9876-2026" \
                   --tribunal "2° Juzgado Civil de Santiago" --materia civil \
                   --contraparte "Inmobiliaria del Sur Ltda." --cuantia 24000000
run causa asignar  --causa 1 --a-usuario 3 --rol-en-causa responsable
run causa asignar  --causa 2 --a-usuario 4 --rol-en-causa responsable
# Los permisos son POR CAUSA: sin asignarlo al equipo, la carga inicial no puede crear plazos.
run causa asignar  --causa 1 --a-usuario 6 --rol-en-causa colaborador
run causa asignar  --causa 2 --a-usuario 6 --rol-en-causa colaborador
run plazo crear    --causa 1 --descripcion "Contestar demanda" --dias 8 --notificacion 2026-09-17   --usuario carga@estudio.cl
run plazo crear    --causa 2 --descripcion "Contestar traslado de apelación" --dias 5 --notificacion 2026-09-21 --usuario carga@estudio.cl
run audiencia crear --causa 1 --tipo "Audiencia preparatoria" --fecha 2026-10-05 --hora 09:30 \
                    --modalidad remota --lugar "https://zoom.us/j/123456789" --usuario carga@estudio.cl
run audiencia crear --causa 2 --tipo "Comparendo de contestación" --fecha 2026-09-30 --hora 11:00 --usuario carga@estudio.cl

echo
run usuario listar
run panel
run vencimientos --desde 2026-09-01 --dias 60

if [[ "${1:-}" == "--servir" ]]; then
  echo
  echo "== levantando el panel (Ctrl-C para detener)"
  exec "$OPENLEGAL" --db "$DB" serve --port "${OPENLEGAL_PANEL_PORT:-8899}"
fi

echo
echo "Listo. Para entrar con correo y contraseña, fija una clave por usuario:"
echo "  $OPENLEGAL --db \"$DB\" usuario clave --email admin@estudio.cl"
echo "Y levanta el panel con:  $OPENLEGAL --db \"$DB\" serve --port 8899"
