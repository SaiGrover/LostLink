from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class LostLinkUserAdmin(UserAdmin):
    list_display = ('username', 'display_name', 'email', 'role', 'is_active')
    list_filter = ('role', 'is_active', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('LostLink profile', {'fields': ('role', 'enrolment_no', 'phone', 'avatar_color')}),
    )
