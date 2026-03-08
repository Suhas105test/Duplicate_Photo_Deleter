import logging
import sys

from ui import launch

def _configure_logging():
    """Set up root logger with console + file handlers."""
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    file_handler = logging.FileHandler("smart_photo_cleaner.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)


if __name__ == "__main__":
    _configure_logging()
    log = logging.getLogger("main")
    log.info("Smart Photo Cleaner starting")
    try:
        log.info("Launching UI...")
        launch()
        log.info("UI closed normally")
    except Exception:
        log.critical("Unhandled crash", exc_info=True)
        raise
