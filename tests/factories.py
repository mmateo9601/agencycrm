import factory
from factory.django import DjangoModelFactory

from apps.accounts.models import AgencyProfile, Membership, ServiceOffering, User, Workspace


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user-{n}")
    password = factory.django.Password("Test-password-42!")


class WorkspaceFactory(DjangoModelFactory):
    class Meta:
        model = Workspace

    name = factory.Sequence(lambda n: f"Agency {n}")
    slug = factory.Sequence(lambda n: f"agency-{n}")


class MembershipFactory(DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    workspace = factory.SubFactory(WorkspaceFactory)


class AgencyProfileFactory(DjangoModelFactory):
    class Meta:
        model = AgencyProfile

    workspace = factory.SubFactory(WorkspaceFactory)
    trading_name = "Fictional Agency"


class ServiceOfferingFactory(DjangoModelFactory):
    class Meta:
        model = ServiceOffering

    workspace = factory.SubFactory(WorkspaceFactory)
    code = "SEO_LOCAL"
    name = "SEO local"
