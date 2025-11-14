import os
import time
import subprocess
from git import Repo, GitCommandError
from tkinter import Tk, filedialog
from tqdm import tqdm

MAX_ARCHIVOS_POR_PUSH = 100
DELAY_ENTRE_PUSHES = 30  # segundos

# === FUNCIONES ===

def leer_gitignore(ruta):
    patrones = []
    gitignore_path = os.path.join(ruta, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea and not linea.startswith("#"):
                    patrones.append(linea)
    return patrones

def archivo_ignorado(ruta_archivo, patrones):
    for patron in patrones:
        if patron in ruta_archivo:
            return True
    return False

def listar_archivos_validos(ruta, patrones):
    archivos_validos = []
    for raiz, _, archivos in os.walk(ruta):
        if ".git" in raiz:
            continue
        for archivo in archivos:
            ruta_relativa = os.path.relpath(os.path.join(raiz, archivo), ruta)
            if archivo_ignorado(ruta_relativa, patrones):
                continue
            if archivo.endswith(('.tmp', '.log', '.bak', '.pyc', '.swp')):
                continue
            archivos_validos.append(ruta_relativa)
    return archivos_validos

def dividir_archivos(archivos, total_commits):
    grupos = []
    total_archivos = len(archivos)
    base = total_archivos // total_commits
    resto = total_archivos % total_commits
    indice = 0
    for i in range(total_commits):
        cantidad = base + (1 if i < resto else 0)
        grupo = archivos[indice:indice + cantidad]
        grupos.append(grupo)
        indice += cantidad
    return grupos

def dividir_por_tamano(grupo):
    return [grupo[i:i + MAX_ARCHIVOS_POR_PUSH] for i in range(0, len(grupo), MAX_ARCHIVOS_POR_PUSH)]

def generar_mensaje_commit(archivos):
    """Genera un mensaje de commit natural basado en los archivos"""
    import random
    
    # Obtener extensiones y nombres de archivos
    extensiones = set()
    nombres = []
    for archivo in archivos:
        nombre = os.path.basename(archivo)
        nombres.append(nombre)
        ext = os.path.splitext(nombre)[1]
        if ext:
            extensiones.add(ext)
    
    # Determinar tipo de cambio
    if len(archivos) == 1:
        nombre = os.path.splitext(nombres[0])[0]  # Sin extensión
        acciones = [
            f"add {nombre}",
            f"create {nombre}",
            f"implement {nombre}",
            f"update {nombre}",
            f"fix {nombre}",
        ]
        return random.choice(acciones)
    
    # Múltiples archivos - mensajes más naturales y variados
    if '.tsx' in extensiones or '.jsx' in extensiones:
        mensajes = [
            "actualización de componentes UI",
            "mejora en la interfaz de usuario",
            "refactor de componentes React",
            "implementación de nuevas vistas",
            "optimización de componentes",
            "corrección en componentes visuales",
            "actualización de páginas",
            "mejora en navegación",
            "implementación de nuevas funcionalidades en UI",
            "corrección de bugs visuales",
            "optimización del renderizado",
            "mejora en experiencia de usuario",
            "actualización de layouts",
            "implementación de diseño responsive",
            "corrección en el flujo de navegación",
            "mejora en componentes reutilizables",
            "actualización de formularios",
            "implementación de validaciones en frontend",
            "mejora en manejo de estados",
            "corrección de warnings en consola",
        ]
    elif '.ts' in extensiones or '.js' in extensiones:
        mensajes = [
            "mejora en la lógica del negocio",
            "implementación de nuevas funcionalidades",
            "refactor de código",
            "optimización de funciones",
            "corrección de errores",
            "actualización de servicios",
            "mejora en el manejo de datos",
            "implementación de validaciones",
            "optimización de rendimiento",
            "corrección de bugs críticos",
            "mejora en manejo de errores",
            "actualización de utilidades",
            "implementación de nuevos endpoints",
            "refactor de funciones auxiliares",
            "mejora en seguridad",
            "corrección en lógica de autenticación",
            "optimización de consultas",
            "implementación de caché",
            "mejora en manejo de promesas",
            "actualización de dependencias",
        ]
    elif '.css' in extensiones or '.scss' in extensiones:
        mensajes = [
            "actualización de estilos",
            "mejora en el diseño visual",
            "ajustes de responsive",
            "corrección de estilos",
            "optimización de CSS",
            "mejora en animaciones",
            "actualización de colores y tipografía",
            "corrección de alineaciones",
            "implementación de tema oscuro",
            "mejora en estilos mobile",
            "optimización de media queries",
            "corrección de espaciados",
            "actualización de variables CSS",
            "mejora en transiciones",
        ]
    elif '.test.' in str(archivos):
        mensajes = [
            "actualización de pruebas unitarias",
            "mejora en cobertura de tests",
            "corrección de tests",
            "implementación de nuevos tests",
            "refactor de casos de prueba",
            "mejora en tests de integración",
            "corrección de tests fallidos",
            "actualización de mocks",
            "implementación de tests e2e",
            "mejora en assertions",
        ]
    elif '.json' in extensiones or '.yml' in extensiones or '.yaml' in extensiones:
        mensajes = [
            "actualización de configuración",
            "ajustes en archivos de config",
            "mejora en configuración del proyecto",
            "actualización de dependencias",
            "corrección en archivos de configuración",
            "mejora en scripts de build",
            "actualización de variables de entorno",
            "ajustes en pipeline CI/CD",
        ]
    elif '.java' in extensiones:
        mensajes = [
            "implementación de servicios backend",
            "mejora en controladores",
            "actualización de modelos",
            "refactor de código Java",
            "corrección en la lógica del servidor",
            "implementación de nuevos endpoints REST",
            "mejora en manejo de excepciones",
            "optimización de consultas JPA",
            "actualización de entidades",
            "implementación de validaciones backend",
            "mejora en seguridad de APIs",
            "corrección de bugs en servicios",
            "refactor de repositorios",
            "implementación de DTOs",
            "mejora en arquitectura del proyecto",
        ]
    elif '.py' in extensiones:
        mensajes = [
            "actualización de scripts",
            "mejora en funciones Python",
            "implementación de nuevas utilidades",
            "corrección de errores",
            "optimización de algoritmos",
            "mejora en manejo de datos",
            "implementación de nuevas APIs",
            "refactor de código Python",
            "corrección de bugs",
            "actualización de modelos",
        ]
    elif '.html' in extensiones:
        mensajes = [
            "actualización de plantillas",
            "mejora en estructura HTML",
            "corrección de vistas",
            "optimización de SEO",
            "mejora en accesibilidad",
            "actualización de meta tags",
            "corrección de estructura semántica",
        ]
    elif '.md' in extensiones or 'README' in str(archivos):
        mensajes = [
            "actualización de documentación",
            "mejora en README",
            "corrección de documentación",
            "actualización de guías",
            "mejora en instrucciones de instalación",
        ]
    elif '.sql' in extensiones:
        mensajes = [
            "actualización de esquema de base de datos",
            "implementación de nuevas migraciones",
            "corrección en queries",
            "optimización de consultas SQL",
            "mejora en estructura de tablas",
        ]
    elif '.xml' in extensiones:
        mensajes = [
            "actualización de configuración XML",
            "mejora en archivos de configuración",
            "corrección en manifiestos",
            "actualización de dependencias Maven",
        ]
    else:
        mensajes = [
            "actualización de archivos del proyecto",
            "mejora en estructura del proyecto",
            "implementación de cambios",
            "corrección de archivos",
            "actualización general",
            "ajustes en el proyecto",
            "mejora en organización de archivos",
            "corrección de configuraciones",
            "actualización de recursos",
            "implementación de mejoras generales",
            "refactor de estructura",
            "optimización del proyecto",
        ]
    
    return random.choice(mensajes)

def ejecutar_comando(comando, ruta):
    resultado = subprocess.run(comando, shell=True, cwd=ruta, text=True, capture_output=True)
    if resultado.returncode != 0:
        print(f"⚠ Error ejecutando {comando}: {resultado.stderr}")

def obtener_token_colaborador(username):
    """Obtiene el token específico de un colaborador"""
    token_file = os.path.expanduser(f"~/.token_git_{username}")
    
    if os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                print(f"🔐 Token de {username} cargado automáticamente.")
                return token

    print(f"\n🔑 Token para {username}:")
    print("   (El colaborador debe generar su PAT en: https://github.com/settings/tokens)")
    token = input(f"   Ingresa el token de {username}: ").strip()
    
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(token)
    print(f"✅ Token de {username} guardado.")
    return token

def seleccionar_rama(repo, tiene_remoto=False):
    ramas_locales = [h.name for h in repo.heads] if repo.heads else []
    ramas_remotas = []
    
    if tiene_remoto:
        try:
            repo.git.fetch("--all")
            ramas_remotas = [r.name.split("/")[-1] for r in repo.remotes.origin.refs if not r.name.endswith("HEAD")]
        except Exception:
            pass

    todas = sorted(set(ramas_locales + ramas_remotas))
    if todas:
        print("\n🌿 Ramas detectadas:")
        for i, r in enumerate(todas, start=1):
            print(f"  {i}. {r}")
        opcion = input("👉 Elige el número de la rama o deja vacío para crear una nueva: ").strip()
        if opcion.isdigit() and 1 <= int(opcion) <= len(todas):
            rama = todas[int(opcion)-1]
            print(f"✅ Usando rama existente: {rama}")
            try:
                repo.git.checkout(rama)
            except GitCommandError:
                repo.git.checkout("-b", rama)
            return rama

    rama = input("🆕 Nombre de nueva rama (por defecto 'main'): ").strip() or "main"
    try:
        repo.git.checkout("-b", rama)
        print(f"🌱 Rama creada y activada: {rama}")
    except GitCommandError:
        repo.git.checkout(rama)
        print(f"🌿 Rama existente activada: {rama}")
    return rama

# === PROGRAMA PRINCIPAL ===

def main():
    print("🗂  Selecciona la carpeta del proyecto...")
    Tk().withdraw()
    ruta_proyecto = filedialog.askdirectory(title="Selecciona la carpeta del proyecto")

    if not ruta_proyecto:
        print("❌ No se seleccionó ninguna carpeta.")
        return

    print(f"📂 Proyecto seleccionado: {ruta_proyecto}")

    if not os.path.exists(os.path.join(ruta_proyecto, ".git")):
        print("📦 Inicializando repositorio Git nuevo...")
        ejecutar_comando("git init", ruta_proyecto)

    repo = Repo(ruta_proyecto)
    
    # Primero obtener URL del remoto
    remote_url = input("🔗 Ingresa la URL del repositorio remoto (https://github.com/usuario/repo.git): ").strip()
    
    # Limpiar URL base sin token
    if remote_url.startswith("https://") and "@" in remote_url:
        remote_url = "https://" + remote_url.split("@")[1]
    
    remote_url_base = remote_url
    
    # Configurar remote temporal para poder hacer fetch
    tiene_remoto = "origin" in [remote.name for remote in repo.remotes]
    if not tiene_remoto:
        repo.create_remote("origin", remote_url_base)
    
    rama = seleccionar_rama(repo, tiene_remoto)

    patrones_ignore = leer_gitignore(ruta_proyecto)
    archivos = listar_archivos_validos(ruta_proyecto, patrones_ignore)

    print(f"📂 Total de archivos detectados: {len(archivos)}")

    if not archivos:
        print("❌ No se encontraron archivos válidos para hacer commit.")
        return

    num_colaboradores = int(input("👥 ¿Cuántos colaboradores participarán?: "))
    colaboradores = []

    for i in range(num_colaboradores):
        print(f"\n👤 Datos del integrante {i+1}:")
        nombre = input("  Nombre completo: ").strip()
        username = input("  GitHub username: ").strip()
        email = input("  Email de GitHub: ").strip()
        commits = int(input("  ¿Cuántos commits debe generar este usuario?: "))
        
        # Obtener token del colaborador
        token = obtener_token_colaborador(username)
        
        colaboradores.append({
            "nombre": nombre,
            "username": username,
            "email": email,
            "commits": commits,
            "token": token
        })

    total_commits = sum(c["commits"] for c in colaboradores)
    grupos = dividir_archivos(archivos, total_commits)

    print(f"\n🚀 Se generarán {total_commits} commits automáticos (máx. {MAX_ARCHIVOS_POR_PUSH} archivos por push)...")
    print(f"⏱  Delay entre pushes: {DELAY_ENTRE_PUSHES} segundos")
    print(f"📊 Total de archivos a subir: {len(archivos)}\n")

    index_commit = 0
    archivos_subidos = 0
    commits_realizados = 0
    
    for colaborador in colaboradores:
        print(f"\n{'='*60}")
        print(f"👤 Procesando commits de: {colaborador['nombre']} (@{colaborador['username']})")
        print(f"📧 Email: {colaborador['email']}")
        print(f"{'='*60}\n")
        
        # Configurar remote con el token del colaborador
        remote_url_con_token = remote_url_base.replace("https://", f"https://{colaborador['token']}@")
        repo.git.remote("set-url", "origin", remote_url_con_token)
        
        # Configurar identidad del colaborador en el repositorio local
        with repo.config_writer() as git_config:
            git_config.set_value("user", "name", colaborador["nombre"])
            git_config.set_value("user", "email", colaborador["email"])
        
        for i in range(colaborador["commits"]):
            if index_commit >= len(grupos):
                print("⚠️  No hay más archivos para asignar")
                break
            
            grupo = grupos[index_commit]
            index_commit += 1

            subgrupos = dividir_por_tamano(grupo)

            for sub_index, subgrupo in enumerate(subgrupos, start=1):
                if not subgrupo:
                    continue

                try:
                    repo.index.add(subgrupo)
                    
                    # Generar mensaje de commit natural
                    mensaje = generar_mensaje_commit(subgrupo)
                    
                    # Hacer commit con la identidad configurada
                    repo.index.commit(mensaje)
                    print(f"✅ {mensaje}")
                    print(f"   📁 {len(subgrupo)} archivo(s) | @{colaborador['username']}")

                    # Push con las credenciales del colaborador
                    repo.git.push("origin", rama)
                    print(f"   ⬆️  Pushed successfully\n")
                    
                    # Contadores
                    archivos_subidos += len(subgrupo)
                    commits_realizados += 1
                    
                except GitCommandError as e:
                    print(f"❌ Error: {e}\n")
                    continue

                # Delay entre pushes (excepto en el último)
                if not (index_commit >= len(grupos) and sub_index == len(subgrupos)):
                    for _ in tqdm(range(DELAY_ENTRE_PUSHES), desc="⏳ Waiting", ncols=70, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}'):
                        time.sleep(1)
                    print()  # Línea en blanco después del delay

    print("\n" + "="*60)
    print("🎉 ¡Proceso completado!")
    print("="*60)
    print(f"✅ Commits realizados: {commits_realizados}/{total_commits}")
    print(f"✅ Archivos subidos: {archivos_subidos}/{len(archivos)}")
    
    if archivos_subidos < len(archivos):
        print(f"\n⚠️  ADVERTENCIA: Faltan {len(archivos) - archivos_subidos} archivos por subir")
        print("   Esto puede deberse a errores durante el push")
    else:
        print("\n🚀 Todos los archivos fueron subidos correctamente")

if __name__ == "__main__":
    main()