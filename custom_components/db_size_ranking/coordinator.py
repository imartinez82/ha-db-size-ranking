"""Coordinadores que refrescan los rankings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.util import session_scope
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.recorder import async_migration_in_progress
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_INTERVAL,
    CONF_DAILY_MODE,
    CONF_TOP_N,
    CONF_TOTAL_INTERVAL,
    DAILY_MODE_MIDNIGHT,
    DEFAULT_DAILY_INTERVAL,
    DEFAULT_DAILY_MODE,
    DEFAULT_TOP_N,
    DEFAULT_TOTAL_INTERVAL,
    DOMAIN,
    PERIOD_DAILY,
    PERIOD_TOTAL,
)
from .db import (
    Calibration,
    DeviceRef,
    RawUsage,
    build_ranking,
    query_calibration,
    query_raw_usage,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SharedState:
    """Estado compartido entre los dos coordinadores."""

    calibration: Calibration | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class RuntimeData:
    """Datos de ejecución de la entrada de configuración."""

    shared: SharedState
    coordinators: dict[str, RankingCoordinator]


type DbRankingConfigEntry = ConfigEntry[RuntimeData]


def _option(entry: ConfigEntry, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


class RankingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Calcula el ranking de un periodo."""

    config_entry: DbRankingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        period: str,
        shared: SharedState,
    ) -> None:
        if period == PERIOD_TOTAL:
            interval = timedelta(
                hours=_option(entry, CONF_TOTAL_INTERVAL, DEFAULT_TOTAL_INTERVAL)
            )
        else:
            interval = timedelta(
                minutes=_option(entry, CONF_DAILY_INTERVAL, DEFAULT_DAILY_INTERVAL)
            )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{period}",
            update_interval=interval,
        )
        self.period = period
        self.shared = shared
        self.top_n: int = int(_option(entry, CONF_TOP_N, DEFAULT_TOP_N))
        self.daily_mode: str = _option(entry, CONF_DAILY_MODE, DEFAULT_DAILY_MODE)
        self._last_usage: dict[str, RawUsage] = {}
        self._last_meta: dict[str, Any] = {}

    def _start_ts(self) -> float | None:
        if self.period == PERIOD_TOTAL:
            return None
        if self.daily_mode == DAILY_MODE_MIDNIGHT:
            return dt_util.start_of_local_day().timestamp()
        return time.time() - 86400

    def _fetch(
        self, start_ts: float | None, need_calibration: bool
    ) -> tuple[dict[str, RawUsage], Calibration | None]:
        """Se ejecuta en el pool de hilos de base de datos del recorder."""
        with session_scope(hass=self.hass, read_only=True) as session:
            usage = query_raw_usage(session, start_ts)
            calibration = query_calibration(session) if need_calibration else None
        return usage, calibration

    async def _async_update_data(self) -> dict[str, Any]:
        if async_migration_in_progress(self.hass):
            raise UpdateFailed("El recorder está migrando la base de datos")

        async with self.shared.lock:
            start_ts = self._start_ts()
            need_calibration = (
                self.period == PERIOD_TOTAL or self.shared.calibration is None
            )
            started = time.monotonic()
            try:
                usage, calibration = await get_instance(
                    self.hass
                ).async_add_executor_job(self._fetch, start_ts, need_calibration)
            except Exception as err:
                raise UpdateFailed(f"Error consultando la base de datos: {err}") from err
            duration = time.monotonic() - started

            if calibration is not None:
                self.shared.calibration = calibration

        self._last_usage = usage
        self._last_meta = {
            "period": self.period,
            "daily_mode": self.daily_mode if self.period == PERIOD_DAILY else None,
            "since": (
                dt_util.utc_from_timestamp(start_ts).isoformat() if start_ts else None
            ),
            "updated": dt_util.utcnow().isoformat(),
            "query_seconds": round(duration, 2),
        }
        _LOGGER.debug(
            "Ranking %s calculado en %.2f s (%d claves)", self.period, duration, len(usage)
        )
        return self.build(self.top_n)

    def _devices(self) -> dict[str, DeviceRef]:
        """Relaciona cada entity_id con su dispositivo (y su área)."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        refs: dict[str, DeviceRef] = {}
        cache: dict[str, DeviceRef | None] = {}
        for entity in ent_reg.entities.values():
            if not entity.device_id:
                continue
            if entity.device_id not in cache:
                device = dev_reg.async_get(entity.device_id)
                if device is None:
                    cache[entity.device_id] = None
                else:
                    area = area_reg.async_get_area(device.area_id) if device.area_id else None
                    cache[entity.device_id] = DeviceRef(
                        device_id=device.id,
                        name=device.name_by_user or device.name,
                        area=area.name if area else None,
                    )
            ref = cache[entity.device_id]
            if ref is not None:
                refs[entity.entity_id] = ref
        return refs

    def build(self, top_n: int) -> dict[str, Any]:
        """Construye el ranking con los últimos datos leídos."""
        calibration = self.shared.calibration or Calibration()
        # Una entidad existe si tiene estado o está en el registro (aunque esté
        # deshabilitada o su integración aún no haya cargado): sólo las demás
        # se consideran huérfanas.
        names: dict[str, str | None] = {
            entity.entity_id: entity.name or entity.original_name
            for entity in er.async_get(self.hass).entities.values()
        }
        names.update(
            {state.entity_id: state.name for state in self.hass.states.async_all()}
        )
        result = build_ranking(
            self._last_usage, calibration, names, top_n, self._devices()
        )
        result.update(self._last_meta)
        result["top_n"] = top_n
        return result
