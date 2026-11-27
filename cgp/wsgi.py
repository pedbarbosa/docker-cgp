"""The WSGI app for gunicorn: gunicorn cgp.wsgi:app"""

import logging

from .app import create_app

# rrdtool and request errors go to gunicorn's error log, i.e. docker logs
gunicorn_logger = logging.getLogger("gunicorn.error")
logging.basicConfig(level=logging.INFO, handlers=gunicorn_logger.handlers or None)

app = create_app()
