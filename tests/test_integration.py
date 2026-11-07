"""Pruebas de DB Size Ranking contra un Home Assistant real con recorder."""

from __future__ import annotations

from datetime import timedelta
import json

from freezegun.api import FrozenDateTimeFactory
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql

from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.db_schema import StateAttributes
from homeassistant.components.recorder.util import session_scope
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.db_size_ranking.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable(recorder_mock, enable_custom_integrations):
    yield


OPTIONS = {
    "top_n": 10,
    "daily_mode": "rolling_24h",
    "daily_interval_minutes": 60,
    "total_interval_hours": 6,
}


async def _write(hass: HomeAssistant, entity_id: str, count: int, big_attrs: bool):
    for i in range(count):
        attrs = {"friendly_name": entity_id.split(".")[1].title()}
        if big_attrs:
            attrs["payload"] = "x" * 500 + str(i)  # atributos que cambian cada vez
        hass.states.async_set(entity_id, str(i * 1.5), attrs)
        await hass.async_block_till_done()


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=OPTIONS, title="DB Size Ranking")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    return entry


async def test_ranking_total_and_daily(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    now = dt_util.utcnow()

    # Datos antiguos (hace 3 días): sólo deben aparecer en el ranking total.
    freezer.move_to(now - timedelta(days=3))
    await _write(hass, "sensor.antiguo", 300, big_attrs=True)
    await async_wait_recording_done(hass)

    freezer.move_to(now)
    await _write(hass, "sensor.grande", 120, big_attrs=True)
    await _write(hass, "sensor.mediano", 120, big_attrs=False)
    await _write(hass, "sensor.pequeno", 5, big_attrs=False)
    await async_wait_recording_done(hass)

    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    total = hass.states.get("sensor.db_size_ranking_total_ranking")
    daily = hass.states.get("sensor.db_size_ranking_daily_ranking")
    size = hass.states.get("sensor.db_size_ranking_database_size")
    assert total is not None and daily is not None and size is not None
    assert total.state not in ("unknown", "unavailable")

    total_ids = [e["entity_id"] for e in total.attributes["ranking"]]
    daily_ids = [e["entity_id"] for e in daily.attributes["ranking"]]
    print("TOTAL", json.dumps(total.attributes["ranking"][:4], indent=1))
    print("DAILY", daily_ids)
    print("SIZE", size.state, size.attributes)

    assert total_ids[0] == "sensor.antiguo"
    assert total_ids.index("sensor.grande") < total_ids.index("sensor.mediano")
    assert total_ids.index("sensor.mediano") < total_ids.index("sensor.pequeno")

    assert "sensor.antiguo" not in daily_ids
    assert daily_ids[0] == "sensor.grande"

    grande = next(e for e in total.attributes["ranking"] if e["entity_id"] == "sensor.grande")
    assert grande["states"] == 120
    assert grande["attribute_rows"] == 120
    assert grande["name"] == "Grande"
    assert grande["exists"] is True
    mediano = next(e for e in total.attributes["ranking"] if e["entity_id"] == "sensor.mediano")
    assert mediano["attribute_rows"] == 1

    pct = sum(e["pct"] for e in total.attributes["ranking"])
    assert pct <= 100.01

    assert float(size.state) > 0
    assert float(total.state) > float(daily.state) > 0

    # Servicio con respuesta
    resp = await hass.services.async_call(
        DOMAIN, "get_ranking", {"period": "daily", "top_n": 2},
        blocking=True, return_response=True,
    )
    assert len(resp["ranking"]) == 2
    assert resp["ranking"][0]["entity_id"] == "sensor.grande"

    # El atributo 'ranking' no se guarda en la base de datos.
    await async_wait_recording_done(hass)

    def _attrs_with_ranking():
        with session_scope(hass=hass, read_only=True) as session:
            return [
                row[0]
                for row in session.execute(select(StateAttributes.shared_attrs))
                if '"ranking"' in row[0]
            ]

    assert await get_instance(hass).async_add_executor_job(_attrs_with_ranking) == []


async def test_orphans_and_daily_attribute_threshold(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    now = dt_util.utcnow()
    freezer.move_to(now - timedelta(days=2))
    # Atributos estáticos creados hace 2 días
    for i in range(50):
        hass.states.async_set("sensor.estatico", str(i), {"big": "y" * 2000})
    hass.states.async_set("sensor.borrado", "1", {"a": 1})
    await async_wait_recording_done(hass)

    freezer.move_to(now)
    for i in range(50, 60):
        hass.states.async_set("sensor.estatico", str(i), {"big": "y" * 2000})
    await async_wait_recording_done(hass)
    hass.states.async_remove("sensor.borrado")
    await hass.async_block_till_done()

    await _setup(hass)
    daily = hass.states.get("sensor.db_size_ranking_daily_ranking")
    total = hass.states.get("sensor.db_size_ranking_total_ranking")

    d = next(e for e in daily.attributes["ranking"] if e["entity_id"] == "sensor.estatico")
    # La fila de atributos ya existía antes del periodo: no cuenta en el diario.
    assert d["states"] == 10
    assert d["attribute_rows"] == 0

    t = next(e for e in total.attributes["ranking"] if e["entity_id"] == "sensor.estatico")
    assert t["attribute_rows"] == 1

    borrado = next(e for e in total.attributes["ranking"] if e["entity_id"] == "sensor.borrado")
    assert borrado["exists"] is False
    assert total.attributes["orphan_count"] >= 1


async def test_options_and_unload(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.a", "1")
    await async_wait_recording_done(hass)
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "top_n": 3, "daily_mode": "since_midnight"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinators["daily"].daily_mode == "since_midnight"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_config_flow(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT


async def test_initial_refresh_after_start(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Sin llamar al servicio, los sensores se rellenan solos tras el arranque."""
    hass.states.async_set("sensor.a", "1")
    await async_wait_recording_done(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.db_size_ranking_total_ranking").state not in (
        "unknown",
        "unavailable",
    )
    assert hass.states.get("sensor.db_size_ranking_daily_ranking").state not in (
        "unknown",
        "unavailable",
    )




async def test_statistics_counted(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        async_import_statistics,
    )

    hass.states.async_set("sensor.energia", "1", {"friendly_name": "Energía"})
    await async_wait_recording_done(hass)
    top_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    stats = [
        {"start": top_hour - timedelta(hours=h), "state": h, "sum": h}
        for h in range(1, 49)
    ]
    meta = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": "Energía",
        "source": "recorder",
        "statistic_id": "sensor.energia",
        "unit_class": "energy",
        "unit_of_measurement": "kWh",
    }
    async_import_statistics(hass, meta, stats)
    async_add_external_statistics(
        hass, {**meta, "source": "prueba", "statistic_id": "prueba:externa"}, stats[:10]
    )
    await async_wait_recording_done(hass)

    await _setup(hass)
    total = hass.states.get("sensor.db_size_ranking_total_ranking").attributes["ranking"]
    daily = hass.states.get("sensor.db_size_ranking_daily_ranking").attributes["ranking"]
    t = next(e for e in total if e["entity_id"] == "sensor.energia")
    d = next(e for e in daily if e["entity_id"] == "sensor.energia")
    assert t["statistics"] == 48
    assert t["states"] == 1
    assert 23 <= d["statistics"] <= 24
    assert t["bytes_statistics"] > 0
    ext = next(e for e in total if e["entity_id"] == "prueba:externa")
    assert ext["external_statistic"] is True and ext["exists"] is True
    assert ext["statistics"] == 10


async def test_without_table_sizes(
    recorder_mock: Recorder, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el motor no da el tamaño de las tablas, se usan estimaciones."""
    from custom_components.db_size_ranking import db

    monkeypatch.setattr(db, "_table_sizes", lambda session, dialect: ({}, None))
    await _write(hass, "sensor.x", 30, big_attrs=True)
    await async_wait_recording_done(hass)
    await _setup(hass)
    total = hass.states.get("sensor.db_size_ranking_total_ranking")
    assert total.attributes["calibrated"] is False
    assert total.attributes["ranking"][0]["bytes"] > 0
    assert "pct_db" not in total.attributes["ranking"][0]
    assert hass.states.get("sensor.db_size_ranking_database_size").state == "unknown"


async def test_markdown_card_template(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """La plantilla de la tarjeta del README se renderiza."""
    import re
    from pathlib import Path

    from homeassistant.helpers.template import Template

    await _write(hass, "sensor.x", 5, big_attrs=True)
    hass.states.async_set("sensor.borrar", "1")
    await async_wait_recording_done(hass)
    hass.states.async_remove("sensor.borrar")
    await _setup(hass)

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    content = re.search(r"content: >\n(.*?)```", readme, re.S).group(1)
    content = "\n".join(line[2:] for line in content.splitlines())
    content = content.replace("ranking_total", "total_ranking").replace(
        "tamano_base_de_datos", "database_size"
    )
    out = Template(content, hass).async_render(parse_result=False)
    print(out)
    assert "sensor.borrar ⚠️" in out
    assert "| 1 |" in out


async def test_device_ranking(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Las entidades se agrupan por dispositivo."""
    from homeassistant.helpers import (
        area_registry as ar,
        device_registry as dr,
        entity_registry as er,
    )

    source = MockConfigEntry(domain="test")
    source.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    salon = ar.async_get(hass).async_create("Salón")

    meteo = dev_reg.async_get_or_create(
        config_entry_id=source.entry_id, identifiers={("test", "meteo")}, name="Estación"
    )
    dev_reg.async_update_device(meteo.id, area_id=salon.id, name_by_user="Meteo jardín")
    enchufe = dev_reg.async_get_or_create(
        config_entry_id=source.entry_id, identifiers={("test", "plug")}, name="Enchufe"
    )
    for uid, object_id, device in (
        ("t", "temperatura", meteo),
        ("h", "humedad", meteo),
        ("p", "potencia", enchufe),
    ):
        ent_reg.async_get_or_create(
            "sensor", "test", uid, suggested_object_id=object_id, device_id=device.id
        )

    await _write(hass, "sensor.temperatura", 60, big_attrs=True)
    await _write(hass, "sensor.humedad", 60, big_attrs=True)
    await _write(hass, "sensor.potencia", 80, big_attrs=True)
    await _write(hass, "sensor.suelto", 10, big_attrs=False)
    await async_wait_recording_done(hass)
    await _setup(hass)

    devices = hass.states.get("sensor.db_size_ranking_device_ranking_total")
    daily = hass.states.get("sensor.db_size_ranking_device_ranking_daily")
    assert devices is not None and daily is not None
    ranking = devices.attributes["ranking"]
    print("DEVICES", json.dumps(ranking, indent=1))

    assert [d["name"] for d in ranking] == ["Meteo jardín", "Enchufe"]
    assert devices.attributes["top_device"] == "Meteo jardín"
    assert devices.attributes["device_count"] == 2
    assert devices.attributes["no_device_entities"] >= 1  # sensor.suelto, etc.
    meteo_entry = ranking[0]
    assert meteo_entry["area"] == "Salón"
    assert meteo_entry["entity_count"] == 2
    assert meteo_entry["states"] == 120
    assert {e["entity_id"] for e in meteo_entry["entities"]} == {
        "sensor.temperatura",
        "sensor.humedad",
    }
    assert round(sum(d["pct"] for d in ranking)) == 100
    assert float(devices.state) == round(sum(d["bytes"] for d in ranking) / 1_048_576, 3)
    assert [d["name"] for d in daily.attributes["ranking"]] == ["Meteo jardín", "Enchufe"]

    resp = await hass.services.async_call(
        DOMAIN, "get_ranking", {"period": "total", "top_n": 1},
        blocking=True, return_response=True,
    )
    assert [d["name"] for d in resp["device_ranking"]] == ["Meteo jardín"]
