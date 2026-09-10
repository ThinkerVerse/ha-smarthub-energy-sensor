"""Constants for the SmartHub integration."""

DOMAIN = "smarthub"

# Configuration keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_ACCOUNT_ID = "account_id"
CONF_LOCATION_ID = "location_id"
CONF_HOST = "host"
CONF_POLL_INTERVAL = "poll_interval"
CONF_TIMEZONE = "timezone"
CONF_MFA_TOTP = "mfa_totp"
CONF_HISTORY_DAYS = "history_days"

# Default values
DEFAULT_POLL_INTERVAL = 360  # 6 hour in minutes
MIN_POLL_INTERVAL = 15  # Minimum 15 minutes
MAX_POLL_INTERVAL = 1440  # Maximum 24 hours

# API constants
DEFAULT_TIMEOUT = 120  # seconds - large history chunks take longer to transfer
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds - fallback when the server does not advertise an interval
SESSION_TIMEOUT = 300  # 5 minutes - force session refresh

# Historical import
HISTORICAL_IMPORT_DAYS = 365  # default days of history to import on first run
MIN_HISTORY_DAYS = 1
MAX_HISTORY_DAYS = 3650  # 10 years
# Each chunk is one poll request. SmartHub returns roughly 630 bytes per hourly
# reading, and the response repeats the same series about eight times, so a
# quarter of hourly data is ~1.4MB on the wire and ~11MB once parsed. Keeping
# chunks small keeps peak memory flat on low-powered Home Assistant hosts.
HISTORY_CHUNK_DAYS = 90

# Polling. SmartHub answers a usage request with PENDING and expects the client
# to re-post the identical body until it returns COMPLETE. The server advertises
# its own cadence through the settings endpoint.
SETTING_POLL_INTERVAL = "UsagePollingRequestInterval"
SETTING_POLL_MAX_RUNTIME = "UsagePollingExecutorMaxRuntime"
DEFAULT_POLL_WAIT = 5  # seconds between PENDING retries
MIN_POLL_WAIT = 1
MAX_POLL_WAIT = 60
DEFAULT_POLL_MAX_WAIT = 300  # seconds to keep polling a single request
MIN_POLL_MAX_WAIT = 30
MAX_POLL_MAX_WAIT = 900

# Sensor constants
ENERGY_SENSOR_KEY = "current_energy_usage"
ATTR_LAST_READING_TIME = "last_reading_time"
ATTR_ACCOUNT_ID = "account_id"
ATTR_LOCATION_ID = "location_id"
LOCATION_KEY = "location"
METER_NAME   = "meter_name"

# List of supported services provided by the smarthub endpoint
ELECTRIC_SERVICE = "electric"
SUPPORTED_SERVICES = [ELECTRIC_SERVICE]
FALLBACK_SERVICES = ["ELEC", "1ELEC", "VELEC", "GELEC"]
