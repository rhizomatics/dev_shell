"""`open(...)` - open a URL for something in this Home Assistant instance.

Currently just `open(hass)`, opening the instance's own frontend URL (whichever
one homeassistant.helpers.network.get_url resolves). Expected to grow forms that
take a specific entity/device/area and build a deep link into the frontend.
"""

from __future__ import annotations

import webbrowser
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url


def open_target(target: Any) -> str:
    """Open target in a browser and return the URL opened."""
    if not isinstance(target, HomeAssistant):
        raise TypeError(f"open() doesn't know how to open a {type(target).__name__} yet")
    url = get_url(target)
    webbrowser.open(url)
    return url
