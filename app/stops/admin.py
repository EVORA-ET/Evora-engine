from django.contrib import admin

from stops.models import BulkUpload, Stop


@admin.register(Stop)
class StopAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'organization', 'stop_type', 'parking_capacity',
        'created_at', 'updated_at',
    ]
    list_filter = ['organization', 'stop_type', 'workshop_available', 'charging_available']
    search_fields = ['name', 'address']


@admin.register(BulkUpload)
class BulkUploadAdmin(admin.ModelAdmin):
    list_display = ['id', 'organization', 'status', 'created_at']
    list_filter = ['status', 'organization']
