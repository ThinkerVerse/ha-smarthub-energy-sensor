import pytest
from datetime import timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from custom_components.smarthub.api import SmartHubAPI

@pytest.mark.parametrize("password", [
    "simplepassword",
    "password with spaces",
    "password#with#hashes",
    "password&with&ampersands",
    "password?with?questions",
    "password%with%percents",
    "password+with+plus",
    "complex!@#$%^&*()_+-=[]{}|;':\",./<>?password"
])
@pytest.mark.asyncio
async def test_get_token_encoding(password):
    """Test get_token correctly handles and encodes various passwords."""
    email = "test+user@example.com"
    
    api = SmartHubAPI(
        email=email,
        password=password,
        account_id="123456",
        timezone="UTC",
        mfa_totp="",
        host="test.smarthub.coop"
    )

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value='{"authorizationToken": "fake_token"}')
    mock_response.json = AsyncMock(return_value={"authorizationToken": "fake_token"})

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_response)
    # Ensure the context manager works for 'async with session.post(...)'
    mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_session.post.return_value.__aexit__ = AsyncMock()

    with patch.object(SmartHubAPI, "_get_session", return_value=mock_session):
        token = await api.get_token()
        
        assert token == "fake_token"
        
        # Check the arguments to post
        args, kwargs = mock_session.post.call_args
        
        # We must use 'data' for form-encoded POST body, not 'params' for URL
        assert "data" in kwargs, "Credentials should be sent in the request body (data), not URL parameters"
        assert "params" not in kwargs or not kwargs["params"], "Credentials should not be sent as URL parameters"
        
        sent_payload = kwargs.get("data")
        assert sent_payload["password"] == password
        assert sent_payload["userId"] == email


def _make_api():
    """Build an API client for parser tests (no network involved)."""
    return SmartHubAPI(
        email="test@example.com",
        password="password",
        account_id="40983403",
        timezone="America/New_York",
        mfa_totp="",
        host="test.smarthub.coop",
    )


def _make_entry(entry_type, values, flow_direction="FORWARD"):
    """Build an ELECTRIC entry shaped like a real utility-usage/poll response."""
    hours = [1786320000000 + (index * 3600000) for index in range(len(values))]
    return {
        "type": entry_type,
        "unitOfMeasure": "KWH",
        "meters": [
            {
                "meterNumber": "65214682",
                "seriesId": "65214682",
                "flowDirection": flow_direction,
                "channel": 1,
            }
        ],
        "series": [
            {
                "name": "65214682",
                "meterNumber": "65214682",
                "data": [{"x": x, "y": y} for x, y in zip(hours, values)],
            }
        ],
    }


def test_parse_usage_extracts_cost_series():
    """COST entries are parsed alongside USAGE and share the same timestamps."""
    usage_values = [2.05, 1.15, 1.02, 0.9, 0.77]
    cost_values = [0.23, 0.13, 0.11, 0.10, 0.09]

    parsed = _make_api().parse_usage(
        {
            "status": "COMPLETE",
            "data": {
                "ELECTRIC": [
                    _make_entry("USAGE", usage_values),
                    _make_entry("COST", cost_values),
                ]
            },
        }
    )

    assert [read["consumption"] for read in parsed["USAGE"]] == usage_values
    assert [read["consumption"] for read in parsed["COST"]] == cost_values
    # The Energy dashboard pairs cost with consumption by timestamp, so the two
    # series have to line up exactly.
    assert [read["reading_time"] for read in parsed["COST"]] == [
        read["reading_time"] for read in parsed["USAGE"]
    ]


def test_parse_usage_without_cost_entry():
    """Providers that only return USAGE do not gain empty cost series."""
    parsed = _make_api().parse_usage(
        {"data": {"ELECTRIC": [_make_entry("USAGE", [1.0, 2.0])]}}
    )

    assert "USAGE" in parsed
    assert "COST" not in parsed
    assert "COST_RETURN" not in parsed


def test_parse_usage_net_meter_splits_cost_and_compensation():
    """A net meter splits charges into COST and bill credits into COST_RETURN."""
    parsed = _make_api().parse_usage(
        {
            "data": {
                "ELECTRIC": [
                    _make_entry("USAGE", [2.0, -1.0, 3.0], flow_direction="NET"),
                    _make_entry("COST", [0.22, -0.11, 0.34], flow_direction="NET"),
                ]
            }
        }
    )

    assert [read["consumption"] for read in parsed["COST"]] == [0.22, 0, 0.34]
    assert [read["consumption"] for read in parsed["COST_RETURN"]] == [0, 0.11, 0]


class _FakeAggregation:
    """Minimal stand-in so history tests do not need the real enum."""
    label = "Hourly"


async def test_get_energy_data_history_walks_backwards_in_chunks():
    """History is fetched in chunks and merged oldest reading first."""
    from datetime import datetime, timedelta
    from custom_components.smarthub.api import Aggregation

    api = _make_api()
    calls = []

    async def fake_get_energy_data(location, aggregation, start_datetime, end_datetime):
        calls.append((start_datetime, end_datetime))
        # One reading per chunk, timestamped at the chunk start.
        return {
            "meter_name": "65214682",
            "USAGE": [{"reading_time": start_datetime, "consumption": 1.0, "raw_timestamp": 0}],
            "COST": [{"reading_time": start_datetime, "consumption": 0.1, "raw_timestamp": 0}],
        }

    api.get_energy_data = fake_get_energy_data

    end = datetime(2026, 9, 10, 0, 0)
    result = await api.get_energy_data_history(
        location=None, aggregation=Aggregation.HOURLY,
        max_days=250, chunk_days=90, end_datetime=end,
    )

    # 90 + 90 + 70 == 250: the final chunk is trimmed, never overshoots.
    assert [(e - s).days for s, e in calls] == [90, 90, 70]
    # Chunks are contiguous and walk backwards.
    assert calls[0][1] == end
    assert calls[1][1] == calls[0][0]
    assert calls[2][1] == calls[1][0]

    assert len(result["USAGE"]) == 3
    assert len(result["COST"]) == 3
    # Merged oldest first, regardless of the order they were fetched in.
    assert result["USAGE"][0]["reading_time"] < result["USAGE"][-1]["reading_time"]
    assert result["meter_name"] == "65214682"


async def test_get_energy_data_history_stops_at_start_of_history():
    """The walk stops at the first empty chunk rather than the configured cap."""
    from datetime import datetime
    from custom_components.smarthub.api import Aggregation

    api = _make_api()
    calls = []

    async def fake_get_energy_data(location, aggregation, start_datetime, end_datetime):
        calls.append(start_datetime)
        if len(calls) > 2:
            return {}  # provider holds nothing older
        return {"USAGE": [{"reading_time": start_datetime, "consumption": 1.0}]}

    api.get_energy_data = fake_get_energy_data

    result = await api.get_energy_data_history(
        location=None, aggregation=Aggregation.HOURLY,
        max_days=3650, chunk_days=90, end_datetime=datetime(2026, 9, 10),
    )

    # Would have been 41 chunks if it ran to the cap.
    assert len(calls) == 3
    assert len(result["USAGE"]) == 2


async def test_get_energy_data_history_deduplicates_chunk_edges():
    """Readings repeated across adjacent chunks are stored once."""
    from datetime import datetime
    from custom_components.smarthub.api import Aggregation

    api = _make_api()
    shared = datetime(2026, 6, 1, 0, 0)
    seen = []

    async def fake_get_energy_data(location, aggregation, start_datetime, end_datetime):
        seen.append(start_datetime)
        if len(seen) > 2:
            return {}
        # Every chunk reports the same reading time.
        return {"USAGE": [{"reading_time": shared, "consumption": 5.0}]}

    api.get_energy_data = fake_get_energy_data

    result = await api.get_energy_data_history(
        location=None, aggregation=Aggregation.HOURLY,
        max_days=300, chunk_days=90, end_datetime=datetime(2026, 9, 10),
    )

    assert len(result["USAGE"]) == 1


@pytest.mark.parametrize("raw,expected", [
    (5, 5.0),          # bare number of seconds
    ("5", 5.0),        # quoted
    (5.0, 5.0),
    (30000, 30.0),     # implausible as seconds -> milliseconds
    (0, None),
    (-1, None),
    (None, None),
    ("nonsense", None),
    (True, None),      # bool is not a duration
])
def test_coerce_seconds(raw, expected):
    """Settings values arrive as loosely typed JSON scalars."""
    assert SmartHubAPI._coerce_seconds(raw) == expected


async def test_get_poll_settings_falls_back_when_unavailable():
    """A provider that does not expose the settings registry still polls sanely."""
    from custom_components.smarthub.const import DEFAULT_POLL_WAIT, DEFAULT_POLL_MAX_WAIT

    api = _make_api()

    async def no_setting(name):
        return None

    api.get_setting = no_setting

    wait, max_wait = await api.get_poll_settings()
    assert wait == DEFAULT_POLL_WAIT
    assert max_wait == DEFAULT_POLL_MAX_WAIT


async def test_get_poll_settings_uses_provider_values_and_caches():
    """The provider's advertised cadence wins, and is only read once."""
    api = _make_api()
    reads = []

    async def one_setting(name):
        reads.append(name)
        return 8 if "Interval" in name else 120

    api.get_setting = one_setting

    assert await api.get_poll_settings() == (8.0, 120.0)
    assert await api.get_poll_settings() == (8.0, 120.0)
    assert len(reads) == 2  # cached, not re-read on the second call


async def test_get_energy_data_history_keeps_data_when_a_chunk_times_out():
    """A timed out chunk stops the walk without discarding what was collected."""
    from datetime import datetime
    from custom_components.smarthub.api import Aggregation

    api = _make_api()
    calls = []

    async def fake_get_energy_data(location, aggregation, start_datetime, end_datetime):
        calls.append(start_datetime)
        if len(calls) > 1:
            return None  # PENDING never resolved
        return {"USAGE": [{"reading_time": start_datetime, "consumption": 1.0}]}

    api.get_energy_data = fake_get_energy_data

    result = await api.get_energy_data_history(
        location=None, aggregation=Aggregation.HOURLY,
        max_days=365, chunk_days=90, end_datetime=datetime(2026, 9, 10),
    )

    assert len(calls) == 2
    assert len(result["USAGE"]) == 1


async def test_get_energy_data_history_prefers_the_newer_chunk_at_a_boundary():
    """Adjacent chunks share their edge; the newer one holds the whole reading."""
    from datetime import datetime
    from custom_components.smarthub.api import Aggregation

    api = _make_api()
    end = datetime(2026, 9, 10, 0, 0)
    boundary = end - timedelta(days=90)
    seen = []

    async def fake_get_energy_data(location, aggregation, start_datetime, end_datetime):
        seen.append(start_datetime)
        if len(seen) > 2:
            return {}
        # Newer chunk starts on the boundary and reports the full hour; the
        # older chunk ends there and only reaches part way into it.
        consumption = 4.0 if start_datetime == boundary else 1.0
        return {"USAGE": [{"reading_time": boundary, "consumption": consumption}]}

    api.get_energy_data = fake_get_energy_data

    result = await api.get_energy_data_history(
        location=None, aggregation=Aggregation.HOURLY,
        max_days=200, chunk_days=90, end_datetime=end,
    )

    assert len(result["USAGE"]) == 1
    assert result["USAGE"][0]["consumption"] == 4.0


async def test_get_energy_data_history_raises_when_the_first_chunk_fails():
    """A failure with nothing collected is an error, not an empty history."""
    from datetime import datetime
    from custom_components.smarthub.api import Aggregation
    from custom_components.smarthub.exceptions import SmartHubConnectionError

    api = _make_api()

    async def fake_get_energy_data(location, aggregation, start_datetime, end_datetime):
        raise SmartHubConnectionError("boom")

    api.get_energy_data = fake_get_energy_data

    with pytest.raises(SmartHubConnectionError):
        await api.get_energy_data_history(
            location=None, aggregation=Aggregation.HOURLY,
            max_days=365, chunk_days=90, end_datetime=datetime(2026, 9, 10),
        )
