from django.contrib import admin

from apps.accounts.admin import PlatformAdminMixin


class DomainReadOnlyAdmin(PlatformAdminMixin, admin.ModelAdmin):
    """Use the workspace UI for mutations so admin cannot bypass application services."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]
