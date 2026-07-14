from django.contrib import admin

from drivers.models import BulkUpload, Driver

admin.site.register(Driver)
admin.site.register(BulkUpload)
