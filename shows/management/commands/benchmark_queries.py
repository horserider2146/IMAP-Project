import csv
import datetime
import os
import re
import time

import matplotlib

matplotlib.use("Agg")  # draw graphs into files, no window needed
import matplotlib.pyplot as plt
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.test.utils import CaptureQueriesContext

from shows.models import Episode, Show

RUNS = 10  # how many times each experiment is repeated
RESULTS_FOLDER = os.path.join(settings.BASE_DIR, "results")
CSV_PATH = os.path.join(RESULTS_FOLDER, "benchmark_results.csv")

# The date range used in the index experiment (one month of episodes).
START_DATE = datetime.date(2015, 3, 1)
END_DATE = datetime.date(2015, 4, 1)


# ---------------------------------------------------------------
# The four N+1 experiments. Each function builds a FRESH queryset,
# reads the related data, and returns a simple list of results
# so we can check that all versions give the same answer.
# ---------------------------------------------------------------

def network_slow():
    """Experiment A: 1 query for shows + 1 extra query per show for its network."""
    shows = Show.objects.order_by("id")
    results = []
    for show in shows:
        # show.network runs a NEW query here every time (unless the network is NULL)
        if show.network is None:
            results.append((show.name, "No network"))
        else:
            results.append((show.name, show.network.name))
    return results


def network_fast():
    """Experiment B: select_related joins the network table into the same query."""
    shows = Show.objects.select_related("network").order_by("id")
    results = []
    for show in shows:
        if show.network is None:
            results.append((show.name, "No network"))
        else:
            results.append((show.name, show.network.name))
    return results


def episodes_slow():
    """Experiment C (before): 1 query for shows + 1 query per show for its episodes."""
    shows = Show.objects.order_by("id")
    results = []
    for show in shows:
        episode_count = len(show.episodes.all())
        results.append((show.name, episode_count))
    return results


def episodes_fast():
    """Experiment C (after): prefetch_related gets ALL episodes in one extra query."""
    shows = Show.objects.prefetch_related("episodes").order_by("id")
    results = []
    for show in shows:
        episode_count = len(show.episodes.all())
        results.append((show.name, episode_count))
    return results


def run_n_plus_one_experiment(name, function, dataset_size, notes):
    """Run one experiment RUNS times. Count queries with CaptureQueriesContext."""
    function()  # warm-up run (not measured) so caches are in a similar state

    total_time = 0
    query_counts = []
    last_results = None

    for run in range(RUNS):
        # Django's query log holds at most 9000 queries. Clear it first, otherwise a full
        # log makes the count wrong (it would show 0).
        connection.queries_log.clear()
        # Only the code inside this "with" block is counted and timed.
        with CaptureQueriesContext(connection) as captured:
            start = time.perf_counter()
            last_results = function()
            end = time.perf_counter()
        total_time = total_time + (end - start)
        query_counts.append(len(captured))

    row = {
        "experiment": name,
        "dataset_size": dataset_size,
        "queries": query_counts[0],
        "runs": RUNS,
        "total_seconds": round(total_time, 5),
        "avg_seconds": round(total_time / RUNS, 5),
        "pg_planning_ms": "",
        "pg_execution_ms": "",
        "notes": notes + " | query counts in each run: " + str(sorted(set(query_counts))),
    }
    return row, last_results


# ---------------------------------------------------------------
# Index experiments
# ---------------------------------------------------------------

def episodes_in_march_2015():
    return Episode.objects.filter(airdate__gte=START_DATE, airdate__lt=END_DATE)


def shows_with_status_ended():
    return Show.objects.filter(status="Ended")


def read_plan(query_function):
    """Run EXPLAIN ANALYZE for the query and pull out the useful numbers."""
    # EXPLAIN ANALYZE really runs the query. Its times are measured inside PostgreSQL.
    plan_text = query_function().explain(analyze=True)

    planning_ms = ""
    execution_ms = ""
    planning_match = re.search(r"Planning Time: ([\d.]+) ms", plan_text)
    execution_match = re.search(r"Execution Time: ([\d.]+) ms", plan_text)
    if planning_match:
        planning_ms = planning_match.group(1)
    if execution_match:
        execution_ms = execution_match.group(1)

    if "Index Only Scan" in plan_text:
        scan_type = "Index Only Scan"
    elif "Bitmap Index Scan" in plan_text:
        scan_type = "Bitmap Index Scan"
    elif "Index Scan" in plan_text:
        scan_type = "Index Scan"
    elif "Seq Scan" in plan_text:
        scan_type = "Seq Scan (sequential scan)"
    else:
        scan_type = "unknown"
    return planning_ms, execution_ms, scan_type, plan_text


def run_index_experiment(name, query_function, dataset_size):
    """Time the filter query RUNS times, then read its PostgreSQL plan."""
    list(query_function())  # warm-up run (not measured)

    total_time = 0
    for run in range(RUNS):
        queryset = query_function()  # fresh queryset each time (no Django cache)
        connection.queries_log.clear()  # keep the query log small so counting stays correct
        with CaptureQueriesContext(connection) as captured:
            start = time.perf_counter()
            rows = list(queryset)
            end = time.perf_counter()
        total_time = total_time + (end - start)

    planning_ms, execution_ms, scan_type, plan_text = read_plan(query_function)

    row = {
        "experiment": name,
        "dataset_size": dataset_size,
        "queries": len(captured),
        "runs": RUNS,
        "total_seconds": round(total_time, 5),
        "avg_seconds": round(total_time / RUNS, 5),
        "pg_planning_ms": planning_ms,
        "pg_execution_ms": execution_ms,
        "notes": "plan: " + scan_type + " | rows returned: " + str(len(rows)),
    }
    return row, plan_text


def get_index_size(index_name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_size_pretty(pg_relation_size(%s))", [index_name])
        return cursor.fetchone()[0]


def analyze_tables():
    """ANALYZE updates PostgreSQL's statistics so the planner makes good decisions."""
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE shows_show")
        cursor.execute("ANALYZE shows_episode")


# ---------------------------------------------------------------
# CSV and graphs
# ---------------------------------------------------------------

def save_csv(rows):
    columns = [
        "experiment", "dataset_size", "queries", "runs", "total_seconds",
        "avg_seconds", "pg_planning_ms", "pg_execution_ms", "notes",
    ]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv():
    with open(CSV_PATH, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def draw_bar_chart(labels, values, title, y_label, file_name, colors):
    plt.figure(figsize=(7, 4.5))
    bars = plt.bar(labels, values, color=colors)
    plt.title(title)
    plt.ylabel(y_label)
    plt.xticks(rotation=15, ha="right")
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), str(value),
                 ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(os.path.join(RESULTS_FOLDER, file_name), dpi=120)
    plt.close()


def make_graphs():
    """Draw the graphs using ONLY the numbers saved in the CSV file."""
    rows = read_csv()
    by_name = {}
    for row in rows:
        by_name[row["experiment"]] = row

    n1_names = [
        "A: shows + network (N+1)", "B: select_related('network')",
        "C: shows + episodes (N+1)", "D: prefetch_related('episodes')",
    ]
    n1_colors = ["tab:red", "tab:green", "tab:red", "tab:green"]

    query_counts = []
    average_ms = []
    for name in n1_names:
        query_counts.append(int(by_name[name]["queries"]))
        average_ms.append(round(float(by_name[name]["avg_seconds"]) * 1000, 2))

    draw_bar_chart(n1_names, query_counts, "SQL queries per run (fewer is better)",
                   "Number of SQL queries", "graph_1_query_counts.png", n1_colors)
    draw_bar_chart(n1_names, average_ms, "Average elapsed time per run (lower is better)",
                   "Milliseconds (Python perf_counter)", "graph_2_elapsed_times.png", n1_colors)

    index_names = [
        "E1: airdate range - no index", "E2: airdate range - with index",
        "F1: status='Ended' - no index", "F2: status='Ended' - with index",
    ]
    index_colors = ["tab:red", "tab:green", "tab:red", "tab:green"]
    index_ms = []
    for name in index_names:
        index_ms.append(round(float(by_name[name]["avg_seconds"]) * 1000, 2))

    draw_bar_chart(index_names, index_ms, "Filter query time before and after indexing",
                   "Milliseconds (Python perf_counter)", "graph_3_index_timings.png", index_colors)


class Command(BaseCommand):
    help = "Run all N+1 and indexing experiments, save a CSV and draw graphs"

    def handle(self, *args, **options):
        show_count = Show.objects.count()
        episode_count = Episode.objects.count()
        if show_count == 0:
            self.stdout.write(self.style.ERROR("No data found. Run: python manage.py import_tvmaze"))
            return

        self.stdout.write("Dataset: " + str(show_count) + " shows, " + str(episode_count) + " episodes")
        rows = []

        # ---------- Part 1: N+1 experiments (indexes do not matter here) ----------
        self.stdout.write("Running N+1 experiments ...")
        row_a, results_a = run_n_plus_one_experiment(
            "A: shows + network (N+1)", network_slow, show_count,
            "reads show.network for every show, no select_related")
        row_b, results_b = run_n_plus_one_experiment(
            "B: select_related('network')", network_fast, show_count,
            "one query with a JOIN")
        row_c, results_c = run_n_plus_one_experiment(
            "C: shows + episodes (N+1)", episodes_slow, show_count,
            "reads show.episodes for every show, no prefetch")
        row_d, results_d = run_n_plus_one_experiment(
            "D: prefetch_related('episodes')", episodes_fast, show_count,
            "one query for shows + one query for all their episodes")
        rows = rows + [row_a, row_b, row_c, row_d]

        # The fast versions must return exactly the same answers as the slow versions.
        if results_a != results_b:
            self.stdout.write(self.style.ERROR("ERROR: A and B returned different results!"))
            return
        if results_c != results_d:
            self.stdout.write(self.style.ERROR("ERROR: C and D returned different results!"))
            return
        self.stdout.write(self.style.SUCCESS("Check passed: optimised queries return the same results."))

        # ---------- Part 2: index experiments ----------
        # Step 1: go back to migration 0001 so the indexes do NOT exist.
        self.stdout.write("Removing indexes (migrate shows 0001) ...")
        call_command("migrate", "shows", "0001", verbosity=0)
        analyze_tables()

        row_e1, plan_e1 = run_index_experiment(
            "E1: airdate range - no index", episodes_in_march_2015, episode_count)
        row_f1, plan_f1 = run_index_experiment(
            "F1: status='Ended' - no index", shows_with_status_ended, show_count)

        # Step 2: apply migration 0002 which creates the indexes.
        self.stdout.write("Creating indexes (migrate shows) ...")
        call_command("migrate", "shows", verbosity=0)
        analyze_tables()

        row_e2, plan_e2 = run_index_experiment(
            "E2: airdate range - with index", episodes_in_march_2015, episode_count)
        row_f2, plan_f2 = run_index_experiment(
            "F2: status='Ended' - with index", shows_with_status_ended, show_count)

        row_e2["notes"] = row_e2["notes"] + " | index size: " + get_index_size("episode_airdate_idx")
        row_f2["notes"] = row_f2["notes"] + " | index size: " + get_index_size("show_status_idx")
        rows = rows + [row_e1, row_e2, row_f1, row_f2]

        # ---------- Save everything ----------
        save_csv(rows)
        plan_file = os.path.join(RESULTS_FOLDER, "query_plans.txt")
        with open(plan_file, "w", encoding="utf-8") as file:
            file.write("E1 - airdate range, NO index\n" + plan_e1 + "\n\n")
            file.write("E2 - airdate range, WITH index\n" + plan_e2 + "\n\n")
            file.write("F1 - status='Ended', NO index\n" + plan_f1 + "\n\n")
            file.write("F2 - status='Ended', WITH index\n" + plan_f2 + "\n")

        make_graphs()

        self.stdout.write(self.style.SUCCESS("Saved " + CSV_PATH))
        for row in rows:
            self.stdout.write(row["experiment"] + " | queries=" + str(row["queries"])
                              + " | avg=" + str(row["avg_seconds"]) + " s"
                              + " | pg_exec=" + str(row["pg_execution_ms"]) + " ms | " + row["notes"])
