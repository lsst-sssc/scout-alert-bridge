from django.contrib import admin
from tom_jpl.models import ScoutDetail, ScoutDetailHistory

from .models import PublishedEvent


@admin.register(PublishedEvent)
class PublishedEventAdmin(admin.ModelAdmin):
    list_display = ('tdes', 'event_type', 'last_run', 'created', 'published_at')
    list_filter = ('event_type',)
    search_fields = ('tdes',)


for model in (ScoutDetail, ScoutDetailHistory):
    if not admin.site.is_registered(model):
        admin.site.register(model)
