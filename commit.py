"""Punto de entrada compatible con la versión anterior.

Se conserva para que ``python commit.py`` siga funcionando. La implementación
vive en el paquete :mod:`autocommit`; se recomienda ``python -m autocommit``.
"""

from __future__ import annotations

from autocommit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
