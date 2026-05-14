"""
conftest.py for tests/evaluation/

Configures sys.path so `evaluation.*` modules can be imported
without installing the package — both from the backend root and
from the tests directory.
"""

import sys
import os

# Ensure the backend root is on sys.path so both `app.*` and `evaluation.*` resolve
BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
