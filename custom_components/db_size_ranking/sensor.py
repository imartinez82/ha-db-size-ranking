"""Sensores de DB Size Ranking."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PERIOD_DAILY, PERIOD_TOTAL
from .coordinator import DbRankingConfigEntry, RankingCoordinator

# El ranking puede ser grande: no se guarda en la base de datos (sería absurdo
# que el sensor que mide el espacio fuese de los que más ocupan).
_UNRECORDED = frozenset(
    {
        "ranking",
        "calibration",
        "since",
        "updated",
        "query_seconds",
        "entity_count",
        "orphan_count",
        "top_n",
        "period",
        "daily_mode",
        "calibrated",
        "dialect",
        "tables_mb",
        "device_count",
        "no_device_mb",
        "no_device_entities",
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DbRankingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crea los sensores."""
    coordinators = entry.runtime_data.coordinators
    async_add_entities(
        [
            RankingSensor(coordinators[PERIOD_TOTAL], entry),
            RankingSensor(coordinators[PERIOD_DAILY], entry),
            DeviceRankingSensor(coordinators[PERIOD_TOTAL], entry),
            DeviceRankingSensor(coordinators[PERIOD_DAILY], entry),
            DatabaseSizeSensor(coordinators[PERIOD_TOTAL], entry),
        ]
    )


class _Base(CoordinatorEntity[RankingCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.MEBIBYTES
    _attr_suggested_display_precision = 2
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = _UNRECORDED

    def __init__(self, coordinator: RankingCoordinator, entry: DbRankingConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="DB Size Ranking",
            manufacturer="DB Size Ranking",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="homeassistant://db-size-ranking",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None


class RankingSensor(_Base):
    """Ranking de entidades de un periodo."""

    def __init__(self, coordinator: RankingCoordinator, entry: DbRankingConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_translation_key = f"ranking_{coordinator.period}"
        self._attr_unique_id = f"{entry.entry_id}_ranking_{coordinator.period}"
        self._attr_icon = (
            "mdi:database-clock" if coordinator.period == PERIOD_DAILY else "mdi:database"
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None:
            return None
        return round(data["total_bytes"] / 1_048_576, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        ranking = data["ranking"]
        return {
            "top_entity": ranking[0]["entity_id"] if ranking else None,
            "period": data["period"],
            "daily_mode": data["daily_mode"],
            "since": data["since"],
            "updated": data["updated"],
            "query_seconds": data["query_seconds"],
            "entity_count": data["entity_count"],
            "orphan_count": data["orphan_count"],
            "top_n": data["top_n"],
            "calibrated": data["calibration"]["calibrated"],
            "ranking": ranking,
        }


class DeviceRankingSensor(_Base):
    """Ranking de dispositivos de un periodo (suma de sus entidades)."""

    def __init__(self, coordinator: RankingCoordinator, entry: DbRankingConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_translation_key = f"device_ranking_{coordinator.period}"
        self._attr_unique_id = f"{entry.entry_id}_device_ranking_{coordinator.period}"
        self._attr_icon = (
            "mdi:devices" if coordinator.period == PERIOD_TOTAL else "mdi:clock-outline"
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None:
            return None
        return round(data["device_total_bytes"] / 1_048_576, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        ranking = data["device_ranking"]
        return {
            "top_device": ranking[0]["name"] if ranking else None,
            "period": data["period"],
            "daily_mode": data["daily_mode"],
            "since": data["since"],
            "updated": data["updated"],
            "device_count": data["device_count"],
            "no_device_mb": round(data["no_device_bytes"] / 1_048_576, 3),
            "no_device_entities": data["no_device_entities"],
            "top_n": data["top_n"],
            "calibrated": data["calibration"]["calibrated"],
            "ranking": ranking,
        }


class DatabaseSizeSensor(_Base):
    """Tamaño total de la base de datos."""

    _attr_translation_key = "database_size"
    _attr_icon = "mdi:database-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RankingCoordinator, entry: DbRankingConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_database_size"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None or not data["calibration"]["db_size"]:
            return None
        return round(data["calibration"]["db_size"] / 1_048_576, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        cal = data["calibration"]
        return {
            "dialect": cal["dialect"],
            "calibrated": cal["calibrated"],
            "updated": data["updated"],
            "tables_mb": {
                name: round(size / 1_048_576, 2)
                for name, size in sorted(
                    cal["table_sizes"].items(), key=lambda kv: -kv[1]
                )
            },
        }
