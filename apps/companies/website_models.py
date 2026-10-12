from django.core.exceptions import ValidationError
from django.db import models

from common.models import WorkspaceModel, limited_metadata


class WebsiteObservation(WorkspaceModel):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.PROTECT, related_name="website_observations"
    )
    job = models.ForeignKey(
        "jobs.Job", on_delete=models.PROTECT, related_name="website_observations"
    )
    attempt = models.PositiveSmallIntegerField()
    provider = models.CharField(
        max_length=10, choices=[("mock", "Simulado"), ("web", "Web pública")]
    )
    root_url = models.URLField(max_length=500)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    title = models.CharField(max_length=300, blank=True)
    meta_description = models.CharField(max_length=1000, blank=True)
    has_contact_page = models.BooleanField(default=False)
    has_privacy_page = models.BooleanField(default=False)
    has_booking = models.BooleanField(default=False)
    has_ecommerce = models.BooleanField(default=False)
    detected_emails = models.JSONField(default=list, blank=True)
    detected_phones = models.JSONField(default=list, blank=True)
    detected_social_urls = models.JSONField(default=list, blank=True)
    pages_crawled = models.PositiveSmallIntegerField(default=0)
    crawl_started_at = models.DateTimeField()
    crawl_finished_at = models.DateTimeField()
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.CharField(max_length=250, blank=True)
    findings = models.JSONField(default=dict, blank=True, validators=[limited_metadata])

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["job", "attempt"], name="unique_website_job_attempt"),
            models.CheckConstraint(
                condition=models.Q(attempt__gte=1), name="website_attempt_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(pages_crawled__lte=8), name="website_page_limit"
            ),
            models.CheckConstraint(
                condition=models.Q(provider__in=["mock", "web"]), name="website_provider_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(crawl_finished_at__gte=models.F("crawl_started_at")),
                name="website_dates_ordered",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "company", "-created_at"])]

    def clean(self):
        super().clean()
        from apps.jobs.models import Job

        if not Job.objects.filter(
            pk=self.job_id, workspace_id=self.workspace_id, kind=Job.Kind.ENRICH_COMPANY
        ).exists():
            raise ValidationError({"job": "Trabajo de enriquecimiento ajeno o no válido."})
        limited_metadata(
            {
                "emails": self.detected_emails,
                "phones": self.detected_phones,
                "socials": self.detected_social_urls,
            }
        )

    def __str__(self):
        return f"{self.title or self.root_url} ({self.provider})"
