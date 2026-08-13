from django.contrib import admin

from .models import AnalysisRecord


@admin.register(AnalysisRecord)
class AnalysisRecordAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "lab_type", "status", "created_at")
    list_filter = ("status", "lab_type")
    search_fields = ("public_id", "user__username")
    readonly_fields = ("public_id", "created_at", "updated_at")
    ordering = ("-created_at",)
