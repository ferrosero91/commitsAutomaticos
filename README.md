# autocommit

Herramienta de línea de comandos para crear un historial de commits reproducible
en un repositorio Git, repartiendo los archivos del proyecto entre uno o varios
colaboradores y subiéndolos a un remoto.

> **Uso responsable.** La herramienta distribuye commits entre identidades y
> tokens que tú proporcionas. Úsala solo en repositorios propios o en los que
> tengas permiso explícito, y respeta las políticas de tu organización, las
> condiciones de GitHub y las normas de integridad académica que te apliquen.

---

## Requisitos

- Python **3.9** o superior.
- Git **2.25** o superior en el `PATH`.
- Sin dependencias de runtime: solo biblioteca estándar.
- `tkinter` solo para la interfaz gráfica (incluido en el instalador oficial de
  Python en macOS/Windows; en Linux: `sudo apt install python3-tk`).

## Instalación

```bash
git clone https://github.com/<usuario>/commitsAutomaticos.git
cd commitsAutomaticos

# Opcional: instalar el comando `autocommit`
python3 -m pip install -e .

# Opcional: dependencias de desarrollo
python3 -m pip install -r requirements-dev.txt
```

También funciona sin instalar nada:

```bash
python3 -m autocommit --help
python3 commit.py --help      # alias compatible con la versión anterior
```

---

## Uso rápido (interactivo)

```bash
python3 -m autocommit
```

El flujo es:

1. Elige la carpeta del proyecto (diálogo o consola).
2. Indica la URL del remoto (si `origin` ya existe, se conserva).
3. Elige una rama existente o crea una nueva.
4. Revisa los archivos detectados y los excluidos por seguridad.
5. Indica cuántos colaboradores participan y sus datos.
6. Confirma el plan; se crean los commits y se hace push.

Cada colaborador necesita un **token de acceso personal (PAT)** de GitHub.
Si no existe uno guardado, se solicita una sola vez con `getpass` (no se
muestra en pantalla) y se almacena de forma segura.

## Interfaz gráfica

```bash
autocommit --gui
# o, tras la instalación:
autocommit-gui
```

La ventana tiene cuatro pestañas:

- **Proyecto:** carpeta, URL del remoto y rama.
- **Colaboradores:** tabla con nombre, usuario, email, commits y estado del
  token. Añadir/editar/quitar con un diálogo; el token se escribe enmascarado y
  se puede guardar en el almacén seguro (`0600`).
- **Opciones:** límites de archivos, pushes, espera, reanudar, continuar ante
  errores, simulación y mensajes con IA (modelo, URL y clave).
- **Registro:** salida en vivo, progreso y resultado.

Botones **Previsualizar** (simulación, no toca el repositorio), **Ejecutar** y
**Detener**. Detener cancela la planificación (incluidas las llamadas a IA) y se
aplica entre commits y durante las esperas; si hay un `fetch` o un `push` en
curso, termina antes de parar. El estado queda guardado para reanudar. La GUI
usa el mismo motor que la CLI y comparte tokens, archivo `.env` y configuración
de IA.

La actividad de la GUI se registra en `~/.config/autocommit/gui.log` (o bajo
`AUTOCOMMIT_CONFIG_DIR`), con las credenciales redactadas; sirve para diagnosticar
cierres o errores inesperados.

Se eligió Tkinter por ser parte de la biblioteca estándar y no añadir un runtime
extra; Flet queda como alternativa si más adelante se prioriza una estética
web. La lógica de validación está separada en `gui_state.py` para poder probarla
sin abrir ventanas.

## Uso no interactivo

```bash
python3 -m autocommit \
  --repo ~/proyectos/mi-app \
  --remote-url https://github.com/mi-org/mi-app.git \
  --branch main \
  --collaborator "Ada Lovelace:ada-lovelace:ada@example.com:12" \
  --collaborator "Alan Turing:alan-turing:alan@example.com:8" \
  --push-every 5 \
  --delay 10 \
  --yes
```

Con mensajes generados por IA (OpenCode) y respaldo heurístico:

```bash
export OPENCODE_API_KEY="oc_sk_..."

python3 -m autocommit \
  --repo ~/proyectos/mi-app \
  --remote-url https://github.com/mi-org/mi-app.git \
  --branch main \
  --collaborator "Ada Lovelace:ada-lovelace:ada@example.com:12" \
  --ai \
  --ai-model deepseek-v4.1-flash \
  --yes
```

Simulación sin tocar el repositorio:

```bash
python3 -m autocommit \
  --repo ~/proyectos/mi-app \
  --branch main \
  --collaborator "Ada Lovelace:ada-lovelace:ada@example.com:4" \
  --dry-run
```

En modo no interactivo (`--non-interactive` o sin TTY) hay que pasar todos los
datos y añadir `--yes` para ejecutar; sin `--yes` el plan solo se muestra y la
operación se cancela.

### Formato de colaborador

```text
NOMBRE:USUARIO:EMAIL:COMMITS
```

Ejemplo: `Ada Lovelace:ada-lovelace:ada@example.com:12`.

## Opciones

| Opción | Descripción | Por defecto |
| --- | --- | --- |
| `--repo PATH` | Carpeta del proyecto. | Diálogo / consola |
| `--remote-url URL` | URL del remoto, sin credenciales. | `origin` existente |
| `--branch NAME` | Rama a usar o crear. | Pregunta |
| `--collaborator SPEC` | Colaborador; se puede repetir. | Pregunta |
| `--max-files-per-commit N` | Máximo de archivos por commit. | `100` |
| `--push-every N` | Commits entre pushes dentro de un mismo autor; al cambiar de colaborador siempre se hace push. `0` = al final de cada bloque. | `1` |
| `--delay SEGUNDOS` | Espera entre pushes. | `0` |
| `--max-file-size-mb MB` | Tamaño máximo por archivo. | `10` |
| `--allow-secrets` | No bloquear posibles secretos (no recomendado). | Desactivado |
| `--ai` | Generar mensajes con OpenCode; si falla, usa la heurística. | Desactivado |
| `--ai-model MODELO` | Modelo de OpenCode. | `deepseek-v4.1-flash` |
| `--ai-base-url URL` | Base OpenAI-compatible (Go o pay-as-you-go). | Go V2 |
| `--ai-api-key-env VAR` | Variable con la clave. | `OPENCODE_API_KEY` |
| `--ai-api-key CLAVE` | Clave directa (queda en el historial). | — |
| `--ai-api-key-file RUTA` | Archivo con la clave. | `~/.config/autocommit/opencode_key` |
| `--ai-timeout SEGUNDOS` | Timeout por llamada. | `30` |
| `--ai-max-calls N` | Mensajes por IA; después, heurística. | `200` |
| `--env-file RUTA` | Carga variables `CLAVE=VALOR` antes de ejecutar. | — |
| `--gui` | Abre la interfaz gráfica en lugar de la consola. | Desactivado |
| `--dry-run` | Mostrar el plan sin modificar el repositorio. | Desactivado |
| `--resume` | Continuar desde `.git/autocommit/state.json`. | Desactivado |
| `--keep-going` | Seguir si un commit falla. | Desactivado |
| `--seed N` | Semilla para mensajes reproducibles. | Aleatoria |
| `-y`, `--yes` | No pedir confirmación final. | Desactivado |
| `--non-interactive` | No pedir datos por consola. | Desactivado |
| `--log-file PATH` | Guardar log detallado. | Solo consola |
| `--log-level LEVEL` | `DEBUG`, `INFO`, `WARNING` o `ERROR`. | `INFO` |

### Códigos de salida

| Código | Significado |
| --- | --- |
| `0` | Todo correcto. |
| `1` | Se completó con errores parciales. |
| `2` | Error de validación, seguridad, estado o Git. |
| `130` | Interrumpido por el usuario (`Ctrl+C`). |

---

## Seguridad

### Tokens

Orden de búsqueda:

1. Variable de entorno `AUTOCOMMIT_TOKEN_<USUARIO>` (útil en CI).
2. Llavero del sistema, si `keyring` está instalado.
3. `~/.config/autocommit/tokens.json` con permisos `0600` y escritura atómica.

```bash
export AUTOCOMMIT_TOKEN_ADA_LOVELACE="ghp_..."
```

El token:

- **nunca** se escribe en `.git/config`;
- **nunca** se pasa por la línea de comandos;
- se entrega al push mediante un script `GIT_ASKPASS` temporal (`0700`) con el
  token en un archivo efímero (`0600`), que se borra al terminar;
- se redacta en logs y mensajes de error;
- se solicita con `getpass`, nunca con `input`.

Crea el PAT en <https://github.com/settings/tokens> con el permiso mínimo
necesario (scope `repo` para repositorios privados).

Si vienes de la versión anterior, `origin` podía contener el token incrustado.
La nueva versión lo detecta y elimina esa credencial de la configuración.

### Validación de contenido

Antes de planificar, cada archivo pasa por un validador que rechaza:

- nombres sensibles: `.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials`, etc.;
- contenido con patrones de secretos (tokens de GitHub, AWS, Google, Slack,
  Stripe, claves privadas, JWT y asignaciones genéricas de credenciales);
- archivos que superen `--max-file-size-mb`.

Se permiten las **plantillas** `.env.example`, `.env.sample`, `.env.template` y
`.env.dist`: no se bloquean por nombre, pero su contenido se escanea igual. Si
una plantilla contiene un token real, se rechaza. Las variantes como `.env`,
`.env.local`, `.env.test` o `.env.production` siguen bloqueadas.

Los archivos rechazados se muestran con su motivo y se excluyen del plan.
`--allow-secrets` desactiva las dos primeras comprobaciones, pero mantiene el
límite de tamaño y **no** desactiva `.gitignore`. Úsalo solo tras revisar el
contenido.

### Qué no se sube

Antes de crear el plan se aplican varias capas independientes:

1. **`.gitignore` real:** `git check-ignore --no-index` respeta patrones,
   negaciones y `.gitignore` anidados, además de `.git/info/exclude` y el
   `core.excludesFile` global.
2. **Archivos ya rastreados:** aunque un archivo esté en el índice, si coincide
   con una regla de ignore se excluye igualmente. Así una clave que se añadió
   con `git add -f` no vuelve a subirse.
3. **Directorios y nombres por defecto:** `.git`, repositorios anidados,
   submódulos, `node_modules`, `.venv`, `build`, `dist`, `target`,
   `__pycache__`, etc.
4. **Sufijos temporales:** `.log`, `.tmp`, `.bak`, `.pyc`, `.swp`, etc.
5. **Validador de seguridad:** `.env`, claves privadas, credenciales, tokens
   conocidos y archivos que superen el tamaño máximo. Las plantillas
   `.env.example`, `.env.sample`, `.env.template` y `.env.dist` se permiten,
   pero se escanean igual.

`.gitignore` sí se versiona (contiene patrones, no secretos); `.git/info/exclude`
vive dentro de `.git` y nunca se sube. Los archivos de ignore globales están
fuera del proyecto. Si tu `.gitignore` ignora `.env.*`, añade una excepción para
la plantilla:

```gitignore
.env
.env.*
!.env.example
```

> **Importante:** ignorar un archivo no borra lo que ya está en el historial.
> Si una clave se subió antes, hay que **revocarla/rotarla** y limpiar el
> historial con `git filter-repo` (o BFG) antes de volver a publicar.

---

## Cómo se construye el plan

- Se descubren los archivos respetando `.gitignore` mediante
  `git check-ignore --no-index` (patrones, negaciones, archivos anidados,
  exclude global y archivos ya rastreados).
- Se omiten directorios de build, dependencias, entornos virtuales, repositorios
  anidados y submódulos.
- Los archivos se reparten en bloques contiguos y equilibrados.
- El plan debe cumplir: `commits <= archivos` y
  `commits >= ceil(archivos / max-files-per-commit)`.
- Cada commit recibe un mensaje en formato
  [Conventional Commits](https://www.conventionalcommits.org/):
  `tipo(scope): descripción`, con una heurística basada en rutas y extensiones.
- Los mensajes no analizan el diff: describen el grupo de archivos.

## Mensajes con IA (OpenCode)

La generación con IA es opcional. Usa el protocolo OpenAI Chat Completions que
OpenCode Console y OpenCode Go exponen en su API V2:

| Plan | Base URL | Ruta |
| --- | --- | --- |
| Go | `https://opencode.ai/inference/go` | `/openai/v1/chat/completions` |
| Pay-as-you-go | `https://opencode.ai/inference` | `/openai/v1/chat/completions` |

El valor de `--ai-base-url` es la base que usarías en un SDK de OpenAI, es
decir, incluye `/openai/v1`:

```bash
# Go (por defecto)
export AUTOCOMMIT_AI_BASE_URL="https://opencode.ai/inference/go/openai/v1"

# Pay-as-you-go
export AUTOCOMMIT_AI_BASE_URL="https://opencode.ai/inference/openai/v1"
```

### Cómo proporcionar la clave

Orden de resolución:

1. `--ai-api-key` (explícita; menos segura).
2. Variable de entorno (`OPENCODE_API_KEY` por defecto).
3. Archivo de clave con permisos `0600`.
4. Prompt interactivo con `getpass`; la primera vez ofrece guardarla en el
   archivo para no repetirla.

```bash
# CI: variable de entorno
export OPENCODE_API_KEY="oc_sk_..."

# Local: archivo 0600 (recomendado)
mkdir -p ~/.config/autocommit
(umask 077 && printf '%s\n' "oc_sk_..." > ~/.config/autocommit/opencode_key)
chmod 600 ~/.config/autocommit/opencode_key
```

También puedes indicar otro archivo con `--ai-api-key-file` u otra variable con
`--ai-api-key-env`. Si usas `--ai-api-key`, la clave puede quedar en el
historial del shell y en la lista de procesos, y la herramienta lo advierte.

### Variables de entorno

La herramienta reconoce estas variables:

| Variable | Uso | Ejemplo |
| --- | --- | --- |
| `OPENCODE_API_KEY` | Clave de OpenCode para `--ai`. | `oc_sk_...` |
| `AUTOCOMMIT_AI_MODEL` | Modelo de IA por defecto. | `deepseek-v4.1-flash` |
| `AUTOCOMMIT_AI_BASE_URL` | Base OpenAI-compatible. | `https://opencode.ai/inference/go/openai/v1` |
| `AUTOCOMMIT_TOKEN_<USUARIO>` | Token de Git de un colaborador. | `AUTOCOMMIT_TOKEN_ADA_LOVELACE` |
| `AUTOCOMMIT_CONFIG_DIR` | Directorio de configuración. | `~/.config/autocommit` |
| `XDG_CONFIG_HOME` | Base para la configuración (XDG). | `~/.config` |

Para los tokens de Git, el nombre se normaliza: mayúsculas y caracteres no
alfanuméricos reemplazados por `_`. Por ejemplo, `ada-lovelace` usa
`AUTOCOMMIT_TOKEN_ADA_LOVELACE`.

```bash
export OPENCODE_API_KEY="oc_sk_..."
export AUTOCOMMIT_TOKEN_ADA_LOVELACE="ghp_..."
export AUTOCOMMIT_TOKEN_ALAN_TURING="ghp_..."

python3 -m autocommit --ai --repo ~/mi-app --branch main \
  --collaborator "Ada Lovelace:ada-lovelace:ada@example.com:10" \
  --collaborator "Alan Turing:alan-turing:alan@example.com:8" --yes
```

### Archivo `.env`

Si prefieres un archivo en lugar de exportar variables en cada shell:

```bash
cp .env.example .env
chmod 600 .env
# edita .env con tus valores
python3 -m autocommit --env-file .env --ai ...
```

`--env-file` no pisa variables que ya estén definidas en el entorno: un secreto
de CI o un `export` explícito siempre gana. Revisa `.env.example` para ver los
nombres disponibles. `.env` está ignorado por git; **nunca** lo confirmes.

La clave se envía como `Authorization: Bearer ...`, junto con
`x-opencode-session` y un `User-Agent` propio, como pide la documentación de Go.

Modelos recomendados para mensajes cortos y económicos:

- `deepseek-v4.1-flash` (por defecto)
- `glm-5.3-flash`
- `kimi-k2.7-code`
- `qwen3.8-flash`

Notas de la API V2:

- `https://opencode.ai/zen/go/v1` era la base anterior (Zen). Sigue funcionando
  como endpoint compatible, pero la documentación actual usa
  `https://opencode.ai/inference/go`.
- Los modelos gratuitos solo se pueden usar desde OpenCode; desde otros
  clientes, como esta herramienta, responden `403`. Usa un modelo de pago o una
  suscripción Go.
- Consulta el catálogo actual con `curl https://opencode.ai/inference/v1/models`.

### Respaldo y coste

- Solo se envían las **rutas** de los archivos, nunca su contenido.
- Si la red, la clave o el formato de la respuesta fallan, se usa el generador
  heurístico y el proceso continúa.
- `--ai-max-calls` limita el gasto; al alcanzarlo se vuelve a la heurística.
- Una respuesta se acepta solo si cumple Conventional Commits y cabe en 72
  caracteres; el resto se descarta.
- Con `--dry-run --ai` también se generan mensajes (y se consume cuota).

> **Nunca incrustes claves de API en el código ni en el repositorio.** Si una
> clave se filtró, revócala en la consola del proveedor y genera otra.

## Varios colaboradores y autoría

Cada commit se crea con el `user.name` y `user.email` del colaborador, de modo
que GitHub lo atribuye a ese email. Para que aparezca vinculado a su cuenta, el
email debe estar **verificado** en su perfil de GitHub.

El push se autentica con el token del autor de su bloque:

- `--push-every 1` (por defecto): cada commit se sube con el token de su autor.
- `--push-every N`: se agrupan commits del mismo autor y, al cambiar de
  colaborador, se fuerza un push con el token del bloque anterior.
- `--push-every 0`: un push al final de cada bloque de colaborador.

Nunca se mezclan autores en un mismo push, así que cada colaborador sube sus
propios commits. Aun así, **todos necesitan permiso de escritura** en el
repositorio, porque se empuja a la misma rama.

> El token no decide el autor del commit: la autoría es el email. El token
> decide quién autentica el push. La herramienta alinea ambos: identidad del
> autor en el commit y token del mismo colaborador en el push.

Permisos recomendados:

- Ser colaborador con rol **Write** (o pertenecer a un equipo con write).
- PAT clásico con scope `repo`, o fine-grained con **Contents: Read and write**.
- Si la organización usa SSO, autorizar el token para la organización.

Si a un colaborador le falta permiso, el push falla con `403`/`404`; el proceso
se detiene, guarda el estado y puedes continuar con `--resume` después de
corregir el acceso.

## Reanudación e idempotencia

El progreso se guarda en `.git/autocommit/state.json` (sin tokens):

- `completed`: commits ya creados;
- `pushed`: último commit confirmado en el remoto.

Con `--resume` se continúa donde se quedó. Si un push falló después de crear
los commits, la siguiente ejecución los empuja antes de continuar. Al terminar
correctamente, el archivo de estado se elimina. Los commits sin cambios en disco
se omiten en lugar de fallar.

---

## Arquitectura

```text
autocommit/
├── cli.py             # argparse, códigos de salida
├── app.py             # orquestación del flujo
├── models.py          # Collaborator, PlannedCommit, CommitPlan
├── settings.py        # configuración inmutable validada
├── planner.py         # reparto de archivos en commits
├── messages.py        # mensajes Conventional Commits
├── ai.py              # mensajes con IA (OpenCode V2)
├── env.py             # carga explícita de archivos .env
├── gui.py             # ventana tkinter/ttk
├── gui_state.py       # validación y preparación sin Tk
├── files.py           # descubrimiento + filtrado .gitignore
├── security.py        # escaneo de secretos y redacción
├── git_repository.py  # único adaptador del binario git
├── runner.py          # subprocess sin shell
├── credentials.py     # almacenes de tokens
├── state.py           # progreso reanudable
├── ui.py              # Prompter, parseo de colaboradores
├── picker.py          # selección de carpeta (tkinter opcional)
└── logging_setup.py   # logging con redacción
```

Principios aplicados:

- **Responsabilidad única:** cada módulo resuelve un único problema.
- **Inversión de dependencias:** `Application` recibe fábricas y protocolos;
  no construye infraestructura concreta.
- **Abierto/cerrado:** el generador de mensajes (heurístico o IA) y el
  validador son sustituibles sin modificar la aplicación.
- **Segregación de interfaces:** `Prompter`, `TokenStore` y
  `CommitMessageFactory` son protocolos pequeños.
- **Sustitución de Liskov:** `NonInteractivePrompter` reemplaza a
  `ConsolePrompter` sin cambiar la lógica.

---

## Pruebas y calidad

```bash
make test        # python3 -m unittest discover -s tests -v
make lint        # ruff check . && ruff format --check .
```

La suite usa `unittest` (sin dependencias) y cubre modelos, planificador,
mensajes, descubrimiento de archivos, seguridad, credenciales, estado,
repositorio Git real (incluido push a un remoto local), la aplicación completa
en modo simulación y real, cancelación entre commits, variables `.env` y la
lógica de la interfaz gráfica (sin abrir ventanas).

## Migración desde la versión anterior

| Antes | Ahora |
| --- | --- |
| Un único `commit.py` de 463 líneas | Paquete modular + alias `commit.py` |
| GitPython | CLI de Git mediante `subprocess` |
| Token dentro de la URL de `origin` | `GIT_ASKPASS` temporal; `origin` limpio |
| `if patron in ruta` | `git check-ignore` real |
| Commit vacío si faltaban archivos | Validación previa del plan |
| `.github` se omitía por error | Se incluye |
| Mensajes aleatorios | Conventional Commits |
| 30 s fijos entre pushes | `--delay` configurable |
| Sin simulación ni reanudación | `--dry-run` y `--resume` |
| Sin validación de secretos | Escaneo y bloqueo por defecto |

Para replicar el ritmo de la versión anterior:

```bash
python3 -m autocommit --delay 30 --push-every 1
```

## Limitaciones conocidas

- Añade commits a la rama actual; no reescribe historia ni crea pull requests.
- La interfaz gráfica no pide tokens por consola: hay que escribirlos, tenerlos
  en `AUTOCOMMIT_TOKEN_*` o guardados en el almacén.
- Los mensajes son heurísticos y no interpretan el contenido del diff.
- El escaneo de secretos es heurístico y solo lee los primeros 512 KB de cada
  archivo.
- Los mensajes con IA dependen de un servicio externo; el respaldo heurístico
  no interpreta el contenido del diff.
- No firma commits con GPG.
- Se omiten submódulos y repositorios anidados.

## Licencia

MIT. Consulta [LICENSE](LICENSE).
