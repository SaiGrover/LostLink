from django.contrib import admin

from .models import Category, Item, Location


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('icon', 'name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('adjacent',)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'report_type', 'category', 'location',
                    'status', 'reporter', 'created_at')
    list_filter = ('report_type', 'status', 'category', 'location')
    search_fields = ('title', 'description', 'brand', 'color')
    date_hierarchy = 'created_at'
