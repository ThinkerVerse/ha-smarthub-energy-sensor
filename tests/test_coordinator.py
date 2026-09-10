"""Test file for SmartHub sensor (statistics)"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from collections.abc import Generator
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smarthub import async_setup_entry
from custom_components.smarthub.api import SmartHubAPI, SmartHubAPIError, SmartHubLocation
from custom_components.smarthub.const import DOMAIN, ELECTRIC_SERVICE

from custom_components.smarthub.api import Aggregation
from custom_components.smarthub.sensor import SmartHubDataUpdateCoordinator
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    get_metadata,
    statistics_during_period,
)
from datetime import timedelta
from functools import partial
from homeassistant.util import dt as dt_util


from homeassistant.components.recorder import get_instance

@pytest.fixture(autouse=True)
def mock_smarthub_api(hass) -> Generator[AsyncMock]:
    """Mock the config entry ..."""

    api = SmartHubAPI(
        email="test@example.com",
        password="testpass",
        account_id="123456",
        timezone="UTC",
        mfa_totp="",
        host="test.smarthub.coop"
    )

    with patch(
        "custom_components.smarthub.api.SmartHubAPI", autospec=True
    ) as mock_api:

        mock_api.timezone="UTC"
        mock_api.parse_usage = api.parse_usage
        mock_api.get_service_locations.return_value = []
        mock_api.get_energy_data.return_value = {}

        # Run the real chunking logic against the mocked single-chunk fetch, so
        # the first-run import path is genuinely exercised.
        async def _history(**kwargs):
            return await SmartHubAPI.get_energy_data_history(mock_api, **kwargs)

        mock_api.get_energy_data_history = _history
        yield mock_api


@pytest.fixture()
def mock_config_entry(hass) -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="SmartHub Test",
        data={
            "email": "test@example.com",
            "password": "testpass",
            "account_id": "123456",
            "location_id": "789012",
            "host": "test.smarthub.coop",
            "poll_interval": 60,
            "timezone": "UTC",
            "mfa_totp": "",
        },
        unique_id="test@example.com_test.smarthub.coop_123456",
    )

async def test_coordinator_first_run_forward_meter(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """Test the coordinator on its first run with no existing statistics."""
    mock_smarthub_api.get_service_locations.return_value = [
      SmartHubLocation(
        id="11111",
        service=ELECTRIC_SERVICE,
        description="test location",
        provider="test provider",
      )
    ]

    test_data = {
        "data": {
            "ELECTRIC": [
                {
                    "type": "USAGE",
                    "meters": [
                     {'meterNumber': '1ND91111111', 'seriesId': '1ND91111111', 'flowDirection': 'FORWARD', 'isNetMeter': False}, # Forward meter is full consumption.
                    ],
                    "series": [
                        {
                            "meterNumber": "1ND91111111", "name": "1ND91111111",
                            "data": [
                                {"x": 1762215300000, "y":   1},
                                {"x": 1762216200000, "y":  10},
                                {"x": 1762217100000, "y": 100},
                                {"x": 1762218900000, "y": 1},
                            ]
                        },
                    ]
                }
            ]
        }
    }

    mock_smarthub_api.get_energy_data.return_value = mock_smarthub_api.parse_usage(test_data)

    coordinator = SmartHubDataUpdateCoordinator(hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720), config_entry=mock_config_entry)

    await coordinator._async_update_data()

    await async_wait_recording_done(hass)

    # Check stats for electric account '111111'
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {
            "smarthub:smarthub_energy_sensor_daily_123456_11111",
        },
        "hour",
        None,
        {"state", "sum"},
    )

    # The first hour's statistics summary is...
    assert stats["smarthub:smarthub_energy_sensor_daily_123456_11111"][0]["sum"] == 111.0


async def test_coordinator_first_run_net_meter(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """Test the coordinator on its first run with no existing statistics."""
    mock_smarthub_api.get_service_locations.return_value = [
      SmartHubLocation(
        id="11111",
        service=ELECTRIC_SERVICE,
        description="test location",
        provider="test provider",
      )
    ]

    test_data = {
        "data": {
            "ELECTRIC": [
                {
                    "type": "USAGE",
                    "meters": [
                     {'meterNumber': '1ND81111111', 'seriesId': '1ND81111111', 'flowDirection': 'NET', 'isNetMeter': True}, # Includes in and out bound flows (posirive/negative)
                     {'meterNumber': '1ND91111111', 'seriesId': '1ND91111111', 'flowDirection': 'FORWARD', 'isNetMeter': False}, # Forward meter is full consumption.
                    ],
                    "series": [
                        {
                            "meterNumber": "1ND91111111", "name": "1ND91111111",
                            "data": [
                                {"x": 1762215300000, "y":   1},
                                {"x": 1762216200000, "y":  10},
                                {"x": 1762217100000, "y": 100},
                                {"x": 1762218900000, "y": 1},
                                {"x": 1762219800000, "y": 1},
                            ]
                        },
                        {
                            "meterNumber": "1ND81111111", "name": "1ND81111111",
                            "data": [
                                {"x": 1762215300000, "y":   1},
                                {"x": 1762216200000, "y":  -5}, # generated 15 KW of power this hour - returned 5 to the grid
                                {"x": 1762217100000, "y": 100},
                                {"x": 1762218900000, "y": 1},
                                {"x": 1762219800000, "y":  -1}, # generated 2 KW of power this hour - returned 1 to the grid
                            ]
                        },
                    ]
                }
            ]
        }
    }

    mock_smarthub_api.get_energy_data.return_value = mock_smarthub_api.parse_usage(test_data)
    assert mock_smarthub_api.get_energy_data.return_value["USAGE_RETURN"][0]['consumption'] == 5
    assert mock_smarthub_api.get_energy_data.return_value["USAGE_RETURN"][1]['consumption'] == 1

    coordinator = SmartHubDataUpdateCoordinator(hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720), config_entry=mock_config_entry)

    await coordinator._async_update_data()

    await async_wait_recording_done(hass)

    # Check stats for electric account '111111'
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {
            "smarthub:smarthub_energy_sensor_daily_123456_11111",
            "smarthub:smarthub_energy_return_sensor_daily_123456_11111",
        },
        "hour",
        None,
        {"state", "sum"},
    )

    # The first hour's statistics summary is...
    assert stats["smarthub:smarthub_energy_sensor_daily_123456_11111"][0]["sum"] == 101.0
    assert stats["smarthub:smarthub_energy_sensor_daily_123456_11111"][1]["sum"] == 102.0
    assert stats["smarthub:smarthub_energy_return_sensor_daily_123456_11111"][0]["sum"] == 5
    assert stats["smarthub:smarthub_energy_return_sensor_daily_123456_11111"][1]["sum"] == 6

async def test_coordinator_first_run_return_meter(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """Test the coordinator on its first run with no existing statistics."""
    mock_smarthub_api.get_service_locations.return_value = [
      SmartHubLocation(
        id="11111",
        service=ELECTRIC_SERVICE,
        description="test location",
        provider="test provider",
      )
    ]

    test_data = {
        "data": {
            "ELECTRIC": [
                {
                    "type": "USAGE",
                    "meters": [
                     {'meterNumber': '1ND91111111', 'seriesId': '1ND87334444', 'flowDirection': 'RETURN', 'isNetMeter': False}, # Includes in and out bound flows (posirive/negative)
                     {'meterNumber': '1ND91111111', 'seriesId': '1ND86200137', 'flowDirection': 'FORWARD', 'isNetMeter': False}, # Forward meter is full consumption.
                    ],
                    "series": [
                        {
                            "meterNumber": "1ND86200137", "name": "1ND86200137",
                            "data": [
                                {"x": 1762215300000, "y":   1},
                                {"x": 1762216200000, "y":  10},
                                {"x": 1762217100000, "y": 100},
                                {"x": 1762218900000, "y": 1},
                                {"x": 1762219800000, "y": 1},
                            ]
                        },
                        {
                            "meterNumber": "1ND87334444", "name": "1ND87334444",
                            "data": [
                                {"x": 1762215300000, "y":   0},
                                {"x": 1762216200000, "y":  5}, # generated 15 KW of power this hour - returned 5 to the grid
                                {"x": 1762217100000, "y": 0},
                                {"x": 1762218900000, "y": 0},
                                {"x": 1762219800000, "y":  1}, # generated 2 KW of power this hour - returned 1 to the grid
                            ]
                        },
                    ]
                }
            ]
        }
    }

    mock_smarthub_api.get_energy_data.return_value = mock_smarthub_api.parse_usage(test_data)
    assert mock_smarthub_api.get_energy_data.return_value["USAGE_RETURN"][0]['consumption'] == 5
    assert mock_smarthub_api.get_energy_data.return_value["USAGE_RETURN"][1]['consumption'] == 1

    coordinator = SmartHubDataUpdateCoordinator(hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720), config_entry=mock_config_entry)

    await coordinator._async_update_data()

    await async_wait_recording_done(hass)

    # Check stats for electric account '111111'
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {
            "smarthub:smarthub_energy_sensor_daily_123456_11111",
            "smarthub:smarthub_energy_return_sensor_daily_123456_11111",
        },
        "hour",
        None,
        {"state", "sum"},
    )

    # The first hour's statistics summary is...
    assert stats["smarthub:smarthub_energy_sensor_daily_123456_11111"][0]["sum"] == 111.0
    assert stats["smarthub:smarthub_energy_sensor_daily_123456_11111"][1]["sum"] == 113.0
    assert stats["smarthub:smarthub_energy_return_sensor_daily_123456_11111"][0]["sum"] == 5
    assert stats["smarthub:smarthub_energy_return_sensor_daily_123456_11111"][1]["sum"] == 6



async def test_coordinator_first_run_with_cost(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """A provider returning COST alongside USAGE gets a cost statistic."""
    mock_smarthub_api.get_service_locations.return_value = [
      SmartHubLocation(
        id="11111",
        service=ELECTRIC_SERVICE,
        description="test location",
        provider="test provider",
      )
    ]

    meters = [
        {'meterNumber': '1ND91111111', 'seriesId': '1ND91111111', 'flowDirection': 'FORWARD', 'isNetMeter': False},
    ]

    def _series(values):
        timestamps = [1762215300000, 1762216200000, 1762217100000, 1762218900000]
        return [
            {
                "meterNumber": "1ND91111111", "name": "1ND91111111",
                "data": [{"x": x, "y": y} for x, y in zip(timestamps, values)],
            },
        ]

    test_data = {
        "data": {
            "ELECTRIC": [
                {"type": "USAGE", "meters": meters, "series": _series([1, 10, 100, 1])},
                # Cost for the same readings, at a flat 10 cents per unit.
                {"type": "COST", "meters": meters, "series": _series([0.1, 1.0, 10.0, 0.1])},
            ]
        }
    }

    mock_smarthub_api.get_energy_data.return_value = mock_smarthub_api.parse_usage(test_data)

    coordinator = SmartHubDataUpdateCoordinator(hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720), config_entry=mock_config_entry)

    await coordinator._async_update_data()

    await async_wait_recording_done(hass)

    consumption_id = "smarthub:smarthub_energy_sensor_daily_123456_11111"
    cost_id = "smarthub:smarthub_energy_cost_daily_123456_11111"

    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {consumption_id, cost_id},
        "hour",
        None,
        {"state", "sum"},
    )

    # Cost tracks consumption one-for-one, so the two series must have the same
    # number of points at the same times.
    assert len(stats[cost_id]) == len(stats[consumption_id])
    assert [row["start"] for row in stats[cost_id]] == [
        row["start"] for row in stats[consumption_id]
    ]

    # The sub-hour readings are consolidated the same way usage is.
    assert stats[consumption_id][0]["sum"] == 111.0
    assert stats[cost_id][0]["sum"] == pytest.approx(11.1)
    assert stats[cost_id][1]["sum"] == pytest.approx(11.2)

    # Cost statistics are stored without a unit so the Energy dashboard renders
    # them in the currency configured in Home Assistant.
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={cost_id})
    )
    assert metadata[cost_id][1]["unit_of_measurement"] is None


async def test_coordinator_first_run_without_cost(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """A provider returning no COST entry gets no cost statistic."""
    mock_smarthub_api.get_service_locations.return_value = [
      SmartHubLocation(
        id="11111",
        service=ELECTRIC_SERVICE,
        description="test location",
        provider="test provider",
      )
    ]

    test_data = {
        "data": {
            "ELECTRIC": [
                {
                    "type": "USAGE",
                    "meters": [
                     {'meterNumber': '1ND91111111', 'seriesId': '1ND91111111', 'flowDirection': 'FORWARD', 'isNetMeter': False},
                    ],
                    "series": [
                        {
                            "meterNumber": "1ND91111111", "name": "1ND91111111",
                            "data": [
                                {"x": 1762215300000, "y":   1},
                                {"x": 1762218900000, "y":   1},
                            ]
                        },
                    ]
                }
            ]
        }
    }

    mock_smarthub_api.get_energy_data.return_value = mock_smarthub_api.parse_usage(test_data)

    coordinator = SmartHubDataUpdateCoordinator(hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720), config_entry=mock_config_entry)

    await coordinator._async_update_data()

    await async_wait_recording_done(hass)

    cost_id = "smarthub:smarthub_energy_cost_daily_123456_11111"
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={cost_id})
    )
    assert cost_id not in metadata



async def test_coordinator_imports_multi_chunk_history(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """First run imports far beyond one chunk, stopping at the provider's floor."""
    from datetime import datetime, timezone as _tz

    mock_smarthub_api.get_service_locations.return_value = [
      SmartHubLocation(
        id="11111",
        service=ELECTRIC_SERVICE,
        description="test location",
        provider="test provider",
      )
    ]

    # The provider holds nothing before this date, however far back we ask.
    floor = datetime(2026, 1, 1, tzinfo=_tz.utc)
    end = datetime(2026, 9, 1, tzinfo=_tz.utc)
    windows = []

    async def fake_get_energy_data(location, aggregation, start_datetime=None, end_datetime=None):
        windows.append((start_datetime, end_datetime))
        start = start_datetime.replace(tzinfo=_tz.utc)
        stop = end_datetime.replace(tzinfo=_tz.utc)
        readings = []
        day = max(start, floor)
        while day < stop:
            readings.append({
                "reading_time": day,
                "consumption": 1.0,
                "raw_timestamp": int(day.timestamp() * 1000),
            })
            day += timedelta(days=1)
        return {"USAGE": readings} if readings else {}

    mock_smarthub_api.get_energy_data = fake_get_energy_data

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, "history_days": 3650}
    )

    coordinator = SmartHubDataUpdateCoordinator(
        hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720),
        config_entry=mock_config_entry,
    )
    # The deep import is the background/service path, not the inline first run.
    await coordinator.async_import_history(3650)
    await async_wait_recording_done(hass)

    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, dt_util.utc_from_timestamp(0), None,
        {"smarthub:smarthub_energy_sensor_123456_11111"}, "hour", None, {"state", "sum"},
    )
    rows = stats["smarthub:smarthub_energy_sensor_123456_11111"]

    # Far more than the 90 days a single chunk covers, and more than the old cap.
    assert len(rows) > 90
    # It walked back past the floor once, found nothing, and stopped - rather
    # than issuing all 41 chunks the 3650 day cap would allow.
    assert len(windows) < 41
    # Chronological with a monotonically rising running total across chunks.
    starts = [r["start"] for r in rows]
    assert starts == sorted(starts)
    sums = [r["sum"] for r in rows]
    assert sums == sorted(sums)
    assert sums[-1] == float(len(rows))


async def test_async_import_history_reimports_on_demand(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """The service entry point imports history for every location."""
    location = SmartHubLocation(
        id="11111", service=ELECTRIC_SERVICE,
        description="test location", provider="test provider",
    )
    mock_smarthub_api.get_service_locations.return_value = [location]

    requested = []

    async def fake_history(location, aggregation, max_days, **kwargs):
        requested.append((aggregation, max_days))
        return {"USAGE": []}

    mock_smarthub_api.get_energy_data_history = fake_history
    mock_smarthub_api.get_energy_data.return_value = {"USAGE": []}

    coordinator = SmartHubDataUpdateCoordinator(
        hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720),
        config_entry=mock_config_entry,
    )
    await coordinator.async_import_history(1200)

    # Both aggregations refreshed, with the requested depth.
    assert (Aggregation.HOURLY, 1200) in requested
    assert (Aggregation.DAILY, 1200) in requested


async def test_first_run_defers_deep_history_to_the_background(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """Setup only imports the opening chunk, and flags the rest for later."""
    location = SmartHubLocation(
        id="11111", service=ELECTRIC_SERVICE,
        description="test location", provider="test provider",
    )
    mock_smarthub_api.get_service_locations.return_value = [location]

    asked = []

    async def fake_history(location, aggregation, max_days, **kwargs):
        asked.append(max_days)
        return {"USAGE": []}

    mock_smarthub_api.get_energy_data_history = fake_history

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, "history_days": 1825}
    )

    coordinator = SmartHubDataUpdateCoordinator(
        hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720),
        config_entry=mock_config_entry,
    )
    await coordinator._insert_statistics(location, Aggregation.HOURLY)

    # Bounded to one chunk so config entry setup is not held up for minutes.
    assert asked == [90]
    assert coordinator.history_backfill_pending is True


async def test_first_run_within_one_chunk_needs_no_backfill(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_smarthub_api: AsyncMock,
) -> None:
    """A shallow configured history is satisfied inline."""
    location = SmartHubLocation(
        id="11111", service=ELECTRIC_SERVICE,
        description="test location", provider="test provider",
    )
    asked = []

    async def fake_history(location, aggregation, max_days, **kwargs):
        asked.append(max_days)
        return {"USAGE": []}

    mock_smarthub_api.get_energy_data_history = fake_history

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, "history_days": 30}
    )

    coordinator = SmartHubDataUpdateCoordinator(
        hass, api=mock_smarthub_api, update_interval=timedelta(minutes=720),
        config_entry=mock_config_entry,
    )
    await coordinator._insert_statistics(location, Aggregation.HOURLY)

    assert asked == [30]
    assert coordinator.history_backfill_pending is False


async def async_wait_recording_done(hass) -> None:
    """Async wait until recording is done."""
    await hass.async_block_till_done()
    get_instance(hass)._async_commit(dt_util.utcnow())
    await hass.async_block_till_done()
    await hass.async_add_executor_job(get_instance(hass).block_till_done)
    await hass.async_block_till_done()
