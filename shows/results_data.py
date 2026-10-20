"""Simple functions that read the saved benchmark files and turn them into
numbers the Results page can show. Nothing here hits the TVMaze API or
PostgreSQL directly - it only reads results/benchmark_results.csv and
results/query_plans.txt, the same files benchmark_queries.py writes.
"""

import csv
import os
import re

from django.conf import settings

RESULTS_DIR = os.path.join(settings.BASE_DIR, "results")


def read_benchmark_rows():
    """Returns (list of all rows, dict of rows keyed by their letter code).

    Each row's "experiment" column looks like "A: shows + network (N+1)" or
    "E1: airdate range - no index". The part before the colon is the code.
    """
    path = os.path.join(RESULTS_DIR, "benchmark_results.csv")
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    by_code = {}
    for row in rows:
        code = row["experiment"].split(":")[0].strip()
        by_code[code] = row
    return rows, by_code


def read_query_plans():
    """Returns a dict of raw EXPLAIN ANALYZE text keyed by letter code
    (e.g. "E1"), read from query_plans.txt. Blocks are separated by a
    blank line; the first line of each block is a title like
    "E1 - airdate range, NO index"."""
    path = os.path.join(RESULTS_DIR, "query_plans.txt")
    with open(path, encoding="utf-8") as file:
        text = file.read()

    plans = {}
    for block in text.strip().split("\n\n"):
        title, _, body = block.partition("\n")
        code = title.split(" - ")[0].strip()
        plans[code] = body.strip()
    return plans


def note_value(notes, key):
    """The notes column looks like "plan: Seq Scan | rows returned: 501 |
    index size: 704 kB". This pulls out the value for one key."""
    for part in notes.split("|"):
        part = part.strip()
        if part.startswith(key + ":"):
            return part.split(":", 1)[1].strip()
    return ""


def plan_metrics(plan_text):
    """Pulls the numbers shown in a metric grid out of one EXPLAIN ANALYZE
    block of text. Missing values (e.g. no "Rows Removed" line) become 0."""
    buffers = re.search(r"Buffers: shared hit=(\d+)", plan_text)
    removed = re.search(r"Rows Removed by Filter: (\d+)", plan_text)
    cost = re.search(r"cost=[\d.]+\.\.([\d.]+)", plan_text)
    return {
        "buffers": buffers.group(1) if buffers else "0",
        "rows_removed": removed.group(1) if removed else "0",
        "cost": cost.group(1) if cost else "",
    }


def plan_scan_type(plan_text):
    """Short label for the plan chips, e.g. "Seq Scan" or "Bitmap Index Scan"."""
    first_line = plan_text.split("\n", 1)[0]
    if "Bitmap Heap Scan" in first_line or "Bitmap Index Scan" in plan_text:
        return "Bitmap Index Scan"
    if "Index Only Scan" in first_line:
        return "Index Only Scan"
    if "Index Scan" in first_line:
        return "Index Scan"
    return "Seq Scan"


def ms(seconds, decimals=1):
    """Seconds (as stored in the CSV) to a milliseconds string, e.g. 0.17968 -> "179.7"."""
    return f"{float(seconds) * 1000:.{decimals}f}"


def speed_up_label(slow_seconds, fast_seconds):
    """"21×" style label. Rounds to a whole number once the ratio is 10 or more."""
    slow_seconds = float(slow_seconds)
    fast_seconds = float(fast_seconds)
    if fast_seconds == 0:
        return "–"
    ratio = slow_seconds / fast_seconds
    if ratio >= 10:
        return f"{round(ratio)}×"
    return f"{ratio:.1f}×"


def bar_percent(value, paired_value):
    """Width of a bar as a percentage of the larger of the two values in its pair.
    The template also applies a 3px minimum width so a tiny bar stays visible."""
    value = float(value)
    paired_value = float(paired_value)
    biggest = max(value, paired_value)
    if biggest == 0:
        return 0
    return round(value / biggest * 100, 2)
