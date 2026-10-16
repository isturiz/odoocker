# odoocker

Motor compartido para **desarrollo Odoo con contenedores**. Cada cliente declara
su workspace; no copia el Dockerfile, Compose ni los scripts del motor.

Prototipo independiente: no instala funciones globales, no modifica Realista y
no reutiliza sus datos. **El único asistente soportado es OpenCode V2**, mediante
`odx code opencode`. El trabajo pendiente y las validaciones están en [TODO.md](TODO.md).

### Transición desde la plantilla anterior

`main` contiene ahora el motor compartido, no una plantilla para copiar por cliente.
La versión anterior se conserva en la rama
[`legacy/copied-template`](https://github.com/isturiz/odoocker/tree/legacy/copied-template),
y su historial sigue siendo parte de este repositorio. Este cambio no migra ni
modifica automáticamente los workspaces o contenedores creados con aquella versión.
Las configuraciones y datos privados de clientes no forman parte del motor.

## Requisitos

- macOS o Linux, `uv`, Docker con Compose y Git.
- OpenCode V2 en el host. Cada workspace fija una versión exacta del servidor;
  el cliente debe coincidir. No se actualiza el host automáticamente.
- Internet para imágenes, fuentes y dependencias.

El perfil inicial usa Python 3.12 y PostgreSQL 16 para Odoo 17 y 19; PostgreSQL 18
y pgvector se pueden seleccionar por workspace. Cada instancia
tiene su propio virtualenv Linux. Las versiones de las imágenes base están fijadas
por etiqueta; el bloqueo por digest y el lock de fuentes/dependencias están pendientes.

## Instalar la CLI

```sh
uv tool install --python 3.12 'git+https://github.com/isturiz/odoocker.git@main'
odx --version
odx --help
```

La instalación es de usuario, independiente del checkout del motor y de los
workspaces; no exige copiar scripts por cliente ni usar `uv run` a diario. Si el
directorio de herramientas no está en el PATH, ejecuta `uv tool update-shell` y
abre una terminal nueva. Instalar la CLI no arranca ni reconstruye contenedores.

Si ya existe una función Fish llamada `odx`, tiene prioridad sobre el ejecutable.
Archiva deliberadamente el helper anterior y sus completions fuera de las rutas
autoload antes de adoptar el comando nuevo. `functions --erase odx` solo descarga
la función de la sesión actual: no retira su archivo. Mientras tanto,
`command odx` ejecuta el binario sin pasar por la función.

Revisa también aliases como `odooctl` y wrappers como `oc`: los comandos antiguos
de restauración, contraseñas, `oc sync`/`update`/`pair` no tienen equivalencia
automática en la CLI nueva. Un acceso directo opcional y sin lógica duplicada es:

```fish
function oc --wraps 'odx code opencode'
    command odx code opencode $argv
end
```

Para actualizar una instalación que sigue `main`, usa `uv tool upgrade odoocker`.
Después, aplica los cambios a cada workspace deliberadamente con `down`,
`configure` y `up` (añade `--code` si utilizas OpenCode), sin operaciones activas.
Esto conserva los volúmenes, pero no migra datos entre versiones PostgreSQL.
También puedes instalar una revisión concreta usando `@<commit>` en vez de `@main`.

### Actualizar OpenCode de una instancia

Primero actualiza OpenCode en el host mediante tu método habitual de instalación.
Después, desde el workspace, sin conversaciones ni operaciones activas:

```sh
opencode --version
odx code opencode update
```

El comando adopta **la versión exacta del host**: no busca `latest`, no actualiza el
host, no copia su perfil personal y no cambia de proveedor. Requiere una instancia
ya preparada con sus volúmenes OpenCode existentes. No acepta flags de actualización
adicionales; `odx code opencode -- update` sigue reenviando `update` al cliente TUI.
Si el host es anterior al servidor configurado, rechaza el downgrade antes de parar
servicios; incluye la precedencia de versiones prerelease en esta comprobación.

El flujo valida el manifiesto, rechaza cambios ajenos pendientes y comprueba sesiones
OpenCode activas en primer plano mediante su API. Un contenedor de mantenimiento mantiene
bloqueados `run`, tests y `deps` durante toda la actualización. Se detienen Odoo y
OpenCode de la instancia (comparten imagen), pero no PostgreSQL ni otras instancias.
Con OpenCode detenido, se respaldan sus volúmenes de configuración, datos y estado;
solo después se cambia `[opencode].version`, se reconstruye, se recrean los dos
servicios y se comprueban la versión y la API autenticada. No se ejecuta `odx run`.

Los respaldos permanecen en:

```text
.odoocker/instances/<instancia>/opencode-updates/<id>/
  opencode-state.tar.gz
  odoocker.toml
  compose.json
  context.json
  odoo.conf
  secrets.json
  result.json
```

Incluyen datos sensibles (sesiones, credenciales y configuración). El directorio
es privado y `.odoocker/` debe seguir excluido de Git; no publiques estos archivos.
`result.json` conserva la fase, el resultado, la imagen anterior y el SHA-256 del
archivo cuando el backup terminó. Se conservan también `compose.env` y el Compose
del contenedor de mantenimiento para diagnóstico. No se eliminan backups automáticamente.

Si falla, se informa la fase y la ruta de recuperación. Si ya había empezado la
parada, se intenta dejar Odoo/OpenCode detenidos **antes** de liberar los bloqueos.
No se intenta un downgrade ni restaurar datos automáticamente: una versión nueva
puede haber migrado el estado. El manifiesto puede quedar en la versión nueva si
se llegó a esa fase; el anterior se conserva en el respaldo. Recuperar exige parar
la instancia y restaurar un conjunto coherente de los tres volúmenes, configuración
generada y versión anterior; no arranques una imagen antigua sobre estado migrado.
La restauración automática del estado OpenCode no está implementada.
Los respaldos reflejan el inicio de cada intento, no necesariamente el último
estado funcional: conserva especialmente el primer respaldo de un cambio de versión.
Para reintentar una construcción fallida se utiliza la imagen real del contenedor
anterior por ID, aunque el Compose generado ya apunte a una imagen nueva inexistente.

Los bloqueos cubren operaciones `odx`, no procesos arbitrarios de un agente ni
comandos Docker externos. Cierra los clientes y evita nuevas conversaciones durante
el mantenimiento; las comprobaciones de sesiones no son una barrera transaccional
frente a otros clientes. SIGKILL o una caída de Docker pueden requerir diagnóstico
manual del contenedor de mantenimiento antes de reintentar.

Para otra instancia, usa `odx --instance <nombre> code opencode update`. El pin es
compartido por el workspace: solo se recrea la instancia seleccionada; las demás
deben actualizarse explícitamente antes de volver a conectar con el host nuevo.
Incluso con la misma versión, el comando respalda y reconstruye para poder aplicar
una nueva versión del motor o reintentar una actualización fallida.

## Probar desde el checkout sin sustituir tu `odx` actual

Desde este repositorio:

```sh
uv sync --group dev
uv run odx --help

uv run odx init ../workspaces/mi-cliente --odoo 19.0
uv run odx --workspace ../workspaces/mi-cliente fetch
uv run odx --workspace ../workspaces/mi-cliente up
uv run odx --workspace ../workspaces/mi-cliente deps
uv run odx --workspace ../workspaces/mi-cliente run --init base --stop-after-init
uv run odx --workspace ../workspaces/mi-cliente up --code --no-build
uv run odx --workspace ../workspaces/mi-cliente code opencode --info
uv run odx --workspace ../workspaces/mi-cliente code opencode
```

En otra terminal, `run` mantiene Odoo en primer plano:

```sh
uv run odx --workspace ../workspaces/mi-cliente run
```

`status` muestra los puertos **realmente asignados por Docker**. `up` prepara los
contenedores; Odoo solo empieza a escuchar HTTP con `run`. Los servicios no usan
los puertos fijos 8069/4097/5432 del host.

Desde el workspace, también se puede invocar el motor sin instalarlo globalmente:

```sh
uv run --project /ruta/al/odoocker odx status
```

No es necesario instalar la herramienta ni reemplazar `odx.fish`/`oc.fish` solo
para probar el checkout. El flujo con `uv run` sigue siendo útil al desarrollar el motor.

### Reutilizar fuentes locales sin modificarlas

```sh
uv run odx init ../workspaces/mi-cliente \
  --odoo 17.0 --source /ruta/a/odoo-17
```

Las fuentes core se montan **solo lectura**. `fetch` clona si faltan y conserva
cualquier checkout existente: no hace pull, reset, checkout de ramas ni instalación
de dependencias en el host. No hay todavía actualización automática ni lock de commits.

## Configuración

Archivo versionado `odoocker.toml`:

```toml
schema = 1

[workspace]
name = "mi-cliente"

[odoo]
version = "19.0"
requirements = ["requirements.txt"] # Optional workspace-level Python dependencies

[odoo.options]
limit_time_real = 0

[opencode]
version = "2.0.30"

[postgres]
version = "18" # Optional: "16" (default) or "18"
pgvector = true # Optional, default false

[[addons]]
path = "addons/oca/partner-contact"
url = "https://github.com/OCA/partner-contact.git"
branch = "19.0"
```

`[[addons]]` reemplaza la lista manual de repositorios/rutas. `odx fetch` clona los
repositorios declarados que falten y sus rutas entran automáticamente en
`addons_path`. `odx fetch --dry-run` muestra el plan sin clonar ni crear estado.
Cada destino debe estar dentro de `addons/` del workspace, sin duplicados,
solapamientos ni escapes por symlinks.

Los checkouts existentes se conservan **sin pull, reset ni cambio de rama**, incluso
si están sucios o en una rama de trabajo. Se verifica que sean repositorios propios
con el origen declarado; no se sustituye un directorio existente. Una descarga
fallida detiene el proceso y permite reintentarlo conservando lo ya completado.
Los repositorios privados usan la autenticación Git del host, no claves montadas
en los contenedores. `fetch` no es un gestor de ramas/worktrees ni una actualización.

`odoo.requirements` contiene archivos relativos al workspace. `deps` resuelve sus
dependencias junto con las del core y los addons, usando los requirements de Odoo
como restricciones. Hay que crear el archivo si se declara; no se ignora si falta.
`odoo.options` admite únicamente límites de memoria, tiempos y peticiones; no permite
sobrescribir conexión PostgreSQL, puertos, credenciales ni rutas administradas.

Archivo privado `odoocker.local.toml`:

```toml
[odoo]
source = "/ruta/local/a/odoo"
addons = ["./addons/mi-cliente", "/ruta/a/repositorio-oca"]
```

Las rutas locales son opcionales y se añaden a los repositorios declarados. No hace
falta repetir aquí los `[[addons]]`; una ruta duplicada se rechaza.

Cada entrada de addons es un directorio cuyos hijos son módulos. Se montan en
lectura/escritura para poder desarrollar. Las rutas relativas se resuelven desde
el workspace, no desde la terminal. No se montan claves SSH, Docker socket ni
configuración global de OpenCode. Los archivos del proyecto, como `.opencode/`,
sí forman parte del workspace montado y pueden ser leídos por el agente.

La CLI rechaza claves desconocidas para detectar errores de configuración pronto.
Tras cambiar manifiesto, rutas o versión del motor, ejecuta `configure` y `up`
para regenerar/recrear. No regeneres mientras otra sesión esté usando la instancia.

### PostgreSQL y pgvector

`[postgres]` selecciona la versión principal del servidor y del cliente de consola
en Odoo/OpenCode (`pg_dump`, `pg_restore`, `psql`, etc.). El cliente se instala desde
el repositorio oficial PGDG con verificación de firma. No se instala el servidor en
el contenedor de agentes ni se publica el puerto PostgreSQL en el host.

`pgvector = true` usa la imagen `pgvector/pgvector:0.8.7-pg<versión>-bookworm`.
La extensión está **disponible**, no se activa automáticamente en todas las bases:
cada base que la necesite debe ejecutar `CREATE EXTENSION IF NOT EXISTS vector`,
ya sea desde su módulo o durante una restauración. No se modifica `template1`.

Los workspaces existentes sin esa sección siguen usando PostgreSQL 16, con su
volumen histórico `db-data`. PostgreSQL 18 utiliza **otro volumen**, `db-data-pg18`,
montado en `/var/lib/postgresql`, según el layout de esa versión. No se conecta un
servidor 18 al directorio de datos de un servidor 16.

Para cambiar de versión deliberadamente, primero cambia el manifiesto y ejecuta:

```sh
odx down
odx configure --allow-database-switch
odx up
```

Este flujo **no migra, restaura ni borra datos**. Al usar una versión por primera vez,
su volumen estará vacío; inicializa `dev` con `odx run --init base --stop-after-init`
o utiliza un flujo de restauración validado. Si vuelves a una versión anterior, se
reutiliza el volumen de esa versión: el flag no garantiza una base nueva cada vez.
El filestore, entorno Python y estado OpenCode se conservan; el flag no es un reset.
La restauración integrada y neutralización siguen pendientes.

`.odoocker/` contiene identidad local, secretos y archivos generados; **no se copia,
versiona ni elimina para limpiar un entorno**: perder su identidad deja volúmenes
anteriores sin asociar. Mover un workspace con estado requiere una migración futura.
Las contraseñas de PostgreSQL y OpenCode son privadas y locales a cada instancia.

## Varias instancias y agentes

```sh
uv run odx --workspace ../workspaces/mi-cliente --instance tarea-facturas up
uv run odx --workspace ../workspaces/mi-cliente --instance tarea-facturas deps
```

Cada instancia tiene PostgreSQL, filestore, entorno Python, red y estado OpenCode
independientes. No hay un selector global de «cliente activo». Dos workspaces con
el mismo nombre de carpeta tampoco comparten identidad.

**Esto no crea otra copia del código.** OpenCode conserva su gestión nativa de
worktrees. La integración completa de rutas Git host/contenedor está pendiente de
validación; no se presenta como funcionalidad terminada. Un worktree de la
configuración tampoco crea worktrees de los repositorios de addons externos.

## Tests ejecutables por un agente

Dentro del contenedor OpenCode, `odx` está instalado y no necesita Docker:

```sh
odx status
odx test --module mi_modulo
odx test --module mi_modulo --tags /mi_modulo:MiClase
```

Desde el host se usa la misma interfaz:

```sh
uv run odx --workspace ../workspaces/mi-cliente test \
  --module base --tags /base:TestACL.test_field_visibility_restriction
```

Cada ejecución genera base, filestore, directorio de datos y logs independientes.
Los tests del host usan un contenedor temporal sin publicar puertos; dentro de una
sesión OpenCode se coordina un puerto propio. **`--no-http` no garantiza que Odoo
no abra HTTP al activar tests**, por eso también aislamos este recurso.

El resultado conserva ID, código de salida y resumen. Cero tests seleccionados
no se considera éxito. Se eliminan únicamente la base y el filestore de esa
ejecución, salvo `--keep`; logs/resultados permanecen en el volumen de runtime:

```sh
uv run odx --workspace ../workspaces/mi-cliente logs --test ID
uv run odx --workspace ../workspaces/mi-cliente logs --test ID --result
```

Los bloqueos impiden instalar dependencias mientras una operación Odoo gestionada
usa el entorno, y ejecutar dos operaciones gestionadas sobre `dev`. No coordinan
procesos externos ni impiden que dos agentes editen el mismo archivo. Una parada
forzada de Docker/SIGKILL puede dejar recursos de tests pendientes; no hay todavía
recolector automático. `down` no debe usarse durante operaciones activas.

### Limitaciones del perfil inicial

- No incluye todavía wkhtmltopdf con Qt parcheado, rtlcss ni navegador para tests
  de tours. Los tests PDF/RTL/browser pueden fallar por estas dependencias.
- El smoke test acotado no sustituye la suite completa de Odoo o de un cliente.
- `deps` resuelve conjuntamente los requirements descubiertos, usando los de Odoo
  como restricciones; no equivale todavía a una instalación reproducible con lock.
- La detección de cambios usa los requirements directos; cambios en includes `-r`
  deben aplicarse ejecutando `deps` explícitamente.
- PostgreSQL no se expone al host. El cliente incluido coincide con la versión
  principal del servidor (16/18); backup/restauración y neutralización integrados
  en la CLI siguen pendientes.
- La imagen usa UID/GID 1000. En Linux, los directorios editables deben permitir
  escritura a ese usuario; parametrizar el UID/GID está pendiente.
- Las credenciales de modelos se configuran en el nuevo servidor con `/connect`;
  no se copian las credenciales del host. No se ha probado aún una conversación
  interactiva autenticada con proveedor.

## Diagnóstico y parada

```sh
uv run odx --workspace ../workspaces/mi-cliente status --json
uv run odx --workspace ../workspaces/mi-cliente doctor
uv run odx --workspace ../workspaces/mi-cliente logs opencode
uv run odx --workspace ../workspaces/mi-cliente down
```

`down` elimina contenedores y red **de esta instancia**, conservando todos sus
volúmenes. No existe un comando de borrado global ni limpieza automática de datos.

## Desarrollo del motor

```sh
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Las pruebas unitarias no necesitan Docker ni credenciales. Las comprobaciones
reales y sus limitaciones se registran por separado en `TODO.md`.
