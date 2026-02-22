import logging

# Configure logger
logger = logging.getLogger('my_app_logger')
logger.setLevel(logging.INFO)

# Create file handler
file_handler = logging.FileHandler('view_logs.txt')
file_handler.setLevel(logging.INFO)

# Create formatter and add it to the handler
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(file_handler)
