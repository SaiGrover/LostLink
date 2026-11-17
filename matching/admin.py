from django.contrib import admin

from .models import ItemFeatures, Match


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'lost_item', 'found_item', 'score', 'tier', 'evidence_coverage',
                    'status', 'retired_reason', 'engine_version', 'updated_at')
    list_filter = ('status', 'tier', 'retired_reason', 'engine_version')
    readonly_fields = ('created_at', 'updated_at', 'retired_at', 'notified_at')

    def get_queryset(self, request):
        # Administrators see history too, not only active matches.
        return Match.all_objects.select_related('lost_item', 'found_item')


@admin.register(ItemFeatures)
class ItemFeaturesAdmin(admin.ModelAdmin):
    list_display = ('item', 'text_state', 'text_model', 'image_state', 'image_model', 'attempts',
                    'last_attempt_at')
    list_filter = ('text_state', 'image_state', 'image_model')
    readonly_fields = [f.name for f in ItemFeatures._meta.fields]
