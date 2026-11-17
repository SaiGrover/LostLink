"""When a report needs a moderator before it is published.

A report goes live straight away unless it may expose personal details:

* the reporter ticked "This report shows personal details" (an ID or bank
  card, a name, number, phone number or QR code in the photo);
* the automatic check suggests it (``media.suggests_sensitive``: an ID-card
  category or words such as "student ID", "Aadhaar", "debit card"), or a
  photo was already marked sensitive automatically or by a moderator;
* the institution switched automatic publishing off
  (``LOSTLINK_AUTO_PUBLISH=false``), so every report is reviewed.

Those reports wait in the moderation queue with the reasons recorded on the
report (``Item.review_reasons``); everything else is approved by the system,
recorded in the audit trail as ``report_auto_published`` and matched
immediately. A report a moderator rejected always goes back to a moderator
after it is edited. Published reports stay open to community flags, which
queue them for a moderator.

Public text cannot carry identifiers at all: the report form refuses phone,
ID and serial numbers and email addresses in the title and description.
"""
from django.conf import settings

from . import lifecycle, media
from .models import Item

REASON_DECLARED = 'The reporter said it shows personal details.'
REASON_DETECTED = 'It looks like an ID card, bank card or personal document.'
REASON_MARKED = 'Its photo is marked as showing personal details.'
REASON_REJECTED = 'A moderator rejected it before, so it is checked again.'
REASON_ALWAYS = 'Every report is reviewed on this site.'


def auto_publish_enabled():
    return getattr(settings, 'LOSTLINK_AUTO_PUBLISH', True)


def review_reasons(item, *, previous_status=None):
    """Why ``item`` needs a moderator before publication; empty if it doesn't."""
    reasons = []
    source = item.photo_sensitivity_source
    if item.photo_sensitive and source == Item.PhotoSensitivitySource.REPORTER:
        reasons.append(REASON_DECLARED)
    if media.suggests_sensitive(item.title, item.description, item.category) or (
            item.photo_sensitive and source == Item.PhotoSensitivitySource.AUTO):
        reasons.append(REASON_DETECTED)
    if item.photo_sensitive and source not in (Item.PhotoSensitivitySource.REPORTER,
                                               Item.PhotoSensitivitySource.AUTO):
        reasons.append(REASON_MARKED)
    if previous_status == Item.Status.REJECTED:
        reasons.append(REASON_REJECTED)
    if not auto_publish_enabled():
        reasons.append(REASON_ALWAYS)
    return reasons


def _store_reasons(item, reasons):
    if item.review_reasons != reasons:
        item.review_reasons = reasons
        item.save(update_fields=['review_reasons', 'updated_at'])


def _publish(item, *, note, metadata):
    from dashboard.models import ModerationDecision
    lifecycle.transition(item, Item.Status.APPROVED, actor=None,
                         action=ModerationDecision.Action.REPORT_AUTO_PUBLISHED,
                         note=note, metadata=metadata, rematch=False)


def submit_new(item):
    """Decide a freshly saved (pending) report. Returns ``True`` if it was
    published. The caller runs matching after the features are prepared."""
    reasons = review_reasons(item)
    _store_reasons(item, reasons)
    if reasons:
        return False
    _publish(item, note='Published automatically: no personal details reported or detected.',
             metadata={'checks': ['reporter_declaration', 'sensitive_content']})
    return True


def resubmit_edited(item, *, previous_status, actor):
    """Decide a report its reporter just edited (row locked by the caller).

    Returns ``True`` if it is (still) published. Matches are always cleared
    by the caller, since the content changed."""
    from dashboard.models import ModerationDecision, record_decision

    reasons = review_reasons(item, previous_status=previous_status)
    _store_reasons(item, reasons)
    if reasons:
        lifecycle.transition(item, Item.Status.PENDING, actor=actor,
                             action=ModerationDecision.Action.REPORT_RESUBMITTED,
                             note='Edited by the reporter; sent to a moderator.',
                             metadata={'reasons': reasons})
        return False
    if item.status == Item.Status.PENDING:
        _publish(item, note='Edited and published automatically: no personal details '
                            'reported or detected.',
                 metadata={'checks': ['reporter_declaration', 'sensitive_content']})
    else:
        record_decision(actor, ModerationDecision.Action.REPORT_AUTO_PUBLISHED, item=item,
                        note='Edited by the reporter; stays published (no personal details '
                             'reported or detected).',
                        from_state=item.status, to_state=item.status,
                        metadata={'checks': ['reporter_declaration', 'sensitive_content']})
    return True
