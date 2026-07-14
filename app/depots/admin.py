from django.contrib import admin

from depots.models import BulkUpload, Depot


@admin.register(Depot)
class DepotAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'organization', 'parking_capacity',
        'created_at', 'updated_at',
    ]
    list_filter = ['organization', 'workshop_available', 'charging_available']
    search_fields = ['name', 'address']


@admin.register(BulkUpload)
class BulkUploadAdmin(admin.ModelAdmin):
    list_display = ['id', 'organization', 'status', 'created_at']
    list_filter = ['status', 'organization']
