# Los tres repos del grafo jurídico (y por qué no están sincronizados)

Verificado el 18-09-2026 con los comandos que aparecen al final. Si algo de acá cambia, hay que
volver a correrlos: este documento es una fotografía, no una promesa.

## Los tres repos

| Repo | Local | Versión | Qué es |
|---|---|---|---|
`open-legal-chile` | `~/Escritorio/Ultimaprensa/open-legal-chile` | **1.5.2** (en PyPI) | La suite completa: 65 herramientas MCP, 20 conectores, CLI. **Es el que consume el harness legal.** |
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
MCP | uno solo, compartido con las otras 60 herramientas | servidor propio (`legal_graphify/mcp/server.py`) |

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

## Lo que falta decidir (no lo decide un agente)

**¿Cuál de los dos motores es el canónico?** Hoy el harness usa el monolito de `open-legal-chile`
(ya está en PyPI y las 5 herramientas `graphify_*` dependen de él). El paquete `legal-graphify` es
más limpio de estructura pero no lo consume nadie. Mientras no se elija uno y el otro se archive o
se haga depender de él, **cada mejora hay que escribirla dos veces** —o se escribe una y el otro
queda mintiendo sobre lo que sabe hacer.
