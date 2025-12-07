"""
Utilities for import/export and datetime parsing.
"""

from dateutil import parser as dateparser
from datetime import datetime, timedelta

def parse_issued_at(value):
    """
    Parse a string into a datetime. If value is falsy, return None.
    Accepts common formats produced by the form (YYYY-MM-DDTHH:MM or YYYY-MM-DD HH:MM).
    Returns a naive datetime (server local).
    """
    if not value or (isinstance(value, float) and str(value).lower() == 'nan'):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return dateparser.parse(str(value))
    except Exception:
        return None


def compute_valid_to(valid_from, validity_value, validity_unit):
    """
    Compute valid_to datetime from valid_from (datetime) and validity_value/unit.
    If valid_from is None, uses now.
    validity_unit is 'days' or 'hours' (case-insensitive).
    """
    if valid_from is None:
        valid_from = datetime.now()
    try:
        v = int(validity_value)
    except Exception:
        v = 0
    if (validity_unit or '').lower() == 'hours':
        return valid_from + timedelta(hours=v)
    return valid_from + timedelta(days=v)
