from django.contrib import admin

from .models import Claim, ClaimDispute, ClaimEvidence, ClaimRevision, HandoverSession, VerificationQuestion


class QuestionInline(admin.TabularInline):
    model = VerificationQuestion
    extra = 0
    readonly_fields = ('asked_by', 'text', 'expected_answer', 'answer', 'answered_at')
    can_delete = False


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    """Inspection only. Status, reservation and handover change exclusively
    through the audited claim workflow, never by editing rows here."""
    list_display = ('id', 'item', 'claimant', 'status', 'reviewed_by', 'handover_method', 'created_at')
    list_filter = ('status', 'handover_method')
    search_fields = ('item__title', 'claimant__username')
    inlines = [QuestionInline]
    readonly_fields = ('item', 'claimant', 'match', 'status', 'reviewed_by', 'decision_note',
                       'appeal_count', 'handover_method', 'returned_at')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ClaimDispute)
class ClaimDisputeAdmin(admin.ModelAdmin):
    list_display = ('id', 'claim', 'status', 'outcome', 'raised_by', 'resolved_by', 'created_at')
    list_filter = ('status', 'outcome')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HandoverSession)
class HandoverSessionAdmin(admin.ModelAdmin):
    """Shows code status and attempts; the code itself is never stored."""
    list_display = ('id', 'claim', 'status', 'generation_number', 'failed_attempts', 'created_at',
                    'expires_at', 'verified_at')
    list_filter = ('status',)
    exclude = ('otp_hash', 'salt')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (ClaimRevision, ClaimEvidence):
    admin.site.register(model, type(f'{model.__name__}Admin', (admin.ModelAdmin,), {
        'has_add_permission': lambda self, request: False,
        'has_change_permission': lambda self, request, obj=None: False,
        'has_delete_permission': lambda self, request, obj=None: False,
    }))
