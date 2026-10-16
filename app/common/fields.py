from rest_framework import serializers


def request_org(serializer_or_field):
    request = serializer_or_field.context.get('request')
    user = getattr(request, 'user', None)
    return getattr(user, 'organization', None)


class OrgScopedPrimaryKeyField(serializers.PrimaryKeyRelatedField):
    """PrimaryKeyRelatedField restricted to the caller's organization.

    Without this, `queryset=Model.objects.all()` lets a user reference another
    tenant's rows by UUID. An id from another organization is reported with the
    same "does not exist" error as an unknown id, so nothing leaks.

    Requires `request` in the serializer context (generic views and the
    explicit `context={'request': request}` calls add it).
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        org = request_org(self)
        if org is None:
            return queryset.none()
        return queryset.filter(organization=org)
