"""Herramienta para crear historiales de commits reproducibles en Git.

El paquete está organizado por responsabilidades (ver README):

    cli            -> interfaz de línea de comandos
    app            -> orquestación del flujo
    git_repository -> único punto de contacto con el binario de git
    files          -> descubrimiento de archivos y respeto de .gitignore
    security       -> validación de contenido y redacción de credenciales
    planner        -> reparto determinista de archivos en commits
    messages       -> generación de mensajes de commit
    credentials    -> almacenamiento seguro de tokens
    state          -> persistencia para reanudar ejecuciones
    ai             -> mensajes con IA (OpenCode) y respaldo heurístico
    env            -> carga de archivos .env
    gui            -> interfaz gráfica (tkinter)
    gui_state      -> validación y estado de la interfaz
"""

__version__ = "2.2.1"

__all__ = ["__version__"]
