import time

import requests
from django.core.management.base import BaseCommand

from shows.models import Episode, Network, Show

API_URL = "https://api.tvmaze.com"


def get_json(url):
    """Download JSON from TVMaze. Wait and retry if we are going too fast (HTTP 429)."""
    for attempt in range(5):
        response = requests.get(url, timeout=30)
        if response.status_code == 429:
            time.sleep(5)
            continue
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json()
    raise Exception("TVMaze kept answering 429 (too many requests): " + url)


def save_network(network_data):
    """Save one network. Returns None when the show has no network."""
    if network_data is None:
        return None

    country = ""
    if network_data.get("country"):
        country = network_data["country"].get("name") or ""

    # update_or_create uses tvmaze_id to find an existing row, so re-running never duplicates.
    network, created = Network.objects.update_or_create(
        tvmaze_id=network_data["id"],
        defaults={"name": network_data["name"], "country": country},
    )
    return network


def save_show(show_data):
    network = save_network(show_data.get("network"))

    show, created = Show.objects.update_or_create(
        tvmaze_id=show_data["id"],
        defaults={
            "name": show_data["name"],
            "show_type": show_data.get("type") or "",
            "language": show_data.get("language") or "",
            "status": show_data.get("status") or "",
            "official_site": show_data.get("officialSite") or "",
            "network": network,
        },
    )
    return show


def save_episodes(show):
    episodes = get_json(API_URL + "/shows/" + str(show.tvmaze_id) + "/episodes")
    for episode_data in episodes:
        # TVMaze gives airdate as "2013-06-24", or an empty string when unknown.
        airdate = episode_data.get("airdate")
        if not airdate:
            airdate = None

        Episode.objects.update_or_create(
            tvmaze_id=episode_data["id"],
            defaults={
                "name": episode_data.get("name") or "",
                "season": episode_data.get("season") or 0,
                "number": episode_data.get("number"),
                "airdate": airdate,
                "show": show,
            },
        )
    return len(episodes)


class Command(BaseCommand):
    help = "Download shows and episodes from TVMaze into PostgreSQL"

    def add_arguments(self, parser):
        # Each TVMaze page has up to 250 shows. 2 pages = about 480 shows.
        parser.add_argument("--pages", type=int, default=2, help="How many pages of shows to download")

    def handle(self, *args, **options):
        total_shows = 0
        total_episodes = 0

        for page in range(options["pages"]):
            self.stdout.write("Downloading show page " + str(page) + " ...")
            shows_data = get_json(API_URL + "/shows?page=" + str(page))

            for show_data in shows_data:
                show = save_show(show_data)
                total_episodes += save_episodes(show)
                total_shows += 1
                # Stay well below TVMaze's limit of 20 requests per 10 seconds.
                time.sleep(0.6)

            self.stdout.write("  shows imported so far: " + str(total_shows))

        self.stdout.write(self.style.SUCCESS("Done."))
        self.stdout.write("Shows in database:    " + str(Show.objects.count()))
        self.stdout.write("Networks in database: " + str(Network.objects.count()))
        self.stdout.write("Episodes in database: " + str(Episode.objects.count()))
