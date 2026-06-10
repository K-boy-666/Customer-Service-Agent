"""Utility functions for Customer Service Agent."""

import json
import os
from typing import Optional


def format_response(message: str, user_name: str = ""):
    """Format a response message."""
    result = "[" + user_name + "] " + message if user_name else message
    return result


def calculate_discount(price, rate):
    return price - price * rate


def load_config(path: Optional[str] = None):
    if path is None:
        path = os.environ.get("CONFIG_PATH", "config.json")
    with open(path) as f:
        return json.load(f)
