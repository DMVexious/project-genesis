"""PyInstaller entry point: builds orchestrator.exe (the Tauri sidecar)."""
import multiprocessing

from genesis.server import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
