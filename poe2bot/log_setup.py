import logging
import logging.handlers
import os

from poe2bot import config

_LOGGER_NAME = "poe2bot"
_configured = False


def get_logger():
    """Shared logger: rotates to logs/poe2bot.log and echoes to the console.

    Level defaults to INFO (lifecycle events, warnings). Per-step chatter is
    logged at DEBUG -- bump this logger's level to see it:
    logging.getLogger('poe2bot').setLevel(logging.DEBUG)
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        logger.setLevel(logging.INFO)

        os.makedirs(config.LOGS_DIR, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(config.LOGS_DIR, "poe2bot.log"), maxBytes=1_000_000, backupCount=3)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(threadName)s] %(levelname)s %(message)s"))
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console_handler)

        _configured = True
    return logger
