from django.contrib import admin

from .models import Match


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'lost_item', 'found_item', 'score', 'tier',
                    'engine_version', 'created_at')
    list_filter = ('tier', 'engine_version')
    readonly_fields = ('created_at',)
