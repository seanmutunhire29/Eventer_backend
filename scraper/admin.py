from django.contrib import admin

from .models import ProcessedEmail


@admin.register(ProcessedEmail)
class ProcessedEmailAdmin(admin.ModelAdmin):
    list_display = ("subject", "scrape_source", "events_extracted", "received_at", "processed_at")
    list_filter = ("scrape_source",)
    search_fields = ("subject", "message_id")
