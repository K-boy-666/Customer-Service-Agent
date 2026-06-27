"""Compatibility shim for the production REST API in ``src/order_api.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SRC_ORDER_API = Path(__file__).resolve().parent / "src" / "order_api.py"
_SRC_DIR = str(_SRC_ORDER_API.parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
_SPEC = importlib.util.spec_from_file_location("_customer_service_order_api", _SRC_ORDER_API)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Cannot load REST API module from {_SRC_ORDER_API}")

_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

app = _MODULE.app


def __getattr__(name: str):
    return getattr(_MODULE, name)
