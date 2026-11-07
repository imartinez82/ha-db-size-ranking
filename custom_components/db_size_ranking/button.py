"""Botones de DB Size Ranking."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_PURGE_COUNT, DOMAIN
from .coordinator import DbRankingConfigEntry
from .purge import async_purge_top, async_repack


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DbRankingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crea los botones."""
    async_add_entities([PurgeTopButton(entry), RepackButton(entry)])


class _Base(ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: DbRankingConfigEntry, key: str) -> None:
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="DB Size Ranking",
            manufacturer="DB Size Ranking",
            entry_type=DeviceEntryType.SERVICE,
        )


class PurgeTopButton(_Base):
    """Borra el historial de las 50 entidades que más ocupan."""

    _attr_icon = "mdi:database-remove"

    def __init__(self, entry: DbRankingConfigEntry) -> None:
        super().__init__(entry, "purge_top")

    async def async_press(self) -> None:
        await async_purge_top(self.hass, self._entry, DEFAULT_PURGE_COUNT)


class RepackButton(_Base):
    """Compacta la base de datos."""

    _attr_icon = "mdi:database-arrow-down"

    def __init__(self, entry: DbRankingConfigEntry) -> None:
        super().__init__(entry, "repack")

    async def async_press(self) -> None:
        await async_repack(self.hass)
