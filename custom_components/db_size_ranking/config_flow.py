"""Flujo de configuración de DB Size Ranking."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DAILY_INTERVAL,
    CONF_DAILY_MODE,
    CONF_TOP_N,
    CONF_TOTAL_INTERVAL,
    DAILY_MODE_MIDNIGHT,
    DAILY_MODE_ROLLING,
    DEFAULT_DAILY_INTERVAL,
    DEFAULT_DAILY_MODE,
    DEFAULT_TOP_N,
    DEFAULT_TOTAL_INTERVAL,
    DOMAIN,
    MAX_TOP_N,
)


def _schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_TOP_N, default=values.get(CONF_TOP_N, DEFAULT_TOP_N)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=MAX_TOP_N, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_DAILY_MODE, default=values.get(CONF_DAILY_MODE, DEFAULT_DAILY_MODE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[DAILY_MODE_ROLLING, DAILY_MODE_MIDNIGHT],
                    translation_key=CONF_DAILY_MODE,
                )
            ),
            vol.Required(
                CONF_DAILY_INTERVAL,
                default=values.get(CONF_DAILY_INTERVAL, DEFAULT_DAILY_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=1440,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TOTAL_INTERVAL,
                default=values.get(CONF_TOTAL_INTERVAL, DEFAULT_TOTAL_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=168,
                    step=1,
                    unit_of_measurement="h",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_TOP_N: int(user_input[CONF_TOP_N]),
        CONF_DAILY_MODE: user_input[CONF_DAILY_MODE],
        CONF_DAILY_INTERVAL: int(user_input[CONF_DAILY_INTERVAL]),
        CONF_TOTAL_INTERVAL: int(user_input[CONF_TOTAL_INTERVAL]),
    }


class DbSizeRankingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuración inicial."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="DB Size Ranking", data=_clean(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DbSizeRankingOptionsFlow()


class DbSizeRankingOptionsFlow(OptionsFlow):
    """Opciones."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
