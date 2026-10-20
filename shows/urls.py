from django.urls import path

from . import views

app_name = "shows"

urlpatterns = [
    path("", views.results_view, name="results"),
    path("shows/", views.shows_view, name="shows"),
    path("explainer/", views.explainer_view, name="explainer"),
]
