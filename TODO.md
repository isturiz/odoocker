# Odoocker — plan y seguimiento

Fuente de seguimiento del proyecto. Trasladado desde el plan de la conversación;
actualizar aquí las decisiones y resultados. No confundir código implementado,
pruebas unitarias y validaciones reales.

## Objetivo y decisiones

- Motor compartido y versionado; clientes declarativos, no plantillas copiadas.
- Desarrollo local macOS/Linux, principalmente personal; sin producción.
- `odoocker` es el proyecto, `odx` la CLI, `odx code opencode` el único asistente
  soportado. `oc` puede ser un alias opcional, nunca lógica duplicada.
- Separar adaptador OpenCode del núcleo; no implementar Claude/Codex ni un
  framework de plugins por adelantado.
- OpenCode conserva sesiones, agentes y worktrees nativos. Odoocker gestiona
  servicios y recursos Odoo, no otro gestor Git.
- No modificar Realista, helpers globales, configuración personal ni sus datos.
- Nunca compartir estado mutable entre clientes ni borrar volúmenes implícitamente.
- No montar Docker socket, SSH ni todo el perfil personal en agentes por defecto.
- Cloudpepper y lógica Realista serán adaptadores posteriores.

## 0. Proyecto independiente

- [x] Revisar destino e instrucciones; crear repositorio nuevo sin commits.
- [x] Paquete Python 3.12 con CLI `odx`, uv y lock del entorno del motor.
- [x] Crear `AGENTS.md`, README y este TODO dentro del proyecto.
- [x] Invocación local sin reemplazar `odx`/`oc` globales.

## 1. Núcleo multiversión

- [x] Manifiesto validado, ajustes privados y archivos generados separados.
- [x] Identidad UUID estable por workspace, instancias explícitas y rutas deterministas.
- [x] Compose/Odoo generados, secretos privados y puertos localhost dinámicos.
- [x] PostgreSQL no publicado; sin `container_name` ni recursos globales fijos.
- [x] Revisar fuentes: Python 3.12 y PostgreSQL 16 compatibles con Odoo 17/19.
- [x] `init`, `fetch`, `configure`, `up`, `down`, `status`, `doctor`, `deps`, `run`.
- [x] `fetch` conserva checkouts existentes; core montado solo lectura.
- [x] Addons locales editables y requirements por repositorio/módulo.
- [x] Pruebas unitarias de aislamiento, rutas, errores, identidades y secretos.
- [x] Crear sandboxes 17 y 19 separados, reutilizando únicamente fuentes core de solo lectura.
- [x] Validar instalación y arranque real de ambas versiones simultáneamente.
- [x] Rechazar configuración generada obsoleta y reutilización de instancia entre versiones.
- [ ] Incluir wkhtmltopdf parcheado, rtlcss y perfil opcional para navegador/tours.
- [x] Alinear cliente PostgreSQL con servidor (perfiles 16/18, paquetes oficiales PGDG).
- [ ] Parametrizar UID/GID y validar montajes editables en un host Linux real.

## 2. OpenCode

- [x] Adaptador concreto y comando `odx code opencode`; sin otros asistentes.
- [x] Binario Linux fijado a versión exacta, cliente host y servidor separados.
- [x] Autenticación propia; configuración/datos/estado persistentes por instancia.
- [x] Estado TUI del cliente separado por instancia sin tocar su configuración global.
- [x] Agentes pueden ejecutar `odx status`, `deps`, `run`, `test` sin Docker socket.
- [x] Validar conexión real autenticada con `odx code opencode --info` en ambos sandboxes.
- [x] Verificar que la API rechaza peticiones sin autenticación con HTTP 401.
- [ ] Validar conversación interactiva y autenticación del proveedor en el nuevo servidor.
- [ ] Verificar persistencia de conversaciones tras recrear/reconstruir.
- [x] Verificar persistencia de una sesión vacía tras `down`/`up`; conversación con proveedor pendiente.
- [ ] Validar rutas y metadatos de worktrees nativos desde host y contenedor.
- [ ] Asociar recursos a checkouts de addons existentes sin gestionar su ciclo Git.
- [ ] Exportación explícita y limitada de configuración global, sin copiar secretos.
- [ ] Evaluar inicio automático seguro del servidor desde `code` tras validar el flujo básico.

## 3. Pruebas y agentes simultáneos

- [x] `odx test`: DB, filestore y datos por UUID; nunca usa `dev` como destino.
- [x] Contenedor temporal sin puertos publicados desde host; puerto coordinado dentro del agente.
- [x] Bloqueo compartido/exclusivo del entorno y exclusivo de la DB habitual.
- [x] Logs y resultado JSON por ejecución; `--keep` para diagnóstico.
- [x] Rechazar como éxito una selección vacía de tests.
- [x] Limpieza limitada al namespace generado; preservar artefactos y reportar errores.
- [x] Validar smoke tests reales acotados de Odoo 17 y 19.
- [x] Validar dos tests simultáneos dentro de OpenCode y conservación de la DB habitual.
- [x] Validar SIGINT, selección vacía y bloqueo de `deps` con Odoo activo en contenedores reales.
- [ ] Manejar SIGTERM y recuperación explícita de recursos abandonados tras paradas forzadas.
- [ ] Añadir descubrimiento de ejecuciones activas y bloquear `down` durante operaciones gestionadas.

## 4. Reproducibilidad

- [ ] Lock de revisiones Git, imágenes por digest y dependencias Linux por perfil/plataforma.
- [ ] Separar `sync` (materializar lock) y `update` (actualizar deliberadamente).
- [x] Gestión declarativa de repositorios addons, sin cambiar ramas/descartar trabajo editable.
- [x] Requirements adicionales del workspace con restricciones Odoo y fingerprint directo.
- [ ] Fingerprint recursivo de includes `-r`/`-c`.
- [ ] Validar reconstrucción desde máquina/workspace limpio.
- [ ] Versionado de esquema y migración explícita de estado/rutas, sin reasociar datos a ciegas.

## 5. Adaptadores y adopción

- [ ] Restauración genérica segura y neutralización por versión.
- [ ] Adaptador Cloudpepper sin credenciales versionadas.
- [ ] Adaptar scripts específicos de Realista.
- [x] Respaldo privado del estado de OpenCode antes de actualizaciones explícitas.
- [ ] Migración voluntaria de Realista con respaldo y vuelta al flujo anterior.

## Registro de validación real

- Entorno de prueba: macOS arm64, Docker Desktop; Realista 17/19 ya activos.
- Verificado perfil en fuentes locales Odoo 17 y 19; no equivale a tests ejecutados.
- Primera imagen Linux y servicios sandbox-19 construidos/arrancados correctamente.
- Instalación conjunta de requirements y `uv pip check` correcta: 65 paquetes en
  Odoo 17 y 66 en Odoo 19, en volúmenes Linux separados.
- Inicialización de `base` y `/web/login` con HTTP 200 en Odoo 17 y 19 a la vez;
  procesos HTTP de smoke detenidos al terminar, servicios de desarrollo conservados.
- Servidores OpenCode 2.0.30 simultáneos, puertos dinámicos distintos y API autenticada
  verificada en ambos. Petición sin contraseña rechazada con 401.
- Smoke `/base:TestACL.test_field_visibility_restriction` superado en ambas versiones:
  una prueba por ejecución, código 0 y limpieza de DB/filestore confirmada.
- Dos ejecuciones de ese smoke en paralelo dentro del mismo contenedor OpenCode 19:
  ambas correctas, IDs/bases distintos, sin bases de test sobrantes y sin cambios
  en el recuento/hash de logins de la base `dev`.
- Selección de clase inexistente devuelve código 1, cero tests y limpia sus recursos.
- SIGINT durante inicialización de una DB de test termina el hijo Odoo, conserva
  resultado/log y elimina DB/filestore de la ejecución.
- `deps` rechazado por bloqueo mientras `odx run` usa el entorno, sin mutar paquetes.
- Wheel construido e invocado desde un entorno uv aislado, sin instalación global.
- 317 pruebas unitarias superadas; Ruff lint/formato correctos. Wheel incluye los
  recursos Docker y se ha reconstruido con la versión final del motor.
- Repetido smoke en paralelo entre Odoo 17 y 19 tras reconstruir la imagen final:
  ambas ejecuciones correctas y sin datos de tests sobrantes.
- `down`/`up` del sandbox 17 conserva sus siete volúmenes, base `dev` y una sesión
  vacía creada para la prueba; la sesión temporal se elimina después. El contenedor
  OpenCode del sandbox 19 permanece intacto durante esta comprobación.
- No equivalen a una suite completa: PDF, tours y conversación con proveedor siguen pendientes.

### Entrega: configuración declarativa de Realista lab

- [x] Preparar el workspace `realista-lab` existente, sin reinicializarlo.
- [x] Trasladar los 21 repositorios a `[[addons]]` en `odoocker.toml`, conservando
  URLs, ramas, nombres de checkout y prioridad del addons_path original.
- [x] Trasladar 44 dependencias adicionales del pyproject a requirements del workspace
  (solo se elimina la entrada duplicada de openupgradelib).
- [x] Configurar los límites de desarrollo originales sin copiar contraseñas,
  configuración de producción/MCP ni referencias a bases de Realista existentes.
- [x] Añadir `fetch --dry-run` y clonado seguro de repositorios declarados al motor.
- [x] Validar clonado/reintentos con repositorios Git temporales locales, conservación
  de ramas de trabajo y cambios sucios, orígenes erróneos, destinos inseguros y limpieza.
- [x] Comparar el manifiesto y requirements preparados con el proyecto original,
  y ejecutar dry-run desde el nuevo workspace sin red ni estado generado.
- [x] 364 pruebas automatizadas superadas, Ruff lint/formato correctos y wheel
  construido/comprobado en un entorno aislado. No se ha validado esta entrega con Docker.
- [ ] Descargar los repositorios privados y validar autenticación/ramas reales.
- [ ] Resolver/instalar todas las dependencias Realista y validar sus módulos en Linux.
- No se han clonado los 21 repositorios, instalado dependencias ni arrancado
  contenedores de Realista lab en esta entrega. Los sandboxes existentes no se recrean.

### Corrección: conflicto Requests en Realista lab

- [x] Identificar los requisitos incompatibles: core y workspace con 2.31.0 frente
  al pin 2.32.4 del clon de addons Realista.
- [x] Ajustar únicamente el clon del laboratorio a `requests>=2.31.0,<3` y dejar
  la versión del workspace a cargo de las restricciones del core.
- [x] Resolver en seco el conjunto completo en el contenedor Linux: 229 paquetes
  compatibles, seleccionando Requests 2.31.0. Sin overrides ni cambios en Odoo/original.
- [x] Verificar instalación real en el contenedor del laboratorio: 229 paquetes,
  `uv pip check` correcto y Requests 2.31.0 bajo Python 3.12.13.
- [x] Smoke de Odoo 19 `/base:TestACL.test_field_visibility_restriction` superado
  con una prueba, código 0 y eliminación de la base/filestore temporales.
- Este cambio de metadatos no demuestra compatibilidad de todas las integraciones
  ni subsana las vulnerabilidades de una versión antigua fijada por el core.

### Entrega: PostgreSQL 18 y pgvector

- [x] Añadir `[postgres]` con versión principal 16/18 y pgvector opcional, manteniendo
  PostgreSQL 16 y sus volúmenes para los manifiestos existentes sin esa sección.
- [x] Separar el volumen PG18 (`db-data-pg18`) del histórico PG16 (`db-data`), con
  montaje `/var/lib/postgresql` para el layout de PG18 y sin migración/borrado implícitos.
- [x] Exigir consentimiento `configure --allow-database-switch` al cambiar de major,
  y rechazar el cambio explícito mientras los servicios estén en marcha.
- [x] Instalar cliente de la misma major desde PGDG firmado y separar imágenes
  Odoo/OpenCode por versión PostgreSQL.
- [x] Configurar Realista lab para PostgreSQL 18 y pgvector 0.8.7 con base nueva,
  según autorización; conservar el volumen PG16, filestore y estado OpenCode existentes.
- [x] 386 pruebas automatizadas superadas y Ruff correcto.
- [x] Validación real en Realista lab (macOS arm64/Docker Desktop): servidor y
  clientes `psql`/`pg_dump`/`pg_restore` 18.6, pgvector 0.8.7 y directorio
  `/var/lib/postgresql/18/docker` sobre el nuevo volumen.
- [x] Crear extensión y consultar vectores en una base temporal; dump custom con
  cliente del contenedor Odoo y restore en otra base conservan extensión y filas.
  Ambas bases y el dump temporal se eliminan al finalizar.
- [x] Inicializar `dev` con Odoo 19, habilitar `vector` en ella y verificar su UUID,
  extensión y datos vectoriales de prueba tras `down`/`up --code --no-build`.
  La base temporal de persistencia se elimina, `dev` se conserva.
- [x] Leer `PG_VERSION = 16` en el volumen anterior mediante montaje solo lectura;
  no se borra ni se monta como datos del servidor 18.
- [x] HTTP 200 en `/web/login`, smoke `/base:TestACL.test_field_visibility_restriction`
  correcto (una prueba, cero errores/fallos, DB y filestore de test eliminados).
- [x] Wheel construido; servicios Odoo/OpenCode del laboratorio quedan preparados,
  sin dejar el proceso HTTP de smoke ejecutándose.
- [x] Los 229 paquetes siguen pasando `uv pip check`; OpenCode recreado responde
  HTTP 200 autenticado. Los seis contenedores sandbox-17/19 conservan sus IDs y
  estado en marcha, sin recreaciones ni cambios de sus manifiestos.
- No se implementa todavía restauración/neutralización integrada en la CLI, ni se
  considera demostrada la compatibilidad funcional de todos los módulos Realista.

## Fuera de alcance inicial

Producción, Kubernetes, interfaz gráfica, ejecución distribuida, programador de
agentes, gestor de worktrees propio, plugins públicos y asistentes distintos de
OpenCode. No migrar ni limpiar automáticamente entornos existentes.

## Publicación del motor compartido

- [x] Conservar el antiguo `main` de `isturiz/odoocker` en la rama remota
  `legacy/copied-template`, commit `7fc86a36fa6537b964dea9fcfc5431423cded276`.
- [x] Mantener el historial anterior como ascendencia del nuevo motor; no crear
  una historia huérfana ni utilizar force push.
- [x] Revisar archivos publicables: sin configuración privada de Realista, estado
  generado, credenciales ni rutas de máquina. Añadir exclusiones preventivas al gitignore.
- [x] Documentar la transición y corregir la limitación obsoleta del cliente PostgreSQL 15.
- [x] Publicar la sustitución de la plantilla por el motor mediante
  [PR #2](https://github.com/isturiz/odoocker/pull/2), conservando el historial.
- No modificar clones de clientes, servicios en marcha, funciones Fish ni configuración global.

## Adopción local de la CLI instalada

- [x] Tras autorización del usuario y retirada por él de la restricción en AGENTS.md,
  instalar `odoocker` con `uv tool install --python 3.12` desde GitHub/main,
  revisión inicial `2026dd62fa7d6420b0352e6bda879f57fef6d395`.
- [x] Archivar fuera de autoload las funciones Fish antiguas `odx`, `oc`, `odooctl`
  y los completions de `odx`/`odooctl`, sin cambiar su contenido; conservar manifiesto
  de checksums e instrucciones de recuperación en el archivo local.
- [x] Retirar el alias `odooctl` en vez de redirigirlo silenciosamente a otra CLI;
  dejar `oc` como wrapper mínimo de `command odx code opencode $argv`.
- [x] Sustituir completions antiguos por los comandos/opciones del motor nuevo.
  Mantener `oc2`, `ocweb`, PATH y configuración nativa de OpenCode sin cambios.
- [x] Validación en Fish nuevo: `odx` resuelve como ejecutable, versión 0.1.0,
  help correcto, sin alias `odooctl` y sin sugerencias de comandos antiguos.
- [x] Comprobar sintaxis Fish, checksums de los cinco originales y forwarding de
  argumentos con espacios/código de salida mediante un ejecutable de prueba aislado.
- [x] `odx code opencode --info` desde la instalación conecta con la instancia
  Realista lab existente y verifica OpenCode 2.0.30 sin recrear servicios.
- [x] Documentar instalación de usuario, convivencia con Fish, actualización y
  acceso directo opcional `oc` sin rutas específicas de máquina.
- [x] Crear un workspace desechable usando el binario instalado desde Fish y
  verificar `fetch --dry-run` sin Docker ni descargas; eliminar solo ese fixture.
- [x] 386 tests del motor y Ruff lint/formato correctos; `oc --info` también funciona
  desde Fish dentro del laboratorio existente. README/TODO actualizados sin commit.
- Las terminales ya abiertas requieren abrir una nueva o descargar las funciones
  y completions antiguos de su memoria. Los comandos legacy sin equivalente siguen pendientes.

## Actualización explícita de OpenCode

- [x] Añadir `odx code opencode update` para adoptar la versión exacta del host sin
  actualizar el host ni recuperar el subcomando global `oc update`.
- [x] Preservar comentarios/configuración TOML; rechazar layouts que no se puedan
  editar con seguridad y cambios pendientes no relacionados con OpenCode.
- [x] Bloqueos de workspace/instancia y contenedor de mantenimiento con locks Linux
  durante todo el proceso; rechazar sesiones activas y operaciones Odoo gestionadas.
- [x] Detener solo Odoo/OpenCode de la instancia, respaldar los tres volúmenes nativos
  y configuración anterior con permisos privados y checksum, sin Docker socket ni
  creación silenciosa de volúmenes ausentes.
- [x] Regenerar, construir, recrear y verificar versión/API; conservar respaldo y
  resultado por fases sin downgrade/restauración automática ante fallos.
- [x] Rechazar downgrades antes de parar servicios (también prereleases); identificar
  la imagen real del contenedor anterior para reintentos tras un build incompleto.
- [x] 422 pruebas automatizadas superadas, incluidas transacciones de fallo,
  conservación de datos/configuración, protocolo de backup y retención real de locks.
- [x] Validar en el workspace propio `update-smoke` la actualización real 2.0.30 ->
  2.0.31, API autenticada, conservación de sesión/título y contenido sintético
  pendiente en inbox (sin invocar proveedores), y marcadores en los tres volúmenes.
- [x] Comprobar respaldo completo, SHA-256 y permisos 0700/0600; PostgreSQL conserva
  el mismo ID y no se recrea durante la actualización; guard temporal eliminado.
- [x] Validar rechazo con lock Odoo ocupado sin parar servicios ni editar manifiesto;
  fallo de build inyectado conserva backup y deja solo PostgreSQL en marcha, con locks
  liberados después de parar Odoo/OpenCode. Reintentar con el comando normal funciona.
- [x] Validar reintento cuando el Compose apunta a una imagen nueva inexistente,
  usando la imagen real anterior por ID para el contenedor de mantenimiento.
- [x] Smoke Odoo 19 superado (una prueba, cero errores/fallos, recursos temporales
  limpiados), wheel construido y help ejecutado desde instalación aislada.
- [x] Parar el workspace de validación conservando sus volúmenes y respaldos;
  no actualizar las instancias de Realista ni los sandboxes existentes.
- [ ] Restauración guiada del respaldo de estado OpenCode tras una actualización fallida.
- El pin sigue siendo de workspace y la actualización de servicios, por instancia.
  Los procesos externos y nuevos clientes requieren coordinación humana durante mantenimiento.
- Distribución: publicar el motor en GitHub/main y renovar la CLI con
  `uv tool upgrade odoocker`, conservando su origen Git. Verificar el help y la
  revisión instalada sin ejecutar el updater sobre ningún workspace del usuario.
- La instalación/actualización de la CLI y la actualización del servidor OpenCode
  son operaciones independientes; los contenedores solo cambian por petición explícita.
