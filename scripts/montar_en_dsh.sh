#!/usr/bin/env bash
#
# Monta Open Legal Harness en un perfil de DeepSeek Harness (dsh).
#
# Deja el perfil con las cuatro piezas en su lugar:
#   1. el CRM (servidor MCP propio, lee y escribe en la base local del estudio)
#   2. la fila «CRM Jurídico» y su panel (plugin cliente dsh-plugin/)
#   3. la marca: la balanza y «Open Legal Harness» (plugin cliente dsh-brand/)
#   4. la biblioteca jurídica open-legal-chile (69 herramientas MCP), si está
#
# Por qué un script y no un archivo de configuración para copiar: las rutas de los
# servidores MCP son ABSOLUTAS y distintas en cada equipo, y el archivo de perfil no
# admite variables. El script las resuelve en la máquina donde corre y no duplica
# entradas si ya estaban.
#
# Uso:
#   scripts/montar_en_dsh.sh [--perfil legal] [--puerto 8801] [--sin-biblioteca] [--sin-marca]
#
set -euo pipefail

PERFIL="legal"
PUERTO="8801"
CON_BIBLIOTECA=1
CON_MARCA=1

while [ $# -gt 0 ]; do
  case "$1" in
    --perfil) PERFIL="$2"; shift 2 ;;
    --puerto) PUERTO="$2"; shift 2 ;;
    --sin-biblioteca) CON_BIBLIOTECA=0; shift ;;
    --sin-marca) CON_MARCA=0; shift ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PERFIL_DIR="$DSH_HOME/profiles/$PERFIL"
PARCHE="$PERFIL_DIR/cordis.patch.yml"

paso() { printf '\n\033[1m%s\033[0m\n' "$*"; }
aviso() { printf '  · %s\n' "$*"; }
error() { printf '  ✗ %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- 1. el harness
paso "1. DeepSeek Harness"
if ! command -v dsh >/dev/null 2>&1; then
  error "no encontré 'dsh'. Instálalo con:
    npm install -g --allow-scripts=@deepseek-ai/dsh-subprocess-local,koffi,node-pty,@google/genai,protobufjs @deepseek-ai/dsh
  (el flag --allow-scripts no es opcional: sin él npm se salta los binarios nativos y
  el harness queda sin terminal ni subprocesos)."
fi
aviso "dsh $(dsh --version 2>/dev/null | head -1)"

# ------------------------------------------------------------- 2. el CRM local
paso "2. El CRM (openlegal)"
EJECUTABLE="$REPO/.venv/bin/openlegal-crm"
if [ ! -x "$EJECUTABLE" ]; then
  aviso "no hay entorno en $REPO/.venv; lo creo e instalo el CRM editable"
  if command -v uv >/dev/null 2>&1; then
    uv venv "$REPO/.venv" >/dev/null
    uv pip install --python "$REPO/.venv/bin/python" -q -e "$REPO[ui]"
  else
    python3 -m venv "$REPO/.venv"
    "$REPO/.venv/bin/pip" install -q -e "$REPO[ui]"
  fi
fi
[ -x "$EJECUTABLE" ] || error "el CRM no quedó ejecutable en $EJECUTABLE"
aviso "ejecutable: $EJECUTABLE"
BASE="${OPENLEGAL_MCP_DB:-sqlite:///$HOME/.openlegal/estudio.db}"
mkdir -p "$(dirname "${BASE#sqlite:///}")"
aviso "base del estudio: $BASE"
"$EJECUTABLE" --db "$BASE" init >/dev/null 2>&1 || true
aviso "esquema al día"

# ------------------------------------------------------------ 3. el perfil dsh
paso "3. El perfil «$PERFIL»"
if [ ! -d "$PERFIL_DIR" ]; then
  aviso "no existía: lo creo desde la plantilla oficial web"
  aviso "esto instala la pila de plugins del harness con pnpm: la primera vez puede tardar varios minutos"
  dsh --profile "$PERFIL" --from-default-profile web 2>&1 | tail -3 | sed 's/^/    /' || error "no pude crear el perfil"
else
  aviso "ya existía"
fi

# --------------------------------------------------- 4. los plugins de interfaz
paso "4. Los plugins de interfaz (pnpm los enlaza al repo; puede tardar)"
if ! dsh plugin --profile "$PERFIL" add "link:$REPO/dsh-plugin" 2>&1 | tail -2 | sed 's/^/    /'; then
  error "no pude montar el plugin del CRM"
fi
aviso "fila «CRM Jurídico»: $REPO/dsh-plugin"
if [ "$CON_MARCA" = "1" ]; then
  if [ ! -f "$REPO/dsh-brand/lib/client.js" ]; then
    aviso "el plugin de marca no está construido; lo construyo"
    ( cd "$REPO/dsh-brand" && [ -d node_modules ] || ln -sfn ../dsh-plugin/node_modules node_modules; node scripts/build.mjs >/dev/null ) \
      || error "el build de la marca falló"
  fi
  if ! dsh plugin --profile "$PERFIL" add "link:$REPO/dsh-brand" 2>&1 | tail -2 | sed 's/^/    /'; then
    error "no pude montar el plugin de marca"
  fi
  aviso "marca (balanza + Open Legal Harness): $REPO/dsh-brand"
else
  aviso "marca omitida por --sin-marca: se verá la del harness"
fi

# ------------------------------------------------ 5. los servidores MCP (parche)
paso "5. Los servidores MCP del perfil"
[ -f "$PARCHE" ] || : > "$PARCHE"
agregar_mcp() { # id, nombre, comando, args_yaml, cwd(opcional), env_yaml(opcional)
  local id="$1" nombre="$2" comando="$3" args="$4" cwd="${5:-}" env="${6:-}"
  if grep -q "id: $id" "$PARCHE" 2>/dev/null; then
    aviso "$id ya estaba en el parche; no lo duplico"
    return
  fi
  # La plantilla del perfil trae los comentarios y un `[]` (array vacío). Si se agrega
  # una entrada después de ese `[]`, el YAML queda inválido y dsh no arranca: hay que
  # sacarlo la primera vez.
  if grep -qE '^[[:space:]]*\[[[:space:]]*\][[:space:]]*$' "$PARCHE" 2>/dev/null; then
    grep -vE '^[[:space:]]*\[[[:space:]]*\][[:space:]]*$' "$PARCHE" > "$PARCHE.tmp"
    mv "$PARCHE.tmp" "$PARCHE"
    aviso "quité el array vacío de la plantilla (si no, el YAML no parsea)"
  fi
  {
    printf '\n- insert:\n'
    printf '    - id: %s\n' "$id"
    printf "      name: '%s'\n" "$nombre"
    printf '      config:\n'
    printf '        serverName: %s\n' "${id#mcp-}"
    printf '        transport: stdio\n'
    printf "        command: '%s'\n" "$comando"
    printf '        args: %s\n' "$args"
    [ -n "$cwd" ] && printf "        cwd: '%s'\n" "$cwd"
    if [ -n "$env" ]; then
      printf '        env:\n'
      printf '%s\n' "$env" | sed 's/^/          /'
    fi
  } >> "$PARCHE"
  aviso "$id agregado"
}

agregar_mcp "mcp-openlegal-crm" "@deepseek-ai/dsh-mcp-client" "$EJECUTABLE" "[mcp]" "" \
"OPENLEGAL_MCP_DB: '$BASE'
OPENLEGAL_MCP_USUARIO: '${OPENLEGAL_MCP_USUARIO:-socia@estudio.cl}'"

if [ "$CON_BIBLIOTECA" = "1" ]; then
  # La biblioteca puede venir del paquete de PyPI (preferido: es lo que se actualiza)
  # o de un clon del repo, si el usuario desarrolla ahí.
  OLC_MCP="$(command -v openlegal-mcp || true)"
  OLC_DIR="${OPENLEGAL_CHILE_DIR:-$HOME/Escritorio/Ultimaprensa/open-legal-chile}"
  if [ -n "$OLC_MCP" ]; then
    agregar_mcp "mcp-open-legal-chile" "@deepseek-ai/dsh-mcp-client" "$OLC_MCP" "[]" "" \
      "TESSDATA_PREFIX: '${TESSDATA_PREFIX:-$HOME/.local/share/tessdata}'"
  elif [ -x "$OLC_DIR/.venv/bin/python" ] && [ -f "$OLC_DIR/mcp_server.py" ]; then
    agregar_mcp "mcp-open-legal-chile" "@deepseek-ai/dsh-mcp-client" "$OLC_DIR/.venv/bin/python" "[mcp_server.py]" "$OLC_DIR" \
      "TESSDATA_PREFIX: '${TESSDATA_PREFIX:-$HOME/.local/share/tessdata}'"
  else
    aviso "no encontré la biblioteca jurídica. Para tenerla:"
    aviso "  pip install 'openlegal-chile[ocr]'   (o clona el repo en $OLC_DIR)"
    aviso "y vuelve a correr este script, o móntala a mano con --sin-biblioteca en mente"
  fi
fi

# ------------------------------------------------------------- 6. verificación
paso "6. Verificación"
if ! dsh --profile "$PERFIL" --dump-config > /tmp/montaje_dump.txt 2>/tmp/montaje_err.txt; then
  error "dsh no pudo componer el perfil. Su error:
$(head -2 /tmp/montaje_err.txt | sed 's/^/    /')
  Revisa $PARCHE"
fi
MONTADOS="$(grep -c -e 'openlegal-crm' -e 'openlegal-brand' -e 'open-legal-chile' /tmp/montaje_dump.txt || true)"
[ "$MONTADOS" -ge 2 ] || error "la composición del perfil no muestra lo que acabo de montar; revisa $PARCHE"
aviso "la composición del perfil incluye $MONTADOS referencias a lo montado"

cat <<FIN

Listo. Para usarlo:

  dsh --profile $PERFIL --no-open --host 127.0.0.1 --port $PUERTO
  # la URL con el token sale en el arranque y rota en cada reinicio

  openlegal-crm --db "$BASE" serve --port 8899     # el panel del CRM, si lo quieres aparte

Y para las actualizaciones del harness principal (que no tocan esta marca):

  npm install -g --allow-scripts=@deepseek-ai/dsh-subprocess-local,koffi,node-pty,@google/genai,protobufjs @deepseek-ai/dsh
  # reinicia el perfil: la balanza y el CRM siguen donde están
FIN
