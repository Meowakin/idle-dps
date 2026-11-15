"""PyInstaller entry point (relative imports don't work in a script run directly)."""
from idle_dps.server import main

if __name__ == "__main__":
    main()
