from django.contrib import admin

from .models import Claim, VerificationQuestion


class QuestionInline(admin.TabularInline):
    model = VerificationQuestion
    extra = 0


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'claimant', 'status', 'reviewed_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('item__title', 'claimant__username')
    inlines = [QuestionInline]
