"""
Custom integration to integrate SmartHub Coop energy sensors with Home Assistant.

For more details about this integration, please refer to
https://github.com/gagata/ha-smarthub-energy-sensor
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv

from .api import SmartHubAPI
from .sensor import  SmartHubDataUpdateCoordinator
from .const import (
    CONF_HISTORY_DAYS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    HISTORICAL_IMPORT_DAYS,
    MAX_HISTORY_DAYS,
    MIN_HISTORY_DAYS,
)

from datetime import timedelta

# Remove explicit config flow import
# from . import config_flow  # noqa: F401

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_IMPORT_HISTORY = "import_history"
ATTR_DAYS = "days"

IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_HISTORY_DAYS, max=MAX_HISTORY_DAYS)
        ),
        vol.Optional("entry_id"): cv.string,
    }
)


def _register_services(hass: HomeAssistant) -> None:
    """Register the integration level services once."""
    if hass.services.has_service(DOMAIN, SERVICE_IMPORT_HISTORY):
        return

    async def _async_import_history(call: ServiceCall) -> None:
        """Re-import historical statistics for one or all configured accounts."""
        entry_id = call.data.get("entry_id")
        entries = [
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if entry_id is None or entry.entry_id == entry_id
        ]

        if not entries:
            _LOGGER.warning("No loaded SmartHub config entries to import history for")
            return

        for entry in entries:
            coordinator = entry.runtime_data
            days = call.data.get(
                ATTR_DAYS, entry.data.get(CONF_HISTORY_DAYS, HISTORICAL_IMPORT_DAYS)
            )
            await coordinator.async_import_history(days)

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _async_import_history,
        schema=IMPORT_HISTORY_SCHEMA,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SmartHub from a config entry."""
    config = entry.data

    # Validate required configuration
    required_fields = ["email", "password", "account_id", "host"]
    missing_fields = [field for field in required_fields if not config.get(field)]

    if missing_fields:
        _LOGGER.error("Missing required configuration fields: %s", missing_fields)
        raise ConfigEntryError(f"Missing configuration fields: {missing_fields}")

    # Initialize the API object
    api = SmartHubAPI(
        email=config["email"],
        password=config["password"],
        account_id=config["account_id"],
        timezone=config.get("timezone", "GMT"), # timezone was not previously required - default it to be GMT
        mfa_totp=config.get("mfa_totp", ""), # mfa_totp is optional
        host=config["host"],
    )

    # Test the connection
    try:
        await api.get_token()
        _LOGGER.info("Successfully connected to SmartHub API")
    except Exception as e:
        _LOGGER.error("Failed to connect to SmartHub API: %s", e)
        await api.close()
        raise ConfigEntryError(f"Cannot connect to SmartHub: {e}") from e

    # Create update coordinator, and store in the config entry
    coordinator = SmartHubDataUpdateCoordinator(
        hass=hass,
        api=api,
        update_interval=timedelta(minutes=config.get("poll_interval", DEFAULT_POLL_INTERVAL)),
        config_entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    # A deep first-time import can run for many minutes, so it happens after
    # setup returns rather than blocking Home Assistant's startup.
    if coordinator.history_backfill_pending:
        history_days = config.get(CONF_HISTORY_DAYS, HISTORICAL_IMPORT_DAYS)
        _LOGGER.info(
            "Continuing historical import (%d days) in the background", history_days
        )
        entry.async_create_background_task(
            hass,
            coordinator.async_import_history(history_days),
            name=f"{DOMAIN}_history_import_{entry.entry_id}",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up API connection
        data = hass.data.get(DOMAIN,{}).get(entry.entry_id)

        if hasattr(entry, "runtime_data") and hasattr(entry.runtime_data, "api"):
            api= entry.runtime_data.api

        if data:
            if isinstance(data, dict) and "api" in data:
                api = data["api"]
            else:
                api = data  # Direct API reference

        if api:
            await api.close()

        # Remove data
        hass.data.get(DOMAIN,{}).pop(entry.entry_id, None)

        # Remove domain data if no entries left
        if DOMAIN in hass.data:
            hass.data.pop(DOMAIN, None)

    return unload_ok

