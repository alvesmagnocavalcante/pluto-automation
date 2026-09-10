import logging
import os
from pathlib import Path

import flet as ft

from pluto_ui.app import main

if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("PLUTO_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ft.run(main, assets_dir=str(Path(__file__).resolve().parent / "assets"))
