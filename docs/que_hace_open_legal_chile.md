# Qué hace Open Legal Chile

> Borrador para revisión. Todas las cifras están medidas sobre el repositorio y las fuentes, no
> estimadas. Donde algo no funciona, lo dice: un catálogo honesto sirve; uno optimista, no.

## En una frase

Le da a un agente de IA **fuentes jurídicas chilenas reales** y el **expediente bajo control**,
funcionando en tu propia máquina: para citar derecho vigente en vez de inventarlo, y para no perder
plazos.

## Qué entrega

**1. Normas, por artículo y en su versión histórica.**
Consulta directa al portal XML oficial de la Biblioteca del Congreso Nacional. Permite pedir el
texto **vigente a una fecha determinada** (`bcn_get_codigo_historico`), que es lo que se necesita
para juzgar un contrato de 2021 con la ley de 2021.

**2. Criterios de la Administración y jurisprudencia.**
Conectores a diez instituciones: BCN, Contraloría (dictámenes, instructivos, auditorías), Dirección
del Trabajo, SII, CMF, TDLC, Poder Judicial, Superintendencia del Medio Ambiente, CNE y Panel de
Expertos. Cada uno con su caché local y su política de avisos.

**3. Un grafo de conocimiento jurídico.**
967 nodos (572 artículos, 205 jurisprudencias, 105 instituciones, 57 obras doctrinales, 15 autores,
13 vías procesales) y 1.366 relaciones: permite preguntar **qué normas fundamentan una tesis, qué
jurisprudencia se apoya en un artículo o qué vía procesal corresponde**, en vez de sólo buscar texto.

**4. Corpus doctrinal con búsqueda de texto completo.**
58 obras en Markdown dentro del paquete, más una biblioteca exportada de 234 documentos.

**5. Lectura de expedientes, incluidos los escaneados.**
Extrae texto por página conservando la referencia a fojas, con OCR de tres motores (nativo,
Tesseract y RapidOCR). Cada página informa método, motor, `ok` y longitud: una página que no se pudo
leer **se marca**, no se disimula.

**6. Gestión de la causa (el CRM).**
Causas, plazos y audiencias con feriados y regla de sábado hábil (art. 59 CPC), usuarios con
permisos por rol, panel web local y dos modos de operación (SQLite para uso individual, PostgreSQL
para oficina).

## Cómo se usa

- **Con un agente**: se expone como servidor MCP (`openlegal-chile`), así que cualquier agente
  compatible lo consulta como herramientas.
- **Desde la terminal**: tiene CLI y cada conector puede consultarse por separado.
- **Instalación**: `pip install openlegal-chile`; el paquete incluye el corpus y el grafo. Para leer
  fotografías de documentos conviene además `pip install "openlegal-chile[ocr]"` (RapidOCR).

## Límites, dichos con nombre y apellido

- **No es una base de datos jurídica viva.** El grafo es una fotografía del corpus doctrinal
  incorporado, no se actualiza solo.
- **No decide.** Cita, ordena y advierte; el criterio es del abogado.
- **El buscador de la BCN no es de texto libre**: el portal sólo publica consultas por `idNorma` o
  número de ley, así que resuelve números de ley y nombres de códigos. Cuando no cubre un término,
  lo dice explícitamente en vez de devolver una lista vacía.
- **Depende de portales públicos.** Cuando una institución reordena su sitio, la fuente se cae. Dos
  índices del SII (resoluciones exentas y oficios) están hoy con dirección 404 y el conector lo
  informa como aviso: una fuente caída no es lo mismo que la inexistencia de documentos.
- **Nada sale de tu máquina.** Todo corre local, que es lo que permite trabajar con expedientes con
  datos personales.

## Estado de calidad (18-09-2026)

- Versión **1.5.9** publicada en PyPI.
- **188 pruebas** automatizadas, verdes en dos entornos distintos y en el CI (Ubuntu y Windows,
  Python 3.10 a 3.14).
- Auditoría estática (bandit) sin hallazgos.
- Los últimos tres parches (1.5.6 a 1.5.9) nacieron de usar el sistema en un caso real: una página
  que no se leía y se informaba como «éxito», un contador que medía el mensaje de error en lugar del
  documento, un buscador que no buscaba y tres conectores que devolvían vacío.
