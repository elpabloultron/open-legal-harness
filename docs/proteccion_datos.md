# Protección de datos y despliegue local

Documento de trabajo del proyecto, no un informe legal firmado. Las citas están verificadas
contra el Diario Oficial y la Biblioteca del Congreso, pero **los artículos hay que leerlos en el
texto oficial antes de usarlos como fundamento**.

## 1. Qué norma aplica y desde cuándo

| Hito | Fecha | Fuente |
|---|---|---|
| Publicación de la **Ley 21.719** (reforma la Ley 19.628 y crea la Agencia de Protección de Datos Personales, APDP) | 13-dic-2024 | Diario Oficial, CVE 2583630 |
| **Entrada en vigencia vigente: 1-dic-2026** (art. primero transitorio: «el día primero del mes vigésimo cuarto posterior a la publicación») | 1-dic-2026 | Ley 21.719 |
| Ley 21.806: ajustes institucionales (adelantó la designación del Consejo Directivo). **No postergó la vigencia** | 5-feb-2026 | Ley 21.806 |
| **Boletín 18.623-07** (Mensaje 110-374, firmado el 31-ago-2026, ingresado al Senado el 1-sep-2026 con *suma urgencia*): propone postergar la vigencia al **1-dic-2027**, subir el Consejo de 3 a 5, designarlo 12 meses antes y extender la amonestación del primer año a toda empresa | en tramitación, sin informe ni votación | Tramitación del Senado |

**Conclusión operativa:** la fecha que rige hoy es **1 de diciembre de 2026**. Mientras el
proyecto no sea ley publicada en el Diario Oficial, nadie puede decir que «se postergó». Hay que
diseñar para diciembre de 2026 y celebrar si llega el año extra.

Tres precisiones donde casi todas las guías comerciales se equivocan:

1. **No existe el plazo de 72 horas.** El art. 14 sexies exige reportar «por los medios más
   expeditos posibles y sin dilaciones indebidas». Las 72 horas son del RGPD europeo.
2. **Citar bien**: las obligaciones están en la **Ley 19.628 en su texto reformado por la Ley
   21.719** (así lo citan las resoluciones del Ministerio de Economía). Quien cita «el art. 16 bis
   de la Ley 21.719» copió de una guía sin abrir el texto.
3. **El «registro de actividades de tratamiento» (RAT) no aparece como obligación expresa para
   responsables privados** en el texto de la ley. Lo que sí existe: registrar las comunicaciones
   de brechas (art. 14 sexies) y el Registro Nacional de Sanciones de la Agencia (art. 39). Dicho
   eso, el principio de responsabilidad proactiva obliga a **poder demostrar** el cumplimiento,
   así que documentar es prudente aunque no sea un deber autónomo.

## 2. Por qué el diseño local nos deja bien parados

El CRM es *local-first*: escucha solo en `127.0.0.1`, no llama a ningún servicio externo y la base
vive en el disco del estudio. Eso elimina de raíz la mayoría de los riesgos que la ley castiga.

| Obligación | Cómo queda cubierta | Estado |
|---|---|---|
| **Base de licitud** (art. 12 y 13) | Para causas se usa el art. **13 letra e)**: tratamiento necesario «para la formulación, ejercicio o defensa de un derecho ante los tribunales de justicia u órganos públicos». No se requiere consentimiento del cliente ni de la contraparte | ✅ por diseño |
| **Deber de secreto** (art. 14 bis) | Nada sale de la máquina; los roles limitan quién ve cada causa y los documentos internos no se publican | ✅ por diseño |
| **Minimización y finalidad** (art. 14 letras b y c) | El modelo guarda solo lo que sirve a la causa: sin campos de marketing, sin perfiles, sin rastreo | ✅ por diseño |
| **Medidas de seguridad** (art. 14 quinquies): control de acceso por roles, gestión de contraseñas, registros de acceso y auditoría, respaldos | Roles por causa, scrypt para contraseñas, sesiones con expiración, bitácora `auditoria` de cada acción, token obligatorio del panel | ✅ 25+ pruebas; ver §4 |
| **Trazabilidad / responsabilidad proactiva** | Cada escritura deja fila en `auditoria` (quién, qué, cuándo), y los accesos denegados también (`permiso.denegado`) | ✅ |
| **Derechos ARSPOB** (acceso, rectificación, supresión, portabilidad, oposición, bloqueo; art. 11) | La información es local y consultable, lo que hace técnicamente viable responder en 30 días corridos | ⚠️ falta el canal y el procedimiento escrito |
| **Deber de información** (art. 14 ter, 12 elementos) | — | ❌ pendiente: aviso de privacidad del estudio |
| **Protocolo de brechas** (art. 14 sexies) | La bitácora permite reconstruir qué se vio y cuándo | ❌ pendiente: procedimiento escrito |
| **Contratos con encargados** | Aplica el día que el estudio use un proveedor (hosting, respaldo, contabilidad) | ❌ pendiente: plantilla |
| **Cláusula de datos en contratos de trabajo** (art. 154 bis del Código del Trabajo) | Aplica a quien trate datos de trabajadores | ❌ pendiente: plantilla |

## 3. El punto crítico: la IA y la salida de datos

Aquí está el único riesgo serio de la arquitectura, y conviene decirlo sin adornos. El harness
(`dsh`) habla con el modelo por la API de DeepSeek: **si el agente lee un expediente del CRM y lo
manda al modelo, esos datos salen del estudio**. Jurídicamente eso es una comunicación a un tercero
que trata datos por cuenta del responsable (encargado) y, por la ubicación de los servidores, una
**transferencia internacional** (arts. 27 a 29), que exige nivel adecuado de protección o garantías
contractuales. Existen cláusulas contractuales tipo aprobadas precisamente para esto
(**Resolución Exenta 2025-3748**, 19-dic-2025, Ministerio de Economía), lo que ayuda, pero no
elimina la necesidad de contrato y de informar en el aviso de privacidad.

Cómo lo resolvemos, en orden de preferencia:

1. **Modo local sin salida**: el CRM se usa sin agente (o con un modelo local) para todo lo que
   identifique a un cliente o contraparte. Es el modo por defecto que recomendamos a un estudio.
2. **Minimización antes de consultar**: el abogado pregunta por la *regla* («plazo para contestar
   una demanda laboral») y no pega el expediente. Las herramientas de `open-legal-chile` (BCN, DT,
   CGR) consultan fuentes públicas: eso no es dato personal del cliente.
3. **Si se manda expediente**: contrato de encargado con el proveedor + cláusulas tipo + aviso de
   privacidad actualizado que declare la transferencia y su garantía.

El panel del CRM (esta parte del software) **nunca** llama a un modelo: solo sirve la información
al navegador del propio estudio. El riesgo es del agente, no del CRM, y por eso se puede apagar.

## 4. Medidas técnicas: lo que hay y lo que falta

Ya implementado y verificado con pruebas:

- Aislamiento por estudio (`estudio_id`): ninguna consulta cruza estudios.
- Acceso por causa: el abogado ve solo las causas donde está asignado; el cliente, solo la suya.
- Roles con permisos explícitos y prueba por fila de la matriz (ver `docs/matriz_permisos.md`).
- Contraseñas con `scrypt` (n=2^14) y sesiones con expiración de 12 horas, revocables.
- Bitácora de auditoría en cada escritura, incluidos los intentos de login fallidos.
- Panel que exige token o sesión, escucha solo en `127.0.0.1` y no emite peticiones externas.

Pendiente antes de datos reales de un cliente:

- **Cifrado en reposo** de la base (SQLite: SQLCipher o cifrado de disco; Postgres: `pgcrypto` o
  disco cifrado) y **respaldo cifrado** con prueba de restauración.
- **Retención y borrado**: cuánto se conserva cada tipo de dato y cómo se borra o anonimiza el
  expediente terminado (art. 14 letra d y derecho de supresión).
- **Segundo factor** para el rol administrador y socio.
- **Hashes de integridad** de documentos, para poder acreditar que un escrito no cambió.
- Canal y procedimiento ARSPOB, aviso de privacidad y protocolo de brechas (§2).

## 5. Sanciones, para dimensionar

Infracciones **leves** hasta 5.000 UTM, **graves** hasta 10.000, **gravísimas** hasta 20.000 UTM
(y hasta el 4% de los ingresos anuales en casos calificados). Las sanciones quedan cinco años en un
registro público y gratuito, y por gravísimas reiteradas en 24 meses la Agencia puede **suspender
las operaciones de tratamiento hasta 30 días**. La infracción más fácil de detectar es la leve: no
informar al titular qué datos se tratan, con qué base legal y por cuánto tiempo — se comprueba
entrando al sitio, sin fiscalizar nada.
