import datetime

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


class ExplainerLiveRunTests(TestCase):
    def setUp(self):
        make_test_data()  # 4 shows: 3 with a network, 1 without

    def run_live(self, rel, mode):
        response = self.client.get("/explainer/run/", {"rel": rel, "mode": mode})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_live_run_reports_real_query_counts(self):
        self.assertEqual(self.run_live("network", "naive")["total"], 4)
        self.assertEqual(self.run_live("network", "opt")["total"], 1)
        self.assertEqual(self.run_live("episodes", "naive")["total"], 5)
        self.assertEqual(self.run_live("episodes", "opt")["total"], 2)

    def test_live_run_returns_the_real_sql_of_every_query(self):
        data = self.run_live("network", "naive")
        self.assertEqual(len(data["sql"]), 4)
        self.assertIn('"shows_show"', data["sql"][0])
        self.assertIn('"shows_network"', data["sql"][1])
        self.assertGreater(data["ms"], 0)

    def test_live_run_shortens_long_column_lists(self):
        data = self.run_live("network", "opt")
        self.assertTrue(data["sql"][0].startswith("SELECT ... FROM"))

    def test_live_run_with_bad_parameters_falls_back_to_defaults(self):
        data = self.run_live("nonsense", "nonsense")
        self.assertEqual(data["rel"], "network")
        self.assertEqual(data["mode"], "naive")

    def test_live_run_works_with_an_empty_database(self):
        Show.objects.all().delete()
        self.assertEqual(self.run_live("network", "naive")["total"], 1)


class ExplainerStepTests(TestCase):
    """The Play button calls /explainer/step/ again and again; each call runs
    the next few queries of the real loop."""

    def setUp(self):
        make_test_data()  # 4 shows: 3 with a network, 1 without

    def step(self, rel, mode, start, n):
        response = self.client.get("/explainer/step/", {"rel": rel, "mode": mode, "start": start, "n": n})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def run_all_steps(self, rel, mode, n):
        """Call the step endpoint like the browser does until it says done."""
        sql, results = [], []
        for _ in range(50):
            data = self.step(rel, mode, len(sql), n)
            sql += data["sql"]
            results += [tuple(row) for row in data["results"]]
            if data["done"]:
                return sql, results
        self.fail("step endpoint never said done")

    def test_stepping_gives_same_queries_and_results_as_the_real_function(self):
        functions = {
            ("network", "naive"): (benchmark_queries.network_slow, 4),
            ("network", "opt"): (benchmark_queries.network_fast, 1),
            ("episodes", "naive"): (benchmark_queries.episodes_slow, 5),
            ("episodes", "opt"): (benchmark_queries.episodes_fast, 2),
        }
        for (rel, mode), (function, expected_queries) in functions.items():
            for n in (1, 2, 200):  # one query per call, a few, or all at once
                sql, results = self.run_all_steps(rel, mode, n)
                self.assertEqual(len(sql), expected_queries, (rel, mode, n))
                self.assertEqual(results, function(), (rel, mode, n))

    def test_each_step_runs_only_the_requested_number_of_queries(self):
        data = self.step("network", "naive", 0, 1)
        self.assertEqual(len(data["sql"]), 1)  # just the shows query
        self.assertIn('"shows_show"', data["sql"][0])
        self.assertFalse(data["done"])
        data = self.step("network", "naive", 1, 1)
        self.assertEqual(len(data["sql"]), 1)
        self.assertIn('"shows_network"', data["sql"][0])
        self.assertGreater(data["ms"], 0)

    def test_optimised_version_sends_everything_in_the_first_step(self):
        data = self.step("episodes", "opt", 0, 1)
        self.assertEqual(len(data["sql"]), 2)
        self.assertEqual(len(data["results"]), 4)
        self.assertTrue(data["done"])
        self.assertEqual(self.step("episodes", "opt", 2, 1)["sql"], [])

    def test_bad_parameters_do_not_crash(self):
        response = self.client.get("/explainer/step/", {"rel": "x", "mode": "y", "start": "abc", "n": "-5"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rel"], "network")

    def test_empty_database_finishes_after_one_query(self):
        Show.objects.all().delete()
        data = self.step("network", "naive", 0, 5)
        self.assertEqual(len(data["sql"]), 1)
        self.assertEqual(data["results"], [])
        self.assertTrue(data["done"])


class IndexingLiveTests(TestCase):
    """The Indexing page runs EXPLAIN ANALYZE live, once with the index
    switched off for that request only and once with the planner free to choose."""

    def setUp(self):
        show = Show.objects.create(tvmaze_id=1, name="Big Show", status="Ended")
        start = datetime.date(2010, 1, 1)
        # 3000 episodes, one per day, so a one-month range matches only ~31 rows.
        Episode.objects.bulk_create([
            Episode(tvmaze_id=i, name="Ep", season=1, number=i,
                    airdate=start + datetime.timedelta(days=i), show=show)
            for i in range(3000)
        ])
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE shows_episode")

    def run_plan(self, exp, index):
        response = self.client.get("/indexing/run/", {"exp": exp, "index": index})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_index_off_forces_a_sequential_scan_that_reads_every_row(self):
        data = self.run_plan("airdate", "off")
        self.assertEqual(data["scan_type"], "Seq Scan")
        self.assertFalse(data["index_used"])
        self.assertEqual(data["rows_examined"], 3000)
        self.assertEqual(data["rows_returned"], 31)  # 1 March to 31 March 2015
        self.assertEqual(data["rows_removed"], 3000 - 31)

    def test_index_on_lets_postgres_use_the_index_for_a_selective_filter(self):
        data = self.run_plan("airdate", "on")
        self.assertTrue(data["index_used"], data["scan_type"])
        self.assertIn("episode_airdate_idx", data["index_name"])
        self.assertEqual(data["rows_returned"], 31)
        self.assertLess(data["rows_examined"], 100)

    def test_response_contains_plan_nodes_sql_and_timings(self):
        data = self.run_plan("airdate", "on")
        self.assertGreater(len(data["nodes"]), 0)
        self.assertIn("node_type", data["nodes"][0])
        self.assertIn("shows_episode", data["sql"])
        self.assertGreater(data["execution_ms"], 0)
        self.assertGreater(data["buffers"], 0)

    def test_switching_the_index_off_does_not_leak_into_later_queries(self):
        self.run_plan("airdate", "off")
        with connection.cursor() as cursor:
            cursor.execute("SHOW enable_indexscan")
            self.assertEqual(cursor.fetchone()[0], "on")
            cursor.execute("SHOW enable_bitmapscan")
            self.assertEqual(cursor.fetchone()[0], "on")
            cursor.execute("SHOW max_parallel_workers_per_gather")
            self.assertNotEqual(cursor.fetchone()[0], "0")

    def test_status_experiment_runs_and_bad_parameters_use_defaults(self):
        data = self.run_plan("status", "off")
        self.assertEqual(data["exp"], "status")
        self.assertEqual(data["rows_returned"], 1)
        data = self.run_plan("nonsense", "nonsense")
        self.assertEqual(data["exp"], "airdate")
        self.assertEqual(data["index"], "on")

    def test_page_facts_describe_the_real_tables_and_indexes(self):
        response = self.client.get("/indexing/")
        self.assertEqual(response.status_code, 200)
        facts = response.context["indexing_data"]["experiments"]["airdate"]
        self.assertEqual(facts["tableRows"], 3000)
        self.assertGreater(facts["tablePages"], 0)
        self.assertEqual(facts["indexName"], "episode_airdate_idx")
        self.assertTrue(facts["indexExists"])
        self.assertIn("kB", facts["indexSize"])


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
