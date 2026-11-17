from django.contrib import admin

from .models import ModerationDecision


@admin.register(ModerationDecision)
class ModerationDecisionAdmin(admin.ModelAdmin):
    """Read-only view of the audit trail: history cannot be edited or deleted."""
    list_display = ('created_at', 'action', 'actor', 'item', 'claim', 'from_state', 'to_state')
    list_filter = ('action',)
    search_fields = ('note',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
