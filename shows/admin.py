from django.contrib import admin

from .models import Episode, Network, Show

admin.site.register(Network)
admin.site.register(Show)
admin.site.register(Episode)
