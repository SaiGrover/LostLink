"""Shared request payloads for claim tests."""

# An independent moderator's approval records the evidence checklist.
APPROVE = {'note': 'Verified', 'check_statement': 'consistent', 'check_private': 'not_available',
           'check_answers': 'correct', 'check_evidence': 'none_provided'}

# A documented moderator exception (the normal handover uses the owner's code).
EXCEPTION = {'reason': 'desk_collection', 'note': 'Collected at the desk after an ID check'}
