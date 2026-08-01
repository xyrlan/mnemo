"""Entry point for the frozen build.

PyInstaller needs a real script to analyse; ``python -m mnemo`` has no
equivalent. Kept deliberately thin so the frozen and packaged builds run the
same ``main()``.
"""
import sys

from mnemo.cli import main

if __name__ == "__main__":
    sys.exit(main())
