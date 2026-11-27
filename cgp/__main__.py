"""Runs the development server: python -m cgp"""

import logging
import os

from .app import create_app

logging.basicConfig(level=logging.INFO)
create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
