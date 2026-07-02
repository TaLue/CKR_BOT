"""Allow ``python -m ckrbot``."""

import sys

from ckrbot.cli import main

if __name__ == "__main__":
    sys.exit(main())
