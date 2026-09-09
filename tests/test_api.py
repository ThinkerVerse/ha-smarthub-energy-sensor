import pytest
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
