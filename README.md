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

## Probar sin sustituir tu `odx` actual

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

No ejecutar `uv tool install` ni reemplazar `odx.fish`/`oc.fish` para probar este
prototipo. Si más adelante se desea, `oc` puede ser un alias de `odx code opencode`.

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
