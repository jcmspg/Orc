"""PyInstaller entry point for the frozen engine: same CLI as the `orc` console script."""

import multiprocessing

from orc.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
