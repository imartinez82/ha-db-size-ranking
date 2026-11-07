"""Consultas a la base de datos del recorder.

Todo lo que hay aquí se ejecuta en el pool de hilos de base de datos del
recorder (nunca en el event loop) y funciona con SQLite, MariaDB/MySQL y
PostgreSQL.

Cómo se calcula el espacio de cada entidad
------------------------------------------
* ``states``: número de filas × coste fijo por fila + longitud del valor del
  estado.
* ``state_attributes``: las filas de atributos están deduplicadas; a cada entidad
  se le asigna el tamaño de las filas de atributos distintas que referencia.
  En el ranking diario sólo cuentan las filas de atributos creadas dentro del
  periodo.
* ``statistics`` y ``statistics_short_term``: filas × bytes por fila.

Cuando el motor permite conocer el tamaño real de cada tabla (dbstat en SQLite,
information_schema en MariaDB/MySQL, pg_total_relation_size en PostgreSQL) los
pesos anteriores se reescalan para que la suma coincida con el tamaño real de
la tabla, índices incluidos. Si no, se usan las estimaciones de ``const.py``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from homeassistant.components.recorder.db_schema import (
    StateAttributes,
    States,
    StatesMeta,
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)

from .const import (
    ATTR_THRESHOLD_WINDOW,
    ATTRS_ROW_OVERHEAD,
    STATES_ROW_OVERHEAD,
    STATISTICS_ROW_BYTES,
)

_LOGGER = logging.getLogger(__name__)

TABLE_STATES = States.__tablename__
TABLE_ATTRS = StateAttributes.__tablename__
TABLE_STATS = Statistics.__tablename__
TABLE_STATS_ST = StatisticsShortTerm.__tablename__


@dataclass
class RawUsage:
    """Contadores en bruto de una entidad (o id de estadística)."""

    states_rows: int = 0
    states_len: int = 0
    attrs_rows: float = 0
    attrs_len: float = 0
    stats_rows: int = 0
    stats_st_rows: int = 0


@dataclass
class Calibration:
    """Factores para pasar de contadores en bruto a bytes."""

    dialect: str = "unknown"
    calibrated: bool = False
    db_size: int | None = None
    table_sizes: dict[str, int] = field(default_factory=dict)
    states_factor: float = 1.0
    attrs_factor: float = 1.0
    stats_bytes_per_row: float = float(STATISTICS_ROW_BYTES)
    stats_st_bytes_per_row: float = float(STATISTICS_ROW_BYTES)
    total_states_rows: int = 0
    total_attrs_rows: int = 0
    total_stats_rows: int = 0
    total_stats_st_rows: int = 0

    def bytes_for(self, raw: RawUsage) -> dict[str, float]:
        """Bytes estimados por tabla para unos contadores."""
        return {
            "states": (raw.states_rows * STATES_ROW_OVERHEAD + raw.states_len)
            * self.states_factor,
            "attributes": (raw.attrs_len + raw.attrs_rows * ATTRS_ROW_OVERHEAD)
            * self.attrs_factor,
            "statistics": raw.stats_rows * self.stats_bytes_per_row
            + raw.stats_st_rows * self.stats_st_bytes_per_row,
        }


def _dialect(session: Session) -> str:
    return session.get_bind().dialect.name


def query_raw_usage(session: Session, start_ts: float | None) -> dict[str, RawUsage]:
    """Devuelve los contadores en bruto por entity_id / statistic_id.

    ``start_ts`` = None significa todo el histórico.
    """
    usage: dict[str, RawUsage] = defaultdict(RawUsage)

    entity_ids: dict[int, str] = {
        metadata_id: entity_id
        for metadata_id, entity_id in session.execute(
            select(StatesMeta.metadata_id, StatesMeta.entity_id)
        )
        if entity_id
    }

    # --- states -----------------------------------------------------------
    stmt = select(
        States.metadata_id,
        func.count(),
        func.coalesce(func.sum(func.length(States.state)), 0),
    ).group_by(States.metadata_id)
    if start_ts is not None:
        stmt = stmt.where(States.last_updated_ts >= start_ts)

    for metadata_id, rows, length in session.execute(stmt):
        entity_id = entity_ids.get(metadata_id)
        if entity_id is None:
            continue
        item = usage[entity_id]
        item.states_rows += int(rows or 0)
        item.states_len += int(length or 0)

    # --- state_attributes ---------------------------------------------------
    attr_threshold = _attributes_threshold(session, start_ts)
    if attr_threshold is not False:
        pairs = select(States.metadata_id, States.attributes_id).where(
            States.attributes_id.is_not(None)
        )
        if start_ts is not None:
            pairs = pairs.where(States.last_updated_ts >= start_ts)
        if attr_threshold:
            pairs = pairs.where(States.attributes_id > attr_threshold)
        sub = pairs.distinct().subquery()
        # Una fila de atributos compartida por varias entidades se reparte a
        # partes iguales entre ellas, para no contarla dos veces.
        shares = (
            select(sub.c.attributes_id, func.count().label("n"))
            .group_by(sub.c.attributes_id)
            .subquery()
        )
        share = 1.0 / shares.c.n

        stmt = (
            select(
                sub.c.metadata_id,
                func.coalesce(func.sum(share), 0),
                func.coalesce(
                    func.sum(func.length(StateAttributes.shared_attrs) * share), 0
                ),
            )
            .select_from(sub)
            .join(shares, shares.c.attributes_id == sub.c.attributes_id)
            .join(StateAttributes, StateAttributes.attributes_id == sub.c.attributes_id)
            .group_by(sub.c.metadata_id)
        )
        for metadata_id, rows, length in session.execute(stmt):
            entity_id = entity_ids.get(metadata_id)
            if entity_id is None:
                continue
            item = usage[entity_id]
            item.attrs_rows += float(rows or 0)
            item.attrs_len += float(length or 0)

    # --- statistics ---------------------------------------------------------
    statistic_ids: dict[int, str] = {
        meta_id: statistic_id
        for meta_id, statistic_id in session.execute(
            select(StatisticsMeta.id, StatisticsMeta.statistic_id)
        )
        if statistic_id
    }
    for table, attr in ((Statistics, "stats_rows"), (StatisticsShortTerm, "stats_st_rows")):
        stmt = select(table.metadata_id, func.count()).group_by(table.metadata_id)
        if start_ts is not None:
            stmt = stmt.where(table.start_ts >= start_ts)
        for meta_id, rows in session.execute(stmt):
            statistic_id = statistic_ids.get(meta_id)
            if statistic_id is None:
                continue
            item = usage[statistic_id]
            setattr(item, attr, getattr(item, attr) + int(rows or 0))

    return dict(usage)


def _attributes_threshold(session: Session, start_ts: float | None) -> int | bool:
    """attributes_id a partir del cual las filas de atributos son "nuevas".

    * 0     -> contar todas (histórico completo, o no hay filas anteriores)
    * N     -> contar sólo attributes_id > N
    * False -> no hay filas de estado en el periodo
    """
    if start_ts is None:
        return 0
    first_state_id = session.execute(
        select(func.min(States.state_id)).where(States.last_updated_ts >= start_ts)
    ).scalar()
    if first_state_id is None:
        return False
    # Cada fila de atributos nueva se crea junto a la fila de estado que la usa
    # por primera vez, así que el máximo attributes_id de las últimas filas de
    # estado anteriores al periodo marca qué filas de atributos ya existían.
    threshold = session.execute(
        select(func.max(States.attributes_id)).where(
            States.state_id < first_state_id,
            States.state_id >= first_state_id - ATTR_THRESHOLD_WINDOW,
        )
    ).scalar()
    return int(threshold or 0)


def query_calibration(session: Session) -> Calibration:
    """Calcula los factores de calibración y el tamaño de la base de datos."""
    cal = Calibration(dialect=_dialect(session))

    total_rows, total_len = session.execute(
        select(func.count(), func.coalesce(func.sum(func.length(States.state)), 0))
    ).one()
    attr_rows, attr_len = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(func.length(StateAttributes.shared_attrs)), 0),
        )
    ).one()
    stats_rows = session.execute(select(func.count()).select_from(Statistics)).scalar()
    stats_st_rows = session.execute(
        select(func.count()).select_from(StatisticsShortTerm)
    ).scalar()

    cal.total_states_rows = int(total_rows or 0)
    cal.total_attrs_rows = int(attr_rows or 0)
    cal.total_stats_rows = int(stats_rows or 0)
    cal.total_stats_st_rows = int(stats_st_rows or 0)

    # Se deja para el final: en PostgreSQL un error aborta la transacción.
    sizes, db_size = _table_sizes(session, cal.dialect)
    cal.table_sizes = sizes
    cal.db_size = db_size

    raw_states = total_rows * STATES_ROW_OVERHEAD + total_len
    raw_attrs = attr_len + attr_rows * ATTRS_ROW_OVERHEAD

    if sizes.get(TABLE_STATES) and raw_states:
        cal.states_factor = sizes[TABLE_STATES] / raw_states
        cal.calibrated = True
    if sizes.get(TABLE_ATTRS) and raw_attrs:
        cal.attrs_factor = sizes[TABLE_ATTRS] / raw_attrs
    if sizes.get(TABLE_STATS) and stats_rows:
        cal.stats_bytes_per_row = sizes[TABLE_STATS] / stats_rows
    if sizes.get(TABLE_STATS_ST) and stats_st_rows:
        cal.stats_st_bytes_per_row = sizes[TABLE_STATS_ST] / stats_st_rows

    return cal


def _table_sizes(session: Session, dialect: str) -> tuple[dict[str, int], int | None]:
    """Tamaño real (datos + índices) de cada tabla y de la base de datos."""
    sizes: dict[str, int] = {}
    db_size: int | None = None
    try:
        if dialect == "sqlite":
            page_size = session.execute(text("PRAGMA page_size")).scalar() or 0
            page_count = session.execute(text("PRAGMA page_count")).scalar() or 0
            db_size = int(page_size) * int(page_count)
            try:
                rows = session.execute(
                    text(
                        "SELECT m.tbl_name, SUM(d.pgsize) FROM dbstat AS d "
                        "JOIN sqlite_master AS m ON d.name = m.name "
                        "GROUP BY m.tbl_name"
                    )
                )
                sizes = {name: int(size or 0) for name, size in rows}
            except Exception as err:  # noqa: BLE001 - dbstat no compilado
                _LOGGER.debug("dbstat no disponible en SQLite: %s", err)
        elif dialect in ("mysql", "mariadb"):
            rows = session.execute(
                text(
                    "SELECT table_name, COALESCE(data_length, 0) + "
                    "COALESCE(index_length, 0) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
            )
            sizes = {str(name): int(size or 0) for name, size in rows}
            db_size = sum(sizes.values())
        elif dialect == "postgresql":
            rows = session.execute(
                text(
                    "SELECT c.relname, pg_total_relation_size(c.oid) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind = 'r' AND n.nspname = current_schema()"
                )
            )
            sizes = {str(name): int(size or 0) for name, size in rows}
            db_size = int(
                session.execute(
                    text("SELECT pg_database_size(current_database())")
                ).scalar()
                or 0
            )
    except Exception as err:  # noqa: BLE001 - sin permisos, etc.
        _LOGGER.warning(
            "No se pudo leer el tamaño de las tablas (%s); se usarán estimaciones: %s",
            dialect,
            err,
        )
    if db_size is None and sizes:
        db_size = sum(sizes.values())
    return sizes, db_size


@dataclass
class DeviceRef:
    """Dispositivo al que pertenece una entidad."""

    device_id: str
    name: str | None
    area: str | None = None


# Entidades que se listan dentro de cada dispositivo del ranking.
DEVICE_TOP_ENTITIES = 10


def build_device_ranking(
    entries: list[dict[str, Any]],
    devices: dict[str, DeviceRef],
    db_size: int | None,
    top_n: int,
) -> dict[str, Any]:
    """Agrupa el ranking de entidades por dispositivo (sólo tipos JSON).

    ``entries`` es el ranking completo de entidades (sin recortar) y
    ``devices`` relaciona cada entity_id con su dispositivo.
    """
    groups: dict[str, dict[str, Any]] = {}
    no_device_bytes = 0
    no_device_entities = 0
    for entry in entries:
        ref = devices.get(entry["entity_id"])
        if ref is None:
            no_device_bytes += entry["bytes"]
            no_device_entities += 1
            continue
        group = groups.get(ref.device_id)
        if group is None:
            group = groups[ref.device_id] = {
                "device_id": ref.device_id,
                "name": ref.name,
                "area": ref.area,
                "bytes": 0,
                "states": 0,
                "statistics": 0,
                "bytes_states": 0,
                "bytes_attributes": 0,
                "bytes_statistics": 0,
                "entities": [],
            }
        group["bytes"] += entry["bytes"]
        group["states"] += entry["states"]
        group["statistics"] += entry["statistics"] + entry["statistics_short_term"]
        group["bytes_states"] += entry["bytes_states"]
        group["bytes_attributes"] += entry["bytes_attributes"]
        group["bytes_statistics"] += entry["bytes_statistics"]
        group["entities"].append(entry)

    total_bytes = sum(g["bytes"] for g in groups.values())
    ranking = sorted(groups.values(), key=lambda g: (-g["bytes"], g["name"] or ""))
    for position, group in enumerate(ranking, start=1):
        group["rank"] = position
        group["mb"] = round(group["bytes"] / 1_048_576, 3)
        group["pct"] = round(group["bytes"] * 100 / total_bytes, 2) if total_bytes else 0
        if db_size:
            group["pct_db"] = round(group["bytes"] * 100 / db_size, 2)
        members = group.pop("entities")  # ya vienen ordenadas de mayor a menor
        group["entity_count"] = len(members)
        group["entities"] = [
            {"entity_id": m["entity_id"], "name": m["name"], "mb": m["mb"]}
            for m in members[:DEVICE_TOP_ENTITIES]
        ]

    return {
        "device_total_bytes": round(total_bytes),
        "device_count": len(ranking),
        "no_device_bytes": round(no_device_bytes),
        "no_device_entities": no_device_entities,
        "device_ranking": ranking[:top_n],
    }


def build_ranking(
    usage: dict[str, RawUsage],
    calibration: Calibration,
    names: dict[str, str | None],
    top_n: int,
    devices: dict[str, DeviceRef] | None = None,
) -> dict[str, Any]:
    """Convierte los contadores en el ranking final (sólo tipos JSON)."""
    entries: list[dict[str, Any]] = []
    total_bytes = 0.0
    for key, raw in usage.items():
        parts = calibration.bytes_for(raw)
        size = sum(parts.values())
        if size <= 0:
            continue
        total_bytes += size
        external = ":" in key  # estadística externa (p.ej. "recorder:xxx")
        entries.append(
            {
                "entity_id": key,
                "name": names.get(key),
                "exists": external or key in names,
                "external_statistic": external,
                "bytes": round(size),
                "states": raw.states_rows,
                "attribute_rows": round(raw.attrs_rows, 2),
                "statistics": raw.stats_rows,
                "statistics_short_term": raw.stats_st_rows,
                "bytes_states": round(parts["states"]),
                "bytes_attributes": round(parts["attributes"]),
                "bytes_statistics": round(parts["statistics"]),
            }
        )

    entries.sort(key=lambda e: (-e["bytes"], e["entity_id"]))
    for position, entry in enumerate(entries, start=1):
        entry["rank"] = position
        entry["mb"] = round(entry["bytes"] / 1_048_576, 3)
        entry["pct"] = round(entry["bytes"] * 100 / total_bytes, 2) if total_bytes else 0
        if calibration.db_size:
            entry["pct_db"] = round(entry["bytes"] * 100 / calibration.db_size, 2)

    result = {
        "total_bytes": round(total_bytes),
        "entity_count": len(entries),
        "orphan_count": sum(1 for e in entries if not e["exists"]),
        "ranking": entries[:top_n],
        "orphans": [
            {
                "entity_id": e["entity_id"],
                "mb": e["mb"],
                "bytes": e["bytes"],
                "statistics": e["statistics"] + e["statistics_short_term"],
            }
            for e in entries
            if not e["exists"]
        ],
        "calibration": asdict(calibration),
    }
    result.update(
        build_device_ranking(entries, devices or {}, calibration.db_size, top_n)
    )
    return result
