import json
import re
import time

from django.core.paginator import Paginator
from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.test.utils import CaptureQueriesContext

from . import results_data
from .management.commands import benchmark_queries
from .models import Episode, Network, Show

# The four experiment functions the Explainer can run live.
LIVE_FUNCTIONS = {
    ("network", "naive"): benchmark_queries.network_slow,
    ("network", "opt"): benchmark_queries.network_fast,
    ("episodes", "naive"): benchmark_queries.episodes_slow,
    ("episodes", "opt"): benchmark_queries.episodes_fast,
}

SHOWS_PER_PAGE = 50
STATUS_BUTTONS = ["All", "Ended", "Running"]


def header_counts():
    """Shown on the right of the header on every page."""
    return {
        "show_count": Show.objects.count(),
        "network_count": Network.objects.count(),
        "episode_count": Episode.objects.count(),
    }


def build_n_plus_one_card(slow_row, fast_row, slow_code, fast_code, slow_orm, fast_orm, footnote):
    """Builds the template context for one of the two N+1 cards (network or episodes)."""
    return {
        "slow_code": slow_code,
        "fast_code": fast_code,
        "slow_orm": slow_orm,
        "fast_orm": fast_orm,
        "queries_slow": slow_row["queries"],
        "queries_fast": fast_row["queries"],
        "time_slow": results_data.ms(slow_row["avg_seconds"]),
        "time_fast": results_data.ms(fast_row["avg_seconds"]),
        "speed_up": results_data.speed_up_label(slow_row["avg_seconds"], fast_row["avg_seconds"]),
        "query_bar_fast_percent": results_data.bar_percent(fast_row["queries"], slow_row["queries"]),
        "time_bar_fast_percent": results_data.bar_percent(fast_row["avg_seconds"], slow_row["avg_seconds"]),
        "per_query_ms": results_data.ms(float(slow_row["avg_seconds"]) / float(slow_row["queries"]), 2),
        "footnote": footnote,
    }


def build_index_card(slow_row, fast_row, slow_code, fast_code, title, index_name, index_used):
    """Builds the template context for one of the two indexing cards (E or F)."""
    slow_plan_text = results_data.read_query_plans()[slow_code]
    fast_plan_text = results_data.read_query_plans()[fast_code]
    slow_metrics = results_data.plan_metrics(slow_plan_text)
    fast_metrics = results_data.plan_metrics(fast_plan_text)

    rows_matched = results_data.note_value(fast_row["notes"], "rows returned")
    dataset_size = int(fast_row["dataset_size"])
    percent_matched = round(int(rows_matched) / dataset_size * 100)

    return {
        "title": title,
        "index_name": index_name,
        "slow_code": slow_code,
        "fast_code": fast_code,
        "rows_matched": rows_matched,
        "dataset_size": dataset_size,
        "percent_matched": percent_matched,
        "index_used": index_used,
        "scan_type_slow": results_data.plan_scan_type(slow_plan_text),
        "scan_type_fast": results_data.plan_scan_type(fast_plan_text),
        "pg_exec_speed_up": results_data.speed_up_label(slow_row["pg_execution_ms"], fast_row["pg_execution_ms"]),
        "python_speed_up": results_data.speed_up_label(slow_row["avg_seconds"], fast_row["avg_seconds"]),
        "index_size": results_data.note_value(fast_row["notes"], "index size"),
        "buffers_slow": slow_metrics["buffers"],
        "buffers_fast": fast_metrics["buffers"],
        "rows_removed_slow": slow_metrics["rows_removed"],
        "rows_removed_fast": fast_metrics["rows_removed"],
        "cost_slow": slow_metrics["cost"],
        "cost_fast": fast_metrics["cost"],
        "planning_slow": slow_row["pg_planning_ms"],
        "planning_fast": fast_row["pg_planning_ms"],
        "exec_slow": slow_row["pg_execution_ms"],
        "exec_fast": fast_row["pg_execution_ms"],
        "exec_bar_fast_percent": results_data.bar_percent(fast_row["pg_execution_ms"], slow_row["pg_execution_ms"]),
        "python_time_slow": results_data.ms(slow_row["avg_seconds"], 2),
        "python_time_fast": results_data.ms(fast_row["avg_seconds"], 2),
        "python_bar_fast_percent": results_data.bar_percent(fast_row["avg_seconds"], slow_row["avg_seconds"]),
        "plan_text_slow": slow_plan_text,
        "plan_text_fast": fast_plan_text,
    }


# The short "Plan" label shown in the "All measurements" table for the
# four N+1 rows, which are about query strategy, not an EXPLAIN plan.
TABLE_PLAN_LABELS = {
    "A": "–",
    "B": "JOIN",
    "C": "–",
    "D": "2nd query, IN (…)",
}


def results_view(request):
    rows, by_code = results_data.read_benchmark_rows()

    plans = results_data.read_query_plans()
    table_rows = []
    for row in rows:
        code = row["experiment"].split(":")[0].strip()
        plan_label = TABLE_PLAN_LABELS.get(code, "–")
        cost = ""
        if code in plans:
            plan_label = results_data.plan_scan_type(plans[code])
            cost = results_data.plan_metrics(plans[code])["cost"]

        table_rows.append({
            "experiment": row["experiment"],
            "dataset_size": row["dataset_size"],
            "queries": row["queries"],
            "is_n_plus_one": code in ("A", "C"),
            "is_optimised": code in ("B", "D"),
            "avg_ms": results_data.ms(row["avg_seconds"], 2),
            "pg_exec_ms": row["pg_execution_ms"] or "–",
            "planning_ms": row["pg_planning_ms"] or "–",
            "cost": cost,
            "plan_label": plan_label,
        })

    context = {
        "active_tab": "results",
        "network_card": build_n_plus_one_card(
            by_code["A"], by_code["B"], "A", "B",
            'Show.objects.order_by("id")',
            'Show.objects.select_related("network").order_by("id")',
            "29 shows have no network, so Django skips the lookup for them: 1 + 456 = 457 queries.",
        ),
        "episodes_card": build_n_plus_one_card(
            by_code["C"], by_code["D"], "C", "D",
            'Show.objects.order_by("id")',
            'Show.objects.prefetch_related("episodes").order_by("id")',
            "Both versions still build 61,658 Episode objects in Python, which is why the time drops less than the query count.",
        ),
        "index_cards": [
            build_index_card(
                by_code["E1"], by_code["E2"], "E1", "E2",
                "Episodes aired in March 2015", "episode_airdate_idx", index_used=True,
            ),
            build_index_card(
                by_code["F1"], by_code["F2"], "F1", "F2",
                "Shows with status = 'Ended'", "show_status_idx", index_used=False,
            ),
        ],
        "table_rows": table_rows,
    }
    context.update(header_counts())
    return render(request, "shows/results.html", context)


def query_string_without(request, key):
    """The current GET query string with one key removed, so links can add
    their own value for that key without losing the other filters."""
    params = request.GET.copy()
    params.pop(key, None)
    params.pop("page", None)  # changing a filter always goes back to page 1
    return params.urlencode()


def shows_view(request):
    load_mode = request.GET.get("load", "select_related")
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "All")
    network_id = request.GET.get("network", "")
    selected_tvmaze_id = request.GET.get("show", "")

    # The query that actually renders the table. In "naive" mode we leave out
    # select_related on purpose, so reading show.network in the loop below
    # becomes the N+1 problem - one extra query per show that has a network.
    if load_mode == "naive":
        shows = Show.objects.order_by("tvmaze_id")
    else:
        shows = Show.objects.select_related("network").order_by("tvmaze_id")

    if search:
        shows = shows.filter(name__icontains=search)
    if status != "All":
        shows = shows.filter(status=status)
    if network_id:
        shows = shows.filter(network_id=network_id)

    total_matching = shows.count()
    paginator = Paginator(shows, SHOWS_PER_PAGE)
    page_number = request.GET.get("page", "1")
    page = paginator.get_page(page_number)

    with CaptureQueriesContext(connection) as captured:
        rows = []
        for show in page.object_list:
            rows.append({
                "id": show.tvmaze_id,
                "name": show.name,
                "network_name": show.network.name if show.network else "–",
                "country": show.network.country if show.network else "",
                "type": show.show_type,
                "status": show.status,
                "selected": str(show.tvmaze_id) == selected_tvmaze_id,
            })
    query_count = len(captured)

    selected_show = None
    episodes = []
    if selected_tvmaze_id:
        selected_show = Show.objects.select_related("network").filter(
            tvmaze_id=selected_tvmaze_id
        ).first()
        if selected_show:
            episodes = list(
                selected_show.episodes.order_by("season", "number")
            )

    status_options = [
        {
            "label": option,
            "value": option,
            "active": option == status,
            "query_string": query_string_without(request, "status"),
        }
        for option in STATUS_BUTTONS
    ]

    context = {
        "active_tab": "shows",
        "rows": rows,
        "page": page,
        "query_string_no_page": query_string_without(request, "page"),
        "search": search,
        "status": status,
        "status_options": status_options,
        "network_id": network_id,
        "networks": Network.objects.order_by("name"),
        "total_matching": total_matching,
        "load_mode": load_mode,
        "load_query_string": query_string_without(request, "load"),
        "query_count": query_count,
        "query_count_is_good": query_count <= 1,
        "selected_show": selected_show,
        "episodes": episodes,
    }
    context.update(header_counts())
    return render(request, "shows/shows.html", context)


def build_relationship_data(rel, by_code, show_count, with_network_count):
    """All the real, measured facts the Explainer page's JavaScript needs for
    one relationship (network or episodes): the two experiment codes, their
    measured query counts and average times, and the real loop code."""
    if rel == "network":
        naive_row, opt_row = by_code["A"], by_code["B"]
        return {
            "label": "Show → network",
            "bodyLines": [
                "results = []",
                "for show in shows:",
                "    if show.network is None:",
                '        results.append((show.name, "No network"))',
                "    else:",
                "        results.append((show.name, show.network.name))",
            ],
            "footnote": (
                f"{show_count - with_network_count} of {show_count} shows have no network, "
                f"so Django skips the lookup for them: 1 + {with_network_count} = {naive_row['queries']} queries."
            ),
            "naive": {"code": "A", "fn": "network_slow", "queryset": 'Show.objects.order_by("id")',
                      "total": int(naive_row["queries"]), "ms": results_data.ms(naive_row["avg_seconds"])},
            "opt": {"code": "B", "fn": "network_fast",
                    "queryset": 'Show.objects.select_related("network").order_by("id")',
                    "total": int(opt_row["queries"]), "ms": results_data.ms(opt_row["avg_seconds"])},
        }

    naive_row, opt_row = by_code["C"], by_code["D"]
    return {
        "label": "Show → episodes",
        "bodyLines": [
            "results = []",
            "for show in shows:",
            "    episode_count = len(show.episodes.all())",
            "    results.append((show.name, episode_count))",
        ],
        "footnote": "",
        "naive": {"code": "C", "fn": "episodes_slow", "queryset": 'Show.objects.order_by("id")',
                  "total": int(naive_row["queries"]), "ms": results_data.ms(naive_row["avg_seconds"])},
        "opt": {"code": "D", "fn": "episodes_fast",
                "queryset": 'Show.objects.prefetch_related("episodes").order_by("id")',
                "total": int(opt_row["queries"]), "ms": results_data.ms(opt_row["avg_seconds"])},
    }


def read_rel_and_mode(request):
    rel = request.GET.get("rel", "network")
    if rel not in ("network", "episodes"):
        rel = "network"
    mode = request.GET.get("mode", "naive")
    if mode not in ("naive", "opt"):
        mode = "naive"
    return rel, mode


def explainer_run_view(request):
    """Runs the real experiment function once, right now, against the database
    and returns the query count, the time, and the SQL Django really sent."""
    rel, mode = read_rel_and_mode(request)
    function = LIVE_FUNCTIONS[(rel, mode)]

    # Django's query log holds at most 9000 queries, so clear it first.
    connection.queries_log.clear()
    with CaptureQueriesContext(connection) as captured:
        start = time.perf_counter()
        function()
        seconds = time.perf_counter() - start

    # Long column lists are shortened to "..." like the SQL shown in the player.
    sql_list = [re.sub(r"^SELECT .*? FROM", "SELECT ... FROM", q["sql"], count=1)
                for q in captured.captured_queries]

    return JsonResponse({
        "rel": rel,
        "mode": mode,
        "total": len(sql_list),
        "ms": round(seconds * 1000, 3),
        "sql": sql_list,
    })


# ---------------------------------------------------------------
# Indexing page: runs EXPLAIN ANALYZE live
# ---------------------------------------------------------------

INDEX_EXPERIMENTS = {
    "airdate": {
        "title": "Episodes aired in March 2015",
        "filter": "airdate >= 2015-03-01 AND airdate < 2015-04-01",
        "note": "A date range matches only a small slice of the table, so an index should help a lot.",
        "model": Episode,
        "table": "shows_episode",
        "index": "episode_airdate_idx",
        "query": benchmark_queries.episodes_in_march_2015,
    },
    "status": {
        "title": "Shows with status = Ended",
        "filter": "status = 'Ended'",
        "note": "Most shows share the same few status values, so this filter matches a big part of the table. PostgreSQL may decide the index is not worth using.",
        "model": Show,
        "table": "shows_show",
        "index": "show_status_idx",
        "query": benchmark_queries.shows_with_status_ended,
    },
}

PAGE_SIZE_BYTES = 8192  # PostgreSQL reads and caches the table in 8 kB pages


def read_exp_and_index(request):
    exp = request.GET.get("exp", "airdate")
    if exp not in INDEX_EXPERIMENTS:
        exp = "airdate"
    index = request.GET.get("index", "on")
    if index not in ("on", "off"):
        index = "on"
    return exp, index


def get_index_facts(exp):
    """Real facts about one experiment's table and index, read from PostgreSQL."""
    experiment = INDEX_EXPERIMENTS[exp]
    facts = {
        "title": experiment["title"],
        "filter": experiment["filter"],
        "note": experiment["note"],
        "table": experiment["table"],
        "indexName": experiment["index"],
        "tableRows": experiment["model"].objects.count(),
        "tablePages": 0,
        "indexExists": False,
        "indexSize": "",
    }
    if connection.vendor != "postgresql":
        return facts  # the page shows an error message when Run is pressed

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_relation_size(%s)", [experiment["table"]])
        facts["tablePages"] = cursor.fetchone()[0] // PAGE_SIZE_BYTES
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [experiment["index"]])
        facts["indexExists"] = cursor.fetchone()[0]
        if facts["indexExists"]:
            cursor.execute("SELECT pg_size_pretty(pg_relation_size(%s))", [experiment["index"]])
            facts["indexSize"] = cursor.fetchone()[0]
    return facts


def flatten_plan(node, depth, nodes):
    """Turn PostgreSQL's nested plan tree into a flat list, one entry per step."""
    nodes.append({
        "depth": depth,
        "node_type": node["Node Type"],
        "relation": node.get("Relation Name", ""),
        "index_name": node.get("Index Name", ""),
        "condition": node.get("Index Cond") or node.get("Recheck Cond") or node.get("Filter") or "",
        "actual_rows": node.get("Actual Rows", 0),
        "rows_removed": node.get("Rows Removed by Filter", 0) + node.get("Rows Removed by Index Recheck", 0),
        "ms": node.get("Actual Total Time", 0),
        "cost": node.get("Total Cost", 0),
    })
    for child in node.get("Plans", []):
        flatten_plan(child, depth + 1, nodes)


def explain_live(queryset, index_on):
    """Really run EXPLAIN (ANALYZE, BUFFERS) for the queryset. When index_on is
    False the planner is told, for THIS transaction only, not to use any index,
    so it has to read the whole table. The index itself is never dropped."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Parallel workers off so both runs are easy to compare.
            cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            if not index_on:
                cursor.execute("SET LOCAL enable_indexscan = off")
                cursor.execute("SET LOCAL enable_bitmapscan = off")
                cursor.execute("SET LOCAL enable_indexonlyscan = off")
        try:
            plan_text = queryset.explain(analyze=True, buffers=True, format="json")
        finally:
            # SET LOCAL ends with the transaction; RESET also covers nested transactions.
            with connection.cursor() as cursor:
                cursor.execute("RESET max_parallel_workers_per_gather")
                cursor.execute("RESET enable_indexscan")
                cursor.execute("RESET enable_bitmapscan")
                cursor.execute("RESET enable_indexonlyscan")
    return json.loads(plan_text)[0]


def indexing_run_view(request):
    exp, index = read_exp_and_index(request)
    if connection.vendor != "postgresql":
        return JsonResponse({"error": "The Indexing page needs PostgreSQL (EXPLAIN ANALYZE is PostgreSQL specific)."},
                            status=501)

    queryset = INDEX_EXPERIMENTS[exp]["query"]()
    try:
        plan = explain_live(queryset, index == "on")
    except Exception as error:
        return JsonResponse({"error": "EXPLAIN failed: " + str(error)}, status=500)

    nodes = []
    flatten_plan(plan["Plan"], 0, nodes)

    # The "heap node" is the step that reads the table itself.
    heap_node = next((n for n in nodes if n["relation"]), nodes[0])
    index_node = next((n for n in nodes if n["index_name"]), None)
    top = plan["Plan"]

    return JsonResponse({
        "exp": exp,
        "index": index,
        "sql": str(queryset.query),
        "nodes": nodes,
        "scan_type": heap_node["node_type"],
        "index_used": any("Index" in n["node_type"] for n in nodes),
        "index_name": index_node["index_name"] if index_node else "",
        "rows_returned": top.get("Actual Rows", 0),
        "rows_examined": heap_node["actual_rows"] + heap_node["rows_removed"],
        "rows_removed": heap_node["rows_removed"],
        "buffers": top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0),
        "buffers_hit": top.get("Shared Hit Blocks", 0),
        "buffers_read": top.get("Shared Read Blocks", 0),
        "total_cost": top.get("Total Cost", 0),
        "planning_ms": plan.get("Planning Time", 0),
        "execution_ms": plan.get("Execution Time", 0),
    })


def indexing_view(request):
    exp, _ = read_exp_and_index(request)
    indexing_data = {
        "exp": exp,
        "runUrl": reverse("shows:indexing_run"),
        "experiments": {name: get_index_facts(name) for name in INDEX_EXPERIMENTS},
    }
    context = {
        "active_tab": "indexing",
        "exp": exp,
        "experiment_choices": [(name, INDEX_EXPERIMENTS[name]["title"]) for name in INDEX_EXPERIMENTS],
        "lanes": [
            {"key": "off", "title": "Without the index",
             "sub": "Index switched off for this one request, so PostgreSQL has to read the whole table."},
            {"key": "on", "title": "With the index",
             "sub": "PostgreSQL is free to choose. It only uses the index if it thinks that is cheaper."},
        ],
        "indexing_data": indexing_data,
    }
    context.update(header_counts())
    return render(request, "shows/indexing.html", context)


def read_int(request, name, default, smallest, biggest):
    try:
        value = int(request.GET.get(name, default))
    except ValueError:
        value = default
    return max(smallest, min(biggest, value))


def explainer_step_view(request):
    """Used by the Play and Step buttons. Runs the NEXT few queries of the real
    loop (the same code as benchmark_queries.py) and returns their SQL, their
    time and the result rows the loop produced.

    It keeps no state on the server: `start` is how many queries the browser
    has already seen, `n` is how many more to run now."""
    rel, mode = read_rel_and_mode(request)
    start = read_int(request, "start", 0, 0, 100000)
    n = read_int(request, "n", 1, 1, 200)

    connection.queries_log.clear()  # Django's log holds at most 9000 queries
    results = []

    if mode == "opt":
        # select_related / prefetch_related fetch everything when the queryset
        # is evaluated, so the very first step already contains every query.
        if start > 0:
            return JsonResponse({"rel": rel, "mode": mode, "sql": [], "results": [], "ms": 0, "done": True})
        if rel == "network":
            shows_query = Show.objects.select_related("network").order_by("id")
        else:
            shows_query = Show.objects.prefetch_related("episodes").order_by("id")

        with CaptureQueriesContext(connection) as captured:
            timer = time.perf_counter()
            for show in shows_query:
                if rel == "network":
                    results.append((show.name, "No network" if show.network is None else show.network.name))
                else:
                    results.append((show.name, len(show.episodes.all())))
            seconds = time.perf_counter() - timer
        done = True
    else:
        # Naive: query 1 loads the shows, then every show after that sends
        # its own query (network: only shows that HAVE a network).
        base_query = Show.objects.order_by("id")
        shows = None
        if start > 0:
            # Not counted: the shows were already loaded by an earlier step,
            # we only need them again to find where the loop was up to.
            shows = list(base_query)

        with CaptureQueriesContext(connection) as captured:
            timer = time.perf_counter()
            if start == 0:
                shows = list(base_query)  # query 1
                budget = n - 1
            else:
                budget = n

            # Skip the shows whose queries were already sent in earlier steps.
            position = 0
            skipped = 0
            while skipped < start - 1 and position < len(shows):
                if rel == "episodes" or shows[position].network_id is not None:
                    skipped += 1
                position += 1

            sent = 0
            done = True
            for show in shows[position:]:
                sends_query = rel == "episodes" or show.network_id is not None
                if sends_query and sent == budget:
                    done = False
                    break
                if sends_query:
                    sent += 1
                if rel == "network":
                    results.append((show.name, "No network" if show.network is None else show.network.name))
                else:
                    results.append((show.name, len(show.episodes.all())))
            seconds = time.perf_counter() - timer

    # Long column lists are shortened to "..." like the rest of the player.
    sql_list = [re.sub(r"^SELECT .*? FROM", "SELECT ... FROM", q["sql"], count=1)
                for q in captured.captured_queries]

    return JsonResponse({
        "rel": rel,
        "mode": mode,
        "sql": sql_list,
        "results": results,
        "ms": round(seconds * 1000, 3),
        "done": done,
    })


def explainer_view(request):
    rel, mode = read_rel_and_mode(request)

    _, by_code = results_data.read_benchmark_rows()
    show_count = Show.objects.count()
    with_network_count = Show.objects.filter(network__isnull=False).count()
    episode_count = Episode.objects.count()

    explainer_data = {
        "showCount": show_count,
        "withNetworkCount": with_network_count,
        "episodeCount": episode_count,
        "stepUrl": reverse("shows:explainer_step"),
        "rel": rel,
        "mode": mode,
        "relationships": {
            "network": build_relationship_data("network", by_code, show_count, with_network_count),
            "episodes": build_relationship_data("episodes", by_code, show_count, with_network_count),
        },
    }

    context = {
        "active_tab": "explainer",
        "rel": rel,
        "mode": mode,
        "explainer_data": explainer_data,
    }
    context.update(header_counts())
    return render(request, "shows/explainer.html", context)
