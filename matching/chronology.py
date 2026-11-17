"""Graded chronology for the date component (date + optional time).

A found item cannot really be found before it was lost, but students enter
dates and times from memory, so the engine never hard-excludes a pair on
chronology. Each pair gets one of these statuses:

``plausible``      found on or after the lost moment.
``approximate``    found slightly *before* lost, within the tolerance that
                   approximate input explains (dates only: up to
                   ``APPROXIMATE_DAYS`` day(s) earlier; both times known: up to
                   ``TIME_TOLERANCE_HOURS`` earlier, including across
                   midnight). Mild penalty, no warning cap.
``suspicious``     found earlier than tolerance allows but within
                   ``CONTRADICTORY_DAYS`` -- e.g. lost 18:00 and found 09:00 on
                   the same day. Penalised, moderator-visible warning, tier
                   capped (default: at most moderate).
``contradictory``  found more than ``CONTRADICTORY_DAYS`` before lost. Date
                   score 0, warning, tier capped (default: weak / not
                   surfaced). The pair is still stored for moderators.
``unknown``        a date is missing; the component is dropped as missing
                   evidence.

Proximity: ``0.5 ** (calendar_day_gap / HALF_LIFE_DAYS)``; times decide the
ordering status but do not shrink same-day proximity, so adding a time never
lowers a consistent pair. ``score = proximity * PENALTY[status]``.

A missing *time* is independent from a missing *date*: with a date on both
reports but a time on only one (or neither), the comparison falls back to
calendar days and the pair is still fully scored.

All thresholds are configurable heuristics in ``settings.MATCHING['CHRONOLOGY']``.
"""
from __future__ import annotations

from datetime import date, datetime, time

DEFAULTS = {
    'HALF_LIFE_DAYS': 10,
    'APPROXIMATE_DAYS': 1,
    'TIME_TOLERANCE_HOURS': 3,
    'CONTRADICTORY_DAYS': 7,
    'PENALTY': {'plausible': 1.0, 'approximate': 0.9, 'suspicious': 0.5, 'contradictory': 0.0},
    'TIER_CAP': {'suspicious': 'medium', 'contradictory': 'low'},
}

WARNINGS = {
    'approximate': 'Found slightly before it was reported lost; probably an approximate date or time.',
    'suspicious': 'Reported found before it was reported lost (beyond the usual approximation). '
                  'Check the dates and times with both reporters.',
    'contradictory': 'Reported found well before it was reported lost. The dates contradict each '
                     'other; treat this pair with caution.',
}


def config() -> dict:
    from django.conf import settings
    merged = dict(DEFAULTS)
    custom = settings.MATCHING.get('CHRONOLOGY') or {}
    merged.update({k: v for k, v in custom.items() if k not in ('PENALTY', 'TIER_CAP')})
    merged['PENALTY'] = {**DEFAULTS['PENALTY'], **(custom.get('PENALTY') or {})}
    merged['TIER_CAP'] = {**DEFAULTS['TIER_CAP'], **(custom.get('TIER_CAP') or {})}
    if 'HALF_LIFE_DAYS' not in custom:
        merged['HALF_LIFE_DAYS'] = settings.MATCHING.get('DATE_HALF_LIFE_DAYS', DEFAULTS['HALF_LIFE_DAYS'])
    if 'APPROXIMATE_DAYS' not in custom:
        merged['APPROXIMATE_DAYS'] = settings.MATCHING.get('TEMPORAL_TOLERANCE_DAYS',
                                                           DEFAULTS['APPROXIMATE_DAYS'])
    return merged


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return value or None


def _as_time(value):
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, str) and value:
        return time.fromisoformat(value)
    return value or None


def assess(lost_date, found_date, lost_time=None, found_time=None, cfg: dict | None = None) -> dict:
    """Chronology status and date-component score for one lost/found pair."""
    cfg = cfg or config()
    lost_date, found_date = _as_date(lost_date), _as_date(found_date)
    lost_time, found_time = _as_time(lost_time), _as_time(found_time)
    if not lost_date or not found_date:
        return {'status': 'unknown', 'score': None, 'day_gap': None, 'hour_gap': None,
                'time_compared': False, 'warning': ''}
    day_gap = (found_date - lost_date).days
    hour_gap = None
    time_compared = bool(lost_time and found_time)
    contradictory_days = cfg['CONTRADICTORY_DAYS']
    if time_compared:
        delta = datetime.combine(found_date, found_time) - datetime.combine(lost_date, lost_time)
        hour_gap = round(delta.total_seconds() / 3600, 2)
        if hour_gap >= 0:
            status = 'plausible'
        elif hour_gap >= -cfg['TIME_TOLERANCE_HOURS']:
            status = 'approximate'
        elif hour_gap >= -24 * contradictory_days:
            status = 'suspicious'
        else:
            status = 'contradictory'
    else:
        if day_gap >= 0:
            status = 'plausible'
        elif day_gap >= -cfg['APPROXIMATE_DAYS']:
            status = 'approximate'
        elif day_gap >= -contradictory_days:
            status = 'suspicious'
        else:
            status = 'contradictory'
    proximity = 0.5 ** (abs(day_gap) / cfg['HALF_LIFE_DAYS'])
    score = float(proximity * cfg['PENALTY'][status])
    return {'status': status, 'score': round(score, 9), 'day_gap': day_gap, 'hour_gap': hour_gap,
            'time_compared': time_compared, 'warning': WARNINGS.get(status, ''),
            'tier_cap': cfg['TIER_CAP'].get(status)}
