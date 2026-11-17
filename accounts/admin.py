from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class LostLinkUserAdmin(UserAdmin):
    list_display = ('username', 'display_name', 'email', 'role', 'campus_verified', 'is_active')
    list_filter = ('role', 'is_active', 'is_staff', 'email_verification_method')
    fieldsets = UserAdmin.fieldsets + (
        ('LostLink profile', {'fields': ('role', 'enrolment_no', 'phone', 'avatar_color')}),
        ('Campus identity', {'fields': ('email_verified_at', 'email_verification_method')}),
    )
    actions = ['mark_email_verified']

    # Fields that grant privileges. Only superusers may change them here, so
    # a staff account holding the "change user" permission cannot promote
    # itself (or anyone) past what LostLink's own role screen allows.
    PRIVILEGE_FIELDS = ('role', 'is_staff', 'is_superuser', 'groups', 'user_permissions',
                        'email_verified_at', 'email_verification_method')

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly += [f for f in self.PRIVILEGE_FIELDS if f not in readonly]
        return readonly

    @admin.display(boolean=True, description='Campus verified')
    def campus_verified(self, obj):
        return obj.is_campus_verified

    @admin.action(description='Mark email verified (confirmed by an administrator)')
    def mark_email_verified(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(request, 'Only superusers can verify accounts here.', messages.ERROR)
            return
        count = 0
        for user in queryset.filter(email_verified_at__isnull=True):
            user.mark_email_verified(User.VerificationMethod.ADMIN)
            count += 1
        self.message_user(request, f'{count} account(s) marked verified.', messages.SUCCESS)
