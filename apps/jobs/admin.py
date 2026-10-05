from django.contrib import admin

from common.admin import DomainReadOnlyAdmin

from .models import Job, JobAttempt


@admin.register(Job)
class JobAdmin(DomainReadOnlyAdmin):
    list_display = ["id", "workspace", "kind", "status", "attempts", "available_at"]
    list_filter = ["status", "kind", "workspace"]
    list_select_related = ["workspace"]


@admin.register(JobAttempt)
class JobAttemptAdmin(DomainReadOnlyAdmin):
    list_display = ["job", "attempt_number", "started_at", "finished_at", "succeeded"]
    list_select_related = ["job"]
