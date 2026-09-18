"""Permite `python -m openlegal`, además del comando `openlegal` instalado."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
