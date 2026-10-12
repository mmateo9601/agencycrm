from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from common.models import WorkspaceModel, limited_metadata
from common.normalization import (
    canonicalize_url,
    normalize_company_name,
    normalize_domain,
    normalize_email,
    normalize_phone,
)

from .website_models import WebsiteObservation as WebsiteObservation


class Company(WorkspaceModel):
    name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=250, blank=True, editable=False)
    legal_name = models.CharField(max_length=250, blank=True)
    primary_sector = models.CharField(max_length=150, blank=True, db_index=True)
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    normalized_domain = models.CharField(max_length=253, blank=True, editable=False)
    phone = models.CharField(max_length=40, blank=True)
    phone_e164 = models.CharField(max_length=16, blank=True, editable=False)
    address_line = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=150, blank=True, db_index=True)
    province = models.CharField(max_length=150, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country_code = models.CharField(
        max_length=2, default="ES", validators=[RegexValidator(r"^[A-Z]{2}$")]
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    google_rating = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    google_review_count = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    do_not_contact = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["workspace", field])
            for field in ("normalized_name", "normalized_domain", "phone_e164")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90)
                | models.Q(latitude__isnull=True),
                name="company_latitude_range",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180)
                | models.Q(longitude__isnull=True),
                name="company_longitude_range",
            ),
            models.CheckConstraint(
                condition=models.Q(google_rating__gte=0, google_rating__lte=5)
                | models.Q(google_rating__isnull=True),
                name="company_rating_range",
            ),
        ]

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "El nombre es obligatorio."})
        self.normalized_name = normalize_company_name(self.name)
        self.website = canonicalize_url(self.website)
        self.normalized_domain = normalize_domain(self.website)
        self.phone_e164 = normalize_phone(self.phone, self.country_code)

    def __str__(self):
        return self.name


class CompanyExternalRef(WorkspaceModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="external_refs")
    provider = models.CharField(max_length=50)
    external_id = models.CharField(max_length=255)
    external_url = models.URLField(max_length=500, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True, validators=[limited_metadata])

    class Meta:
        ordering = ["provider", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "provider", "external_id"],
                name="unique_workspace_external_ref",
            )
        ]

    def __str__(self):
        return f"{self.provider}: {self.external_id}"

    def clean(self):
        super().clean()
        self.provider = self.provider.strip().upper()
        self.external_id = self.external_id.strip()
        if not self.provider or not self.external_id:
            raise ValidationError("Proveedor e identificador externo son obligatorios.")
        self.external_url = canonicalize_url(self.external_url)
        limited_metadata(self.metadata)


class Contact(WorkspaceModel):
    class Type(models.TextChoices):
        GENERAL = "GENERAL", "General"
        OWNER = "OWNER", "Propietario"
        MARKETING = "MARKETING", "Marketing"
        SALES = "SALES", "Ventas"
        ADMIN = "ADMIN", "Administración"
        OTHER = "OTHER", "Otro"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="contacts")
    full_name = models.CharField(max_length=200)
    job_title = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    normalized_email = models.CharField(max_length=254, blank=True, editable=False)
    phone = models.CharField(max_length=40, blank=True)
    phone_e164 = models.CharField(max_length=16, blank=True, editable=False)
    whatsapp_e164 = models.CharField(max_length=16, blank=True)
    contact_type = models.CharField(max_length=12, choices=Type.choices, default=Type.GENERAL)
    is_primary = models.BooleanField(default=False)
    is_business_contact = models.BooleanField(default=True)
    source_url = models.URLField(max_length=500, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)
    do_not_contact = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["full_name", "id"]

    def clean(self):
        super().clean()
        self.normalized_email = normalize_email(self.email)
        region = self.company.country_code if self.company_id else "ES"
        self.phone_e164 = normalize_phone(self.phone, region)
        self.whatsapp_e164 = normalize_phone(self.whatsapp_e164, region)

    def __str__(self):
        return self.full_name


class SocialProfile(WorkspaceModel):
    class Platform(models.TextChoices):
        INSTAGRAM = "INSTAGRAM", "Instagram"
        FACEBOOK = "FACEBOOK", "Facebook"
        LINKEDIN = "LINKEDIN", "LinkedIn"
        TIKTOK = "TIKTOK", "TikTok"
        YOUTUBE = "YOUTUBE", "YouTube"
        X = "X", "X"
        OTHER = "OTHER", "Otra"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="social_profiles")
    platform = models.CharField(max_length=12, choices=Platform.choices)
    url = models.URLField(max_length=500)
    username = models.CharField(max_length=200, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True, validators=[limited_metadata])

    class Meta:
        ordering = ["platform", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "platform", "url"], name="unique_company_social_url"
            )
        ]

    def clean(self):
        super().clean()
        self.url = canonicalize_url(self.url)
        limited_metadata(self.metadata)

    def __str__(self):
        return f"{self.get_platform_display()}: {self.username or self.url}"
