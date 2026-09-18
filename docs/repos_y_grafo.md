# Los tres repos del grafo jurídico (y por qué no están sincronizados)

Verificado el 18-09-2026 con los comandos que aparecen al final. Si algo de acá cambia, hay que
volver a correrlos: este documento es una fotografía, no una promesa.

## Los tres repos

| Repo | Local | Versión | Qué es |
|---|---|---|---|
`open-legal-chile` | `~/Escritorio/Ultimaprensa/open-legal-chile` | **1.5.5** (en PyPI) | La suite completa: 64 herramientas MCP, 15 conectores institucionales (`*_connector.py`), 18 perfiles de agente y CLI. **Es el que consume el harness legal.** |
`legal-graphify` | `~/Escritorio/Ultimaprensa/legal-graphify` | **0.1.0** | Paquete modular del motor del grafo (core, extractors, visualizer, mcp, agents) con tests propios |
`graphify` | `~/Escritorio/Ultimaprensa/graphify` | **0.9.61** | **Fork de un proyecto externo** (© 2026 Safi Shamsi y contribuidores, paquete `graphifyy`). Genera `graphify-out/` (grafo de código + AST) |

## Lo que creíamos y lo que es

**Creíamos:** al actualizar `legal-graphify`, `open-legal-chile` se actualizaba solo.

**Es falso, por dos razones independientes:**

1. **No hay receptor.** `legal-graphify/.github/workflows/ci-sync.yml` emite un `repository_dispatch`
   con `event-type: legal-graphify-updated` hacia `elpabloultron/open-legal-chile`, pero los workflows
   de ese repo son `audit.yml`, `ci.yml`, `publish-pypi.yml` y `state-api-monitor.yml`: **ninguno
   escucha ese evento**.
2. **El dispatch no puede funcionar ni con receptor.** Usa `secrets.GITHUB_TOKEN`, que está acotado al
   repositorio donde corre y **no puede disparar workflows en otro repositorio** (para eso hace falta
   un PAT con permiso `repo`). Además está envuelto en `continue-on-error: true`, así que la falla es
   invisible en el CI.

**Y aunque el evento llegara, no habría nada que copiar:** los dos motores son **implementaciones
distintas**, no una copia. Comparten **1 solo** nombre de método de 19 y 18 — `__init__`.

| | `open-legal-chile` | `legal-graphify` |
|---|---|---|
Forma | archivo único `legal_graphify.py` (1.126 líneas) | paquete (1.620 líneas en 16 archivos) |
Clase | `LegalGraphifyEngine` | `LegalGraphEngine` |
Idioma del código | español (`construir_grafo_desde_doctrina`, `consultar_subgrafo`) | inglés (`extract_doctrine_directory`, `compute_pagerank`) |
Ranking y caminos | dentro de la clase | módulos aparte: `core/centrality.py`, `core/reasoning.py` |
MCP | uno solo, compartido con las otras 59 herramientas | servidor propio (`legal_graphify/mcp/server.py`) |

**Nombres de herramienta MCP: 64, no 65.** Lo verifiqué contando por el protocolo (initialize + tools/list). Ojo con el desfase documental: `README.md` dice 64 y `server.json` dice «55».

## El grafo, medido

- **Grafo jurídico**: 967 nodos, 1.366 aristas. Tipos: `legal_norm` 572, `legal_ruling` 205,
  `legal_doctrine` 105, `legal_work` 57, `legal_author` 15, `legal_procedure` 13. **Cero nodos
  aislados** (los 967 tienen al menos un enlace). Hubs: *Cumplimiento Forzado de las Obligaciones* (41),
  *Acción Reivindicatoria* (37), *Tutela Laboral* (28), autor René Ramos Pazos (22).
- **Grafo fusionado** (`graphify-out/graph.json`, 1.640 KB): 1.716 nodos = **967 jurídicos**
  (`_origin: legal_graphify`) + **749 del AST** (`_origin: ast`). El merge lo hace
  `integrar_con_graphify()`: agrega nodos y aristas al grafo de Graphify marcándolos y sin duplicar
  los que ya existen.
- **Ahorro de tokens, medido** (no el folleto): 2.800 → **83** tokens (despido injustificado),
  2.800 → **52** (audiencia preparatoria), 1.200 → **529** (recurso de protección). O sea **56% a
  98%** según institución, no un 85-95% uniforme.

## Desfases detectados

1. **`graphify-out/` es de la versión 0.9.55 y el fork local ya está en 0.9.61.** El artefacto quedó
   atrás respecto de la herramienta que lo genera.
2. **El grafo local duplica los enlaces** bajo dos claves (`edges` y `links`, 1.366 cada una) mientras
   la copia publicada en Hugging Face solo trae `edges`. Un consumidor que lea `links` funciona contra
   el archivo local y falla contra el publicado, y al revés.
3. **`calcular_ahorro_tokens` no devuelve el porcentaje**: devuelve los dos conteos de tokens, pero la
   clave del porcentaje sale vacía. El número que se cita (85-95%) hay que calcularlo por fuera.
4. **La copia publicada en Hugging Face está congelada** desde el 6 de septiembre (dataset
   `pablobenavidesj/doctrina-jurisprudencia-chile`: 59 `.md` contra 58 locales, y el grafo con 967
   nodos).

## Cómo volver a verificar todo esto

```bash
# el dispatch sin receptor
grep -rn 'repository_dispatch\|legal-graphify-updated' ~/Escritorio/Ultimaprensa/open-legal-chile/.github/workflows/ \
  || echo "sin receptor"
ls ~/Escritorio/Ultimaprensa/open-legal-chile/.github/workflows/

# los dos motores no comparten métodos
grep -o 'def [a-z_]*' ~/Escritorio/Ultimaprensa/open-legal-chile/legal_graphify.py | sort -u > /tmp/a
cat ~/Escritorio/Ultimaprensa/legal-graphify/legal_graphify/core/*.py | grep -o 'def [a-z_]*' | sort -u > /tmp/b
comm -12 /tmp/a /tmp/b

# el grafo y sus claves
python3 -c "import json,pathlib,collections; d=json.loads(pathlib.Path.home().joinpath('Escritorio/Ultimaprensa/open-legal-chile/data/legal_knowledge_graph.json').read_text()); print(len(d['nodes']), len(d.get('edges',[])), len(d.get('links',[])))"

# la versión con que se generó el artefacto
cat ~/Escritorio/Ultimaprensa/open-legal-chile/.claude/skills/graphify/.graphify_version
grep '^version' ~/Escritorio/Ultimaprensa/graphify/pyproject.toml
```

## Lo que se auditó y se arregló el 18-09-2026

Tres auditorías sobre los repos, con arreglos verificados por pruebas. Commits locales, **sin push**.

**`open-legal-chile` (`fb64c01`, suite 153 en verde):**

| Defecto | Evidencia | Arreglo |
|---|---|---|
El ahorro de tokens estaba **fabricado**: los nodos `obra` guardan su tamaño en `tokens_archivo`, así que el cálculo caía al default 2800 y reportaba ~97% | medido: 55,9% a 93,4% con el arreglo, contra el «85-95%» del docstring, la descripción MCP y el README | fallback `tokens_completos → tokens_archivo → 2800` |
Las aristas se guardaban **duplicadas** bajo `edges` y `links` (1.366 cada una; 976 KB) | grafo regenerado **topológicamente idéntico** (967 nodos, 1.366 aristas, mismos atributos): 976 → **704 KB** | una sola clave |
`integrar_con_graphify` **crasheaba** con `KeyError: 'links'` ante un grafo producido por su propio `guardar_grafo_json` | round-trip roto | acepta ambos esquemas |
`except (TypeError, Exception)` — un `except Exception` disfrazado — y `os.makedirs('')` con rutas relativas | `FileNotFoundError: ''` | corregidos |

**`legal-graphify` (`bf6a1c2`, suite 15 → 30 en verde):**

| Defecto | Consecuencia real | Arreglo |
|---|---|---|
`load_graph_json` tragaba toda excepción y devolvía un motor vacío | ante un archivo corrupto, cada consulta respondía «node not found» con exit 0 | un archivo ilegible levanta `ValueError`; «no existe» sigue siendo `False` |
`ingest` de un documento inexistente «funcionaba» | devolvía el nombre del archivo como si fuera markdown | `FileNotFoundError`, CLI con código distinto de cero |
El ejemplo del README **sobrescribía la semilla versionada** | 967/1366 → 972/1372 y git marcando el archivo como modificado | el agente ya no la pisa por defecto |
`nx.node_link_graph` sin el kwarg `edges` | `FutureWarning` hoy, y el default cambia en NetworkX 3.6 (el CI cubre 3.10-3.13) | queda explícito |
`python -m legal_graphify` no existía | solo funcionaba el console script | `__main__.py` |

## El riesgo que estaba escondido (ya desactivado)

El paquete `legal-graphify 0.1.0` estaba **instalado editable dentro del venv de `open-legal-chile`**, y las dos distribuciones reclaman el mismo nombre de import (`legal_graphify`), porque `setup.py:47` lo declara en `py_modules`. El resultado depende del directorio de trabajo:

```
desde el repo → legal_graphify.py        (el monolito, lo que el MCP necesita)
desde /tmp    → legal-graphify/legal_graphify/__init__.py   (el paquete, sin LegalGraphifyEngine)
```

El harness **sobrevive solo porque lanza el MCP con `cwd=open-legal-chile`**. Si alguien cambiaba ese `cwd`, no se caía solo `graphify_*`: **no arrancaba el servidor de 64 herramientas.** Desinstalé el paquete del venv; ahora el import resuelve al monolito desde cualquier directorio y el servidor sigue entero (64 herramientas, `graphify_god_nodes` respondiendo).

## Lo que falta decidir (no lo decide un agente)

**1. El motor canónico: RESUELTO.** El paquete `legal-graphify` quedó **archivado en GitHub** (`isArchived: true`, 18-09-2026) después de rescatar su única pieza de valor —el parser de artículos de códigos— y arreglarla (su patrón no sobrevivía a una coma, así que sobre un código real encontraba casi ningún artículo). Vive en `open-legal-chile` como `extract_articulos_de_codigo()` e `ingerir_codigo_bcn()`. Un solo motor, un solo repo para el producto.

**2. Los números de marketing: RESUELTO Y PUBLICADO.** La página de PyPI (que es el README) decía «85%-95%», «> 80%» y «55 herramientas». Ahora dice el rango medido (**31,9 % a 90,5 %**, mediana 74,1 %) y las 64 herramientas. Todo número publicado sale de `docs/medicion_tokens.json`, se reproduce con `scripts/medir_ahorro_tokens.py` y se coteja con un verificador que además falla si alguien reintroduce una cifra vieja.

**3. `ci-sync.yml`: sin resolver, y ya no importa.** El evento sigue sin receptor, pero el repo que lo emitía está archivado, así que no hay desincronización posible. **Decide el usuario** si borra el workflow en el repo archivado (es de solo lectura: ya no se puede).

**4. `numpy` y PageRank: RESUELTO.** `numpy` y `scipy` son dependencias declaradas; el ranking es PageRank real y el payload declara `ordenado_por`, avisando cuando no puede calcularlo. El ranking **cambió** (antes: Cumplimiento Forzado, Reivindicación, Tutela Laboral; ahora: Responsabilidad Extracontractual, Daño Reparable, Dolo Civil) porque el 83 % de los nodos no tiene aristas de salida: PageRank y grado no ordenan igual, y por eso cada entrada trae las dos métricas.

**5. El fork de `graphify`: NO se mantiene.** Es una herramienta de terceros (0.9.63, publicada en PyPI como `graphifyy`) y este proyecto solo **consume** su salida; su commit de showcase quedó **36 commits atrás con historia divergente** y llevaba dentro las cifras falsas (85-95 %, «55 herramientas»), así que enviarlo upstream habría publicado justo lo corregido. El contenido se reescribió con los números medidos en `open-legal-chile/docs/integracion_graphify.md`, que es donde documenta algo propio. Si algún día se quiere reconocimiento en el proyecto original, corresponde un PR corto y factual basado en `upstream/main` — nunca el commit actual.

## El agujero que apareció al verificar el paquete publicado

Instalando `openlegal-chile` **como lo hace un usuario** (sin clonar el repo) quedó a la vista lo peor de todo: el wheel de PyPI **solo llevaba los módulos**. Las 64 herramientas estaban, pero `graphify_*` respondía «no encontrado» a cualquier tema y `doctrina_search` no tenía índice: las dos funciones que justifican la suite, huecas, y el motor callado. El corpus (352 KB, 58 obras) y el grafo (704 KB) vivían solo en el repositorio.

Corregido en **1.5.4**: `doctrina/` y `data/` se declaran como paquetes de datos (namespace packages), así que setuptools los instala junto a los módulos, en site-packages, que es exactamente donde el motor los busca — sin duplicar archivos ni cambiar la resolución de rutas. El índice FTS5 no viaja: se construye solo en la primera búsqueda. El wheel pasó de 52 a 111 archivos, y se verificó instalándolo en un entorno limpio: 58 obras, 967 nodos / 1.366 aristas, consulta real al 86,6 %, PageRank real y 64 herramientas.

Y ya nada falla en silencio: un grafo vacío o un artefacto ausente dejan aviso con la causa y la salida (1.5.3).


## Pendientes reales (al 18-09-2026)

- **CI del paquete**: instalar `.[dev,mcp]` en vez de `.[dev]` para que la única prueba del servidor
  MCP deje de saltarse en cada corrida. Verificado que pasa con fastmcp 4.0.5, pero
  `fastmcp>=0.1.0` está sin fijar: conviene anclar la versión antes de meterlo, o el CI puede caerse
  con un release del upstream.
- **Feriados 2027** del CRM sin consolidar: el cómputo del harness avisa cuando un plazo los pisa, en
  vez de inventar el feriado que falta (deliberado).
- **Criterio del sábado** para el estudio: el CRM lo parametriza (`sabado_habil`) y escribe la regla
  aplicada en el resultado; falta que el estudio decida si cuenta sábados como hábiles.
- **Fecha real de la cédula** en el caso C-1234-2026: el 01-10/02-10-2026 es provisional hasta tener
  la fecha de la diligencia.

## Historial: cómo se llegó acá

La primera sospecha fue que los dos motores estaban **duplicados** (un archivo suelto contra un
paquete modular). La auditoría mostró algo peor: eran **dos implementaciones distintas** —compartían
un solo nombre de método de 19 y 18, `__init__`— y el paquete, aunque más ordenado, **no podía
reconstruir el grafo** (947 nodos / 1.215 aristas sin vías procesales ni atributos de tipo contra
967 / 1.366). Su archivo de datos era un artefacto del otro repo, y por eso sus pruebas pasaban.
De ahí las tres opciones que se evaluaron (paquete canónico: 16-20 h y riesgo alto; monolito canónico
con el paquete archivado: 1-2 h y riesgo cero; los dos vivos con prueba de paridad: costo permanente
y riesgo de verde falso). Se eligió la segunda, y la prueba de paridad quedó descartada como red
precisamente porque 3 de las 5 herramientas ya coincidían en números: una prueba laxa habría
certificado la divergencia en `god_nodes` en lugar de evitarla.

