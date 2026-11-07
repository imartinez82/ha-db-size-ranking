"""Página propia de la integración en la barra lateral."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

PANEL_URL_PATH = "db-size-ranking"
PANEL_COMPONENT = "db-size-ranking-panel"
STATIC_URL = f"/{DOMAIN}_static"
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Registra la página (sólo para administradores)."""
    if not hass.data.get(_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False
                )
            ]
        )
        hass.data[_STATIC_REGISTERED] = True

    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="DB Size Ranking",
        sidebar_icon="mdi:database-search",
        module_url=f"{STATIC_URL}/{PANEL_COMPONENT}.js?v={version}",
        require_admin=True,
        config_panel_domain=DOMAIN,
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Quita la página de la barra lateral."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
