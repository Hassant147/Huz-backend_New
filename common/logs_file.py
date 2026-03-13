import logging
from pathlib import Path

from django.conf import settings

# Configure logger
logger = logging.getLogger('my_app_logger')
logger.setLevel(logging.INFO)

if not logger.handlers:
    log_path = Path(settings.VIEW_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
