from django.core.paginator import Paginator
from django.db import connection
from django.shortcuts import render
from django.test.utils import CaptureQueriesContext

from . import results_data
from .models import Episode, Network, Show

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


def explainer_view(request):
    rel = request.GET.get("rel", "network")
    if rel not in ("network", "episodes"):
        rel = "network"
    mode = request.GET.get("mode", "naive")
    if mode not in ("naive", "opt"):
        mode = "naive"

    _, by_code = results_data.read_benchmark_rows()
    show_count = Show.objects.count()
    with_network_count = Show.objects.filter(network__isnull=False).count()
    episode_count = Episode.objects.count()

    explainer_data = {
        "showCount": show_count,
        "withNetworkCount": with_network_count,
        "episodeCount": episode_count,
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
