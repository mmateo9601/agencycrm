from rest_framework import serializers

from apps.companies.models import Company


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = (
            "id",
            "workspace",
            "name",
            "legal_name",
            "primary_sector",
            "website",
            "phone",
            "city",
            "province",
            "country_code",
            "is_active",
            "do_not_contact",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
