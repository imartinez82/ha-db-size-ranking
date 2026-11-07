"""Constantes de DB Size Ranking."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "db_size_ranking"

PERIOD_DAILY: Final = "daily"
PERIOD_TOTAL: Final = "total"
PERIODS: Final = (PERIOD_DAILY, PERIOD_TOTAL)

CONF_TOP_N: Final = "top_n"
CONF_TOTAL_INTERVAL: Final = "total_interval_hours"
CONF_DAILY_INTERVAL: Final = "daily_interval_minutes"
CONF_DAILY_MODE: Final = "daily_mode"

DAILY_MODE_ROLLING: Final = "rolling_24h"
DAILY_MODE_MIDNIGHT: Final = "since_midnight"

DEFAULT_TOP_N: Final = 25
DEFAULT_TOTAL_INTERVAL: Final = 6  # horas
DEFAULT_DAILY_INTERVAL: Final = 60  # minutos
DEFAULT_DAILY_MODE: Final = DAILY_MODE_ROLLING

MAX_TOP_N: Final = 200

# Estimaciones de bytes por fila (datos + índices) que se usan cuando la base de
# datos no permite conocer el tamaño real de cada tabla. Cuando sí se conoce, se
# usan sólo como pesos relativos y el resultado se calibra con el tamaño real.
STATES_ROW_OVERHEAD: Final = 110
ATTRS_ROW_OVERHEAD: Final = 40
STATISTICS_ROW_BYTES: Final = 120

# Filas de "states" que se miran justo antes del inicio del periodo diario para
# saber qué filas de atributos ya existían.
ATTR_THRESHOLD_WINDOW: Final = 5000

SERVICE_REFRESH: Final = "refresh"
SERVICE_GET_RANKING: Final = "get_ranking"
SERVICE_PURGE_TOP: Final = "purge_top"

DEFAULT_PURGE_COUNT: Final = 50

ATTR_PERIOD: Final = "period"
ATTR_TOP_N: Final = "top_n"
