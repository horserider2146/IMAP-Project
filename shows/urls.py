from django.urls import path

from . import views

app_name = "shows"

urlpatterns = [
    path("", views.results_view, name="results"),
    path("shows/", views.shows_view, name="shows"),
    path("explainer/", views.explainer_view, name="explainer"),
    path("explainer/run/", views.explainer_run_view, name="explainer_run"),
    path("explainer/step/", views.explainer_step_view, name="explainer_step"),
    path("indexing/", views.indexing_view, name="indexing"),
    path("indexing/run/", views.indexing_run_view, name="indexing_run"),
]
