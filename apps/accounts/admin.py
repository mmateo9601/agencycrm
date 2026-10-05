"""Platform administration is reserved to superusers, never workspace staff."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AgencyProfile, Membership, ServiceOffering, User, Workspace


class PlatformAdminMixin:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if self.has_module_permission(request) else queryset.none()


@admin.register(User)
class CustomUserAdmin(PlatformAdminMixin, UserAdmin):
    pass


@admin.register(Workspace)
class WorkspaceAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["name", "slug", "is_active"]
    search_fields = ["name", "slug"]


@admin.register(Membership)
class MembershipAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["user", "workspace", "role", "is_active"]
    list_select_related = ["workspace", "user"]


@admin.register(AgencyProfile)
class AgencyProfileAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["trading_name", "workspace"]
    list_select_related = ["workspace"]


@admin.register(ServiceOffering)
class ServiceOfferingAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["name", "workspace", "code", "is_active"]
    list_select_related = ["workspace"]
