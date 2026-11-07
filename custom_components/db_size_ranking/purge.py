"""Borrado del historial de las entidades que más ocupan."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN, get_instance
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, PERIOD_DAILY, PERIOD_TOTAL
from .coordinator import DbRankingConfigEntry

_LOGGER = logging.getLogger(__name__)

# El recorder borra por lotes en segundo plano; se espera un poco antes de
# recalcular el ranking para que ya refleje el borrado.
REFRESH_DELAY = timedelta(seconds=60)
NOTIFICATION_ID = f"{DOMAIN}_purge"


async def async_purge_top(
    hass: HomeAssistant,
    entry: DbRankingConfigEntry,
    count: int,
    period: str = PERIOD_TOTAL,
) -> dict[str, Any]:
    """Borra el historial (estados) de las ``count`` entidades del ranking.

    Las estadísticas a largo plazo no se tocan (panel de Energía, medias).
    """
    coordinator = entry.runtime_data.coordinators[period]
    if coordinator.data is None:
        await coordinator.async_refresh()
    if coordinator.data is None:
        return {"purged": [], "bytes": 0}

    # Los sensores de esta integración no se borran a sí mismos.
    own = {
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    targets = [
        item
        for item in coordinator.build(count)["ranking"]
        if not item["external_statistic"]
        and item["entity_id"] not in own
        and item["bytes_states"] + item["bytes_attributes"] > 0
    ]
    entity_ids = [item["entity_id"] for item in targets]
    freed = sum(item["bytes_states"] + item["bytes_attributes"] for item in targets)

    if entity_ids:
        await hass.services.async_call(
            RECORDER_DOMAIN,
            "purge_entities",
            {"entity_id": entity_ids, "keep_days": 0},
            blocking=True,
        )
    _LOGGER.info(
        "Borrado el historial de %d entidades (~%.1f MiB)", len(entity_ids), freed / 1_048_576
    )

    lines = "\n".join(
        f"{item['rank']}. `{item['entity_id']}` — "
        f"{(item['bytes_states'] + item['bytes_attributes']) / 1_048_576:.2f} MiB"
        for item in targets
    )
    persistent_notification.async_create(
        hass,
        (
            f"Se ha borrado el historial de **{len(entity_ids)} entidades** "
            f"(~{freed / 1_048_576:.1f} MiB). Las estadísticas a largo plazo se "
            "conservan.\n\nSi no quieres que vuelvan a crecer, exclúyelas del "
            "recorder en `configuration.yaml`. En SQLite el archivo no encoge "
            "hasta que pulses **Compactar base de datos**.\n\n" + lines
        ),
        title="DB Size Ranking: historial borrado",
        notification_id=NOTIFICATION_ID,
    )

    _schedule_refresh(hass, entry)
    return {"purged": entity_ids, "bytes": round(freed)}


async def async_purge_orphans(
    hass: HomeAssistant, entry: DbRankingConfigEntry
) -> dict[str, Any]:
    """Borra todo lo guardado de las entidades que ya no existen.

    Incluye sus estadísticas a largo plazo: la entidad ya no está en Home
    Assistant, así que nadie las va a usar.
    """
    coordinator = entry.runtime_data.coordinators[PERIOD_TOTAL]
    if coordinator.data is None:
        await coordinator.async_refresh()
    if coordinator.data is None:
        return {"purged": [], "bytes": 0}

    # build() vuelve a comprobar qué existe ahora mismo (estados y registro).
    orphans = coordinator.build(1)["orphans"]
    entity_ids = [o["entity_id"] for o in orphans]
    with_stats = [o["entity_id"] for o in orphans if o["statistics"]]
    freed = sum(o["bytes"] for o in orphans)

    if entity_ids:
        await hass.services.async_call(
            RECORDER_DOMAIN,
            "purge_entities",
            {"entity_id": entity_ids, "keep_days": 0},
            blocking=True,
        )
    if with_stats:
        get_instance(hass).async_clear_statistics(with_stats)
    _LOGGER.info(
        "Borrados los datos de %d entidades huérfanas (~%.1f MiB)",
        len(entity_ids),
        freed / 1_048_576,
    )

    if entity_ids:
        persistent_notification.async_create(
            hass,
            (
                f"Se han borrado los datos de **{len(entity_ids)} entidades que ya no "
                f"existen** (~{freed / 1_048_576:.1f} MiB), estadísticas incluidas. "
                "En SQLite el archivo no encoge hasta que pulses **Compactar base "
                "de datos**.\n\n"
                + "\n".join(f"- `{o['entity_id']}` — {o['mb']:.2f} MiB" for o in orphans)
            ),
            title="DB Size Ranking: huérfanas borradas",
            notification_id=NOTIFICATION_ID,
        )
    _schedule_refresh(hass, entry)
    return {"purged": entity_ids, "bytes": round(freed)}


def _schedule_refresh(hass: HomeAssistant, entry: DbRankingConfigEntry) -> None:
    """Recalcula los rankings cuando el recorder haya terminado de borrar."""

    @callback
    def _refresh(_now: Any) -> None:
        for key in (PERIOD_TOTAL, PERIOD_DAILY):
            entry.async_create_background_task(
                hass,
                entry.runtime_data.coordinators[key].async_refresh(),
                f"{DOMAIN}_refresh_after_purge_{key}",
            )

    entry.async_on_unload(
        async_call_later(hass, REFRESH_DELAY, HassJob(_refresh, cancel_on_shutdown=True))
    )


async def async_repack(hass: HomeAssistant) -> None:
    """Compacta la base de datos para que el archivo encoja."""
    await hass.services.async_call(
        RECORDER_DOMAIN, "purge", {"repack": True}, blocking=True
    )
