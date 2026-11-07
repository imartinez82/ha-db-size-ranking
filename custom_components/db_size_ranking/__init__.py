"""DB Size Ranking: qué entidades ocupan más espacio en la base de datos."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_PERIOD,
    ATTR_TOP_N,
    DOMAIN,
    PERIOD_DAILY,
    PERIOD_TOTAL,
    PERIODS,
    DEFAULT_PURGE_COUNT,
    SERVICE_GET_RANKING,
    SERVICE_PURGE_ORPHANS,
    SERVICE_PURGE_TOP,
    SERVICE_REFRESH,
)
from .coordinator import DbRankingConfigEntry, RankingCoordinator, RuntimeData, SharedState
from .panel import async_register_panel, async_unregister_panel
from .purge import async_purge_orphans, async_purge_top

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

REFRESH_SCHEMA = vol.Schema({vol.Optional(ATTR_PERIOD): vol.In(PERIODS)})
GET_RANKING_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PERIOD, default=PERIOD_TOTAL): vol.In(PERIODS),
        vol.Optional(ATTR_TOP_N): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100000)
        ),
    }
)


PURGE_TOP_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_TOP_N, default=DEFAULT_PURGE_COUNT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1000)
        ),
        vol.Optional(ATTR_PERIOD, default=PERIOD_TOTAL): vol.In(PERIODS),
    }
)


def _get_entry(hass: HomeAssistant) -> DbRankingConfigEntry:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    raise ServiceValidationError("DB Size Ranking no está cargado")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Registra los servicios."""

    async def _refresh(call: ServiceCall) -> None:
        entry = _get_entry(hass)
        coordinators = entry.runtime_data.coordinators
        periods = [call.data[ATTR_PERIOD]] if ATTR_PERIOD in call.data else [
            PERIOD_TOTAL,
            PERIOD_DAILY,
        ]
        for period in periods:
            await coordinators[period].async_refresh()

    async def _get_ranking(call: ServiceCall) -> ServiceResponse:
        entry = _get_entry(hass)
        coordinator = entry.runtime_data.coordinators[call.data[ATTR_PERIOD]]
        if coordinator.data is None:
            await coordinator.async_refresh()
        if coordinator.data is None:
            raise ServiceValidationError("Todavía no hay datos del ranking")
        top_n = call.data.get(ATTR_TOP_N, coordinator.top_n)
        return coordinator.build(top_n)

    async def _purge_top(call: ServiceCall) -> ServiceResponse:
        entry = _get_entry(hass)
        return await async_purge_top(
            hass, entry, call.data[ATTR_TOP_N], call.data[ATTR_PERIOD]
        )

    async def _purge_orphans(call: ServiceCall) -> ServiceResponse:
        return await async_purge_orphans(hass, _get_entry(hass))

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh, REFRESH_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RANKING,
        _get_ranking,
        GET_RANKING_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PURGE_TOP,
        _purge_top,
        PURGE_TOP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PURGE_ORPHANS,
        _purge_orphans,
        vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DbRankingConfigEntry) -> bool:
    """Configura la integración."""
    shared = SharedState()
    coordinators = {
        period: RankingCoordinator(hass, entry, period, shared) for period in PERIODS
    }
    entry.runtime_data = RuntimeData(shared=shared, coordinators=coordinators)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass)

    async def _initial_refresh() -> None:
        # Primero el total (calcula la calibración) y luego el diario.
        await coordinators[PERIOD_TOTAL].async_refresh()
        await coordinators[PERIOD_DAILY].async_refresh()

    @callback
    def _on_started(_hass: HomeAssistant) -> None:
        # La consulta puede tardar en bases de datos grandes: no bloquea el arranque.
        entry.async_create_background_task(
            hass, _initial_refresh(), f"{DOMAIN}_initial_refresh"
        )

    entry.async_on_unload(async_at_started(hass, _on_started))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: DbRankingConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: DbRankingConfigEntry) -> bool:
    """Descarga la integración."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_unregister_panel(hass)
    return unloaded
