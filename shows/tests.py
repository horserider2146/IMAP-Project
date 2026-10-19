from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from shows.management.commands import benchmark_queries
from shows.management.commands import import_tvmaze
from shows.models import Episode, Network, Show


def make_test_data():
    """3 shows with a network (2 episodes each) and 1 show with no network."""
    for number in range(3):
        network = Network.objects.create(tvmaze_id=number + 1, name="Network " + str(number))
        show = Show.objects.create(tvmaze_id=number + 1, name="Show " + str(number), network=network)
        Episode.objects.create(tvmaze_id=number * 10 + 1, name="Ep 1", season=1, number=1, show=show)
        Episode.objects.create(tvmaze_id=number * 10 + 2, name="Ep 2", season=1, number=2, show=show)
    Show.objects.create(tvmaze_id=99, name="Show without network")


class RelationshipTests(TestCase):
    def test_network_has_many_shows_and_show_has_many_episodes(self):
        make_test_data()
        network = Network.objects.get(tvmaze_id=1)
        show = Show.objects.get(tvmaze_id=1)
        self.assertEqual(network.shows.count(), 1)  # reverse relationship
        self.assertEqual(show.episodes.count(), 2)  # reverse relationship
        self.assertEqual(show.network, network)

    def test_show_can_have_no_network(self):
        make_test_data()
        show = Show.objects.get(tvmaze_id=99)
        self.assertIsNone(show.network)


class ImportTests(TestCase):
    def test_show_without_network_is_saved(self):
        show_data = {"id": 500, "name": "No Network Show", "type": "Scripted", "network": None}
        show = import_tvmaze.save_show(show_data)
        self.assertIsNone(show.network)
        self.assertEqual(show.language, "")  # missing field becomes an empty string

    def test_importing_twice_does_not_create_duplicates(self):
        show_data = {
            "id": 7, "name": "Some Show", "type": "Scripted", "language": "English",
            "status": "Ended", "officialSite": None,
            "network": {"id": 3, "name": "NBC", "country": {"name": "United States"}},
        }
        import_tvmaze.save_show(show_data)
        import_tvmaze.save_show(show_data)
        self.assertEqual(Show.objects.count(), 1)
        self.assertEqual(Network.objects.count(), 1)

        # Replace the real API call with fake episode data.
        fake_episodes = [
            {"id": 1, "name": "Pilot", "season": 1, "number": 1, "airdate": "2020-01-01"},
            {"id": 2, "name": "Special", "season": 1, "number": None, "airdate": ""},
        ]
        real_get_json = import_tvmaze.get_json
        import_tvmaze.get_json = lambda url: fake_episodes
        try:
            show = Show.objects.get(tvmaze_id=7)
            import_tvmaze.save_episodes(show)
            import_tvmaze.save_episodes(show)
        finally:
            import_tvmaze.get_json = real_get_json
        self.assertEqual(Episode.objects.count(), 2)
        self.assertIsNone(Episode.objects.get(tvmaze_id=2).airdate)


class QueryCountTests(TestCase):
    def setUp(self):
        make_test_data()  # 4 shows: 3 with a network, 1 without

    def count_queries(self, function):
        with CaptureQueriesContext(connection) as captured:
            function()
        return len(captured)

    def test_network_n_plus_one_and_select_related(self):
        # 1 query for the shows + 1 per show that HAS a network (3) = 4
        self.assertEqual(self.count_queries(benchmark_queries.network_slow), 4)
        # select_related uses a JOIN, so only 1 query
        self.assertEqual(self.count_queries(benchmark_queries.network_fast), 1)

    def test_episode_n_plus_one_and_prefetch_related(self):
        # 1 query for shows + 1 per show (4) = 5
        self.assertEqual(self.count_queries(benchmark_queries.episodes_slow), 5)
        # prefetch_related: 1 query for shows + 1 query for all episodes = 2
        self.assertEqual(self.count_queries(benchmark_queries.episodes_fast), 2)

    def test_optimised_queries_return_same_results(self):
        self.assertEqual(benchmark_queries.network_slow(), benchmark_queries.network_fast())
        self.assertEqual(benchmark_queries.episodes_slow(), benchmark_queries.episodes_fast())


class BenchmarkResultTests(TestCase):
    def test_benchmark_row_contains_real_measurements(self):
        make_test_data()
        row, results = benchmark_queries.run_n_plus_one_experiment(
            "test", benchmark_queries.network_slow, 4, "test notes")
        self.assertEqual(row["queries"], 4)
        self.assertEqual(row["runs"], benchmark_queries.RUNS)
        self.assertGreater(row["total_seconds"], 0)
        self.assertGreater(row["avg_seconds"], 0)
        self.assertEqual(len(results), 4)

    def test_explain_analyze_returns_postgres_timing(self):
        # This needs real PostgreSQL because EXPLAIN ANALYZE is PostgreSQL specific.
        make_test_data()
        planning_ms, execution_ms, scan_type, plan_text = benchmark_queries.read_plan(
            benchmark_queries.shows_with_status_ended)
        self.assertNotEqual(execution_ms, "")
        self.assertIn("Execution Time", plan_text)


class FullQueryLogTests(TestCase):
    def test_query_count_is_correct_even_when_django_query_log_is_full(self):
        # Django keeps at most 9000 queries in its log. When the log is full,
        # a naive count returns 0. The benchmark must still count correctly.
        make_test_data()
        with CaptureQueriesContext(connection):
            for number in range(9100):
                Show.objects.filter(tvmaze_id=number).exists()
            row, results = benchmark_queries.run_n_plus_one_experiment(
                "test", benchmark_queries.network_fast, 4, "test notes")
        self.assertEqual(row["queries"], 1)
