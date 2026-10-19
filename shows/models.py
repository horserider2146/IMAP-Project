from django.db import models


class Network(models.Model):
    # The ID TVMaze gives this network. We use it to avoid duplicates on re-import.
    tvmaze_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name


class Show(models.Model):
    tvmaze_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=300)
    show_type = models.CharField(max_length=50, blank=True)
    language = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50, blank=True)
    official_site = models.URLField(max_length=500, blank=True)

    # ForeignKey: each show points to ONE network, but a network has MANY shows.
    # null=True because some shows have no network in the API.
    # Reverse relationship: network.shows.all()
    network = models.ForeignKey(
        Network,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shows",
    )

    class Meta:
        # Index used in the indexing experiment (added by migration 0002).
        # status has only a few different values, so it is a LOW selectivity field.
        indexes = [models.Index(fields=["status"], name="show_status_idx")]

    def __str__(self):
        return self.name


class Episode(models.Model):
    tvmaze_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=500)
    season = models.IntegerField()
    # Some TVMaze episodes (specials) have no number, so this can be empty.
    number = models.IntegerField(null=True, blank=True)
    # Some episodes have no air date.
    airdate = models.DateField(null=True, blank=True)

    # ForeignKey: each episode belongs to ONE show, a show has MANY episodes.
    # Reverse relationship: show.episodes.all()
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="episodes")

    class Meta:
        # Index used in the indexing experiment (added by migration 0002).
        # airdate has many different values, so a date range matches few rows.
        indexes = [models.Index(fields=["airdate"], name="episode_airdate_idx")]

    def __str__(self):
        return self.name
