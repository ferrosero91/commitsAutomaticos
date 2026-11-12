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
| `--push-every N` | Commits entre pushes; `0` = un único push al final. | `1` |
| `--delay SEGUNDOS` | Espera entre pushes. | `0` |
| `--max-file-size-mb MB` | Tamaño máximo por archivo. | `10` |
| `--allow-secrets` | No bloquear posibles secretos (no recomendado). | Desactivado |
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

Los archivos rechazados se muestran con su motivo y se excluyen del plan.
`--allow-secrets` desactiva las dos primeras comprobaciones, pero mantiene el
límite de tamaño. Úsalo solo tras revisar el contenido.

---

## Cómo se construye el plan

- Se descubren los archivos respetando `.gitignore` mediante
  `git check-ignore` (patrones, negaciones y archivos anidados reales).
- Se omiten directorios de build, dependencias, entornos virtuales, repositorios
  anidados y submódulos.
- Los archivos se reparten en bloques contiguos y equilibrados.
- El plan debe cumplir: `commits <= archivos` y
  `commits >= ceil(archivos / max-files-per-commit)`.
- Cada commit recibe un mensaje en formato
  [Conventional Commits](https://www.conventionalcommits.org/):
  `tipo(scope): descripción`, con una heurística basada en rutas y extensiones.
- Los mensajes no analizan el diff: describen el grupo de archivos.

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
- **Abierto/cerrado:** el generador de mensajes y el validador son
  sustituibles sin modificar la aplicación.
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
repositorio Git real (incluido push a un remoto local) y la aplicación completa
en modo simulación y real.

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
- Los mensajes son heurísticos y no interpretan el contenido del diff.
- El escaneo de secretos es heurístico y solo lee los primeros 512 KB de cada
  archivo.
- No firma commits con GPG.
- Se omiten submódulos y repositorios anidados.

## Licencia

MIT. Consulta [LICENSE](LICENSE).
