# core/time_utils.py
# MARK XXXV - Centralized timezone utilities.
# Provides effective timezone resolution: config override OR system local.
# No external dependencies — uses only Python stdlib (datetime, zoneinfo).

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python < 3.9 fallback (should not happen on 3.11+)
    ZoneInfo = None  # type: ignore


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE_DIR = _get_base_dir()

# Legacy MSK timezone for backward compatibility with old reminders
_MSK = timezone(timedelta(hours=3))


def _load_config_timezone() -> str | None:
    """
    Reads 'timezone' from the unified config (config.loader).
    Returns None if not set.
    """
    try:
        from config.loader import get_setting
        return get_setting("timezone")
    except Exception:
        return None


def _validate_timezone(tz_name: str) -> "ZoneInfo | timezone | None":
    """
    Validates and returns a ZoneInfo object for the given timezone name.
    Returns None if invalid.
    """
    if not tz_name or not ZoneInfo:
        return None
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return None


def get_system_timezone() -> "ZoneInfo | timezone":
    """
    Returns the system's local timezone.
    Falls back to UTC if detection fails.
    """
    try:
        # Get local timezone from a localized datetime
        local_dt = datetime.now().astimezone()
        return local_dt.tzinfo  # type: ignore
    except Exception:
        return timezone.utc


def get_effective_timezone() -> "ZoneInfo | timezone":
    """
    Returns the effective timezone to use:
    1. If 'timezone' is set in ~/.jarvis/settings.json and valid → use it
    2. Otherwise → use system local timezone
    """
    config_tz = _load_config_timezone()
    if config_tz:
        validated = _validate_timezone(config_tz)
        if validated:
            return validated
    return get_system_timezone()


def get_timezone_label(tz: "ZoneInfo | timezone | None" = None) -> str:
    """
    Returns a human-readable label for the timezone.
    Examples: "Europe/Amsterdam", "UTC+03:00", "MSK"
    """
    if tz is None:
        tz = get_effective_timezone()
    
    # If it's a ZoneInfo, return the key (e.g., "Europe/Amsterdam")
    if ZoneInfo and isinstance(tz, ZoneInfo):
        return str(tz.key)
    
    # If it's a fixed offset timezone, format as UTC±HH:MM
    if isinstance(tz, timezone):
        offset = tz.utcoffset(None)
        if offset is not None:
            total_seconds = int(offset.total_seconds())
            hours, remainder = divmod(abs(total_seconds), 3600)
            minutes = remainder // 60
            sign = "+" if total_seconds >= 0 else "-"
            if minutes:
                return f"UTC{sign}{hours:02d}:{minutes:02d}"
            return f"UTC{sign}{hours:02d}:00"
    
    return "Local"


def get_utc_offset_str(tz: "ZoneInfo | timezone | None" = None) -> str:
    """
    Returns the UTC offset as a string like "UTC+02:00".
    """
    if tz is None:
        tz = get_effective_timezone()
    
    now = datetime.now(tz)
    offset = now.utcoffset()
    if offset is None:
        return "UTC+00:00"
    
    total_seconds = int(offset.total_seconds())
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes = remainder // 60
    sign = "+" if total_seconds >= 0 else "-"
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def get_now(tz: "ZoneInfo | timezone | None" = None) -> datetime:
    """
    Returns the current timezone-aware datetime in the effective timezone.
    """
    if tz is None:
        tz = get_effective_timezone()
    return datetime.now(tz)


def get_msk_timezone() -> timezone:
    """
    Returns the Moscow timezone (UTC+3) for legacy reminder compatibility.
    """
    return _MSK


def describe_timezone(tz: "ZoneInfo | timezone | None" = None) -> str:
    """
    Human description of the timezone. Never prints the same thing twice.

    A named zone gives two different facts worth showing: "Europe/Moscow,
    UTC+03:00". A plain offset zone — what Windows hands back when nobody set
    a zone name in settings — gives only one, and the old code printed it
    twice: "(UTC+03:00, UTC+03:00)". That looked like a bug to the owner, and
    a clock that looks broken is not trusted about reminders either.
    """
    if tz is None:
        tz = get_effective_timezone()
    label = get_timezone_label(tz)
    offset = get_utc_offset_str(tz)
    if not label or label == offset:
        return offset
    return f"{label}, {offset}"


def format_time_context() -> str:
    """
    Returns a formatted time context string for the system prompt.
    Includes current date/time and timezone information.
    """
    tz = get_effective_timezone()
    now = get_now(tz)
    
    where = describe_timezone(tz)
    time_str = now.strftime("%A, %d %B %Y — %H:%M")
    
    return (
        f"[CURRENT DATE & TIME — USER LOCAL TIME]\n"
        f"Right now it is: {time_str} ({where})\n"
        f"ALWAYS use the user's effective local time zone for reminders and time calculations.\n"
        f"When the user says a time (e.g. '15:00'), treat it as local time "
        f"unless the user explicitly specifies another time zone.\n\n"
    )


def parse_to_aware_datetime(date_str: str, time_str: str, tz: "ZoneInfo | timezone | None" = None) -> datetime | None:
    """
    Parses date and time strings into a timezone-aware datetime.
    Returns None if parsing fails.
    """
    if tz is None:
        tz = get_effective_timezone()
    
    # Parse date
    parsed_date = None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            parsed_date = datetime.strptime(date_str.strip(), fmt)
            break
        except ValueError:
            continue
    
    if parsed_date is None:
        return None
    
    # Parse time
    parsed_time = None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            parsed_time = datetime.strptime(time_str.strip(), fmt)
            break
        except ValueError:
            continue
    
    if parsed_time is None:
        return None
    
    # Combine date and time
    naive_dt = datetime(
        parsed_date.year, parsed_date.month, parsed_date.day,
        parsed_time.hour, parsed_time.minute, 0
    )
    
    # Make timezone-aware
    if ZoneInfo and isinstance(tz, ZoneInfo):
        return naive_dt.replace(tzinfo=tz)
    else:
        # For fixed offset timezones
        return naive_dt.replace(tzinfo=tz)


def datetime_to_iso(dt: datetime) -> str:
    """
    Converts a datetime to ISO 8601 format with timezone offset.
    Example: "2026-04-10T15:00:00+02:00"
    """
    return dt.isoformat()


def iso_to_datetime(iso_str: str) -> datetime | None:
    """
    Parses an ISO 8601 datetime string back to a datetime object.
    Returns None if parsing fails.
    """
    try:
        return datetime.fromisoformat(iso_str)
    except Exception:
        return None


def legacy_msk_to_aware(msk_str: str) -> datetime | None:
    """
    Converts a legacy MSK datetime string (e.g., "2026-04-10 15:00")
    to a timezone-aware datetime in MSK.
    Used for backward compatibility with old reminders.
    """
    try:
        naive = datetime.strptime(msk_str, "%Y-%m-%d %H:%M")
        return naive.replace(tzinfo=_MSK)
    except Exception:
        return None


def format_datetime_local(dt: datetime, tz: "ZoneInfo | timezone | None" = None) -> str:
    """
    Formats a datetime in the effective local timezone.
    Returns a string like "2026-04-10 15:00".
    """
    if tz is None:
        tz = get_effective_timezone()
    
    # Convert to effective timezone if needed
    if dt.tzinfo is not None:
        local_dt = dt.astimezone(tz)
    else:
        local_dt = dt.replace(tzinfo=tz)
    
    return local_dt.strftime("%Y-%m-%d %H:%M")
