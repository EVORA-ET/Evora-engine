from django.contrib import admin, messages
from django.utils import timezone

from ev_migration.models import EVModel


@admin.register(EVModel)
class EVModelAdmin(admin.ModelAdmin):
    """EVORA staff maintain the shared catalogue here; customers cannot."""

    list_display = ('manufacturer', 'model', 'variant', 'vehicle_type',
                    'verification_status', 'active', 'updated_at')
    list_filter = ('vehicle_type', 'verification_status', 'active')
    search_fields = ('manufacturer', 'model', 'variant')
    readonly_fields = ('verification_status', 'verified_by_name',
                       'verified_at', 'verification_note', 'created_at',
                       'updated_at')
    actions = ['mark_verified']

    def save_model(self, request, obj, form, change):
        if change and form.changed_data:
            # A verification only covers the figures that were checked.
            obj.verification_status = 'unverified'
            obj.verified_by_name = ''
            obj.verified_at = None
            obj.verification_note = ''
        super().save_model(request, obj, form, change)

    @admin.action(description='Mark verified against the cited document')
    def mark_verified(self, request, queryset):
        n = queryset.update(
            verification_status='verified',
            verified_by_name=request.user.get_username(),
            verified_at=timezone.now(), updated_at=timezone.now(),
            verification_note='Checked against the cited document in admin.')
        self.message_user(request, f'{n} model(s) marked verified.',
                          messages.SUCCESS)
