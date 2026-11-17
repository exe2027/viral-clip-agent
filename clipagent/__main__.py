"""python -m clipagent <comando> — punto de entrada del CLI."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
