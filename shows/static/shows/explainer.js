// Plain DOM JavaScript, no framework. Drives the step/play/reset buttons for
// the CURRENT relationship + mode (switching those reloads the page, see the
// links in explainer.html).
//
// Step and Play really run the queries: each press calls /explainer/step/,
// Django runs the next few queries of the loop against the database and sends
// back their SQL, their time and the result rows. Nothing here is replayed.
// The numbers in the "explainer-data" JSON tag are only the saved benchmark
// (used for the progress bar before the run finishes and for the other version).

const data = JSON.parse(document.getElementById("explainer-data").textContent);
const rel = data.relationships[data.rel];
const current = rel[data.mode];
const other = rel[data.mode === "naive" ? "opt" : "naive"];
const PLAY_SPEED = 5; // matches the design's default speed (range was 1-10)
const RESULT_ROWS_SHOWN = 12;

// What the database has sent back so far.
let sqlList = [];      // real SQL, one string per query
let resultRows = [];   // rows the loop has produced: [show name, network name or episode count]
let serverMs = 0;      // time Django spent running the queries (summed over all steps)
let done = false;      // true once the loop has finished
let busy = false;      // true while a request is in flight
let runId = 0;         // bumped by Reset so a late response is thrown away
let playing = false;
let errorText = "";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function noteForStep(i) {
  if (i === 1) {
    if (data.rel === "network" && data.mode === "opt") {
      return { note: "Shows and their networks in one JOIN", kind: "base" };
    }
    return { note: "Loads all " + data.showCount + " shows", kind: "base" };
  }
  if (data.mode === "opt") {
    return {
      note: "prefetch_related: every episode for all " + data.showCount + " shows, attached in Python",
      kind: "good",
    };
  }
  if (data.rel === "network") {
    return {
      note: "show.network, lazy load for show " + (i - 1) + " of " + data.withNetworkCount + " with a network",
      kind: "bad",
    };
  }
  return {
    note: "show.episodes.all() for show " + (i - 1) + " of " + data.showCount,
    kind: "bad",
  };
}

// Asks Django to run the next `n` queries and adds what comes back.
async function runNextQueries(n) {
  if (busy || done) return;
  busy = true;
  errorText = "";
  const myRun = runId;
  render();
  try {
    const url = data.stepUrl + "?rel=" + data.rel + "&mode=" + data.mode + "&start=" + sqlList.length + "&n=" + n;
    const response = await fetch(url);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const step = await response.json();
    if (myRun !== runId) return; // Reset was pressed while waiting
    sqlList = sqlList.concat(step.sql);
    resultRows = resultRows.concat(step.results);
    serverMs += step.ms;
    done = step.done;
  } catch (error) {
    playing = false;
    errorText = "Could not run the queries (" + error.message + "). Is the Django server running?";
  } finally {
    if (myRun === runId) busy = false;
    render();
  }
}

function lineNoteFor(count) {
  if (count === 0) return "Press Step or Play to run the queries on the database.";
  if (count === 1) {
    if (data.mode === "opt" && data.rel === "network") {
      return "The for loop evaluates the queryset. select_related adds a JOIN, so each show arrives with its network.";
    }
    return "The for loop evaluates the queryset: one query for the shows.";
  }
  if (data.mode === "naive") {
    return data.rel === "network"
      ? "Reading show.network is lazy: Django sends a new query for each show."
      : "show.episodes.all() is not cached: one new query per show.";
  }
  return "prefetch_related runs a second query for all episodes at once. The loop body sends no SQL.";
}

function render() {
  const count = sqlList.length;
  // Until the loop finishes the total is only the saved benchmark's number.
  const total = done ? count : Math.max(current.total, count);

  document.getElementById("fn-name").textContent = current.fn + "()";
  document.getElementById("exp-id").textContent = "experiment " + current.code;
  document.getElementById("count").textContent = count;
  document.getElementById("total").textContent = total;
  const footnote = data.rel === "network" ? rel.footnote : "";
  document.getElementById("footnote").textContent = footnote;
  document.getElementById("live-status").textContent = errorText ||
    (busy ? "Running on the database..." : done ? "Finished: every query above was run by Django just now." :
     count ? "Paused. Each press runs the next queries for real." : "Nothing has run yet.");

  const countColor = data.mode === "naive" && count > 1 ? "var(--bad)" : count ? "var(--good)" : "var(--ink)";
  document.getElementById("counter-value").style.color = countColor;
  document.getElementById("progress-fill").style.width = (total ? (count / total) * 100 : 0) + "%";
  document.getElementById("progress-fill").style.background = countColor;

  // Code lines: line 0 is the queryset, the rest is the loop body.
  const lines = ["shows = " + current.queryset, ...rel.bodyLines];
  let activeIndex = -1;
  if (count >= 1) activeIndex = (count === 1 || data.mode === "opt") ? 2 : 3;
  const codeBox = document.getElementById("code-lines");
  codeBox.innerHTML = "";
  lines.forEach((text, i) => {
    const row = document.createElement("div");
    row.className = "code-line";
    if (i === activeIndex) {
      row.classList.add(count > 1 && data.mode === "naive" ? "active-slow" : "active-fast");
    }
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = i + 1;
    const t = document.createElement("span");
    t.className = "text";
    t.textContent = text;
    row.appendChild(n);
    row.appendChild(t);
    codeBox.appendChild(row);
  });
  document.getElementById("line-note").textContent = lineNoteFor(count);

  // SQL log: newest first, last 10 entries.
  const logBox = document.getElementById("sql-log-rows");
  logBox.innerHTML = "";
  const emptyBox = document.getElementById("sql-log-empty");
  const hiddenBox = document.getElementById("sql-log-hidden");
  if (count === 0) {
    emptyBox.style.display = "block";
    hiddenBox.style.display = "none";
  } else {
    emptyBox.style.display = "none";
    for (let i = count; i >= Math.max(1, count - 9); i--) {
      const q = noteForStep(i);
      const row = document.createElement("div");
      row.className = "sql-row";
      if (i === count) row.style.background = "var(--soft)";
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = "#" + i;
      const body = document.createElement("div");
      const sqlLine = document.createElement("span");
      sqlLine.className = "sql";
      sqlLine.textContent = sqlList[i - 1];
      const note = document.createElement("span");
      note.className = "note";
      note.style.color = q.kind === "bad" ? "var(--bad)" : q.kind === "good" ? "var(--good)" : "var(--muted)";
      note.textContent = q.note;
      body.appendChild(sqlLine);
      body.appendChild(note);
      row.appendChild(n);
      row.appendChild(body);
      logBox.appendChild(row);
    }
    const hidden = Math.max(0, count - 10);
    if (hidden > 0) {
      hiddenBox.style.display = "block";
      hiddenBox.textContent = "... " + hidden + " earlier queries";
    } else {
      hiddenBox.style.display = "none";
    }
  }

  // Results the loop has produced so far, newest first.
  const resultBox = document.getElementById("result-rows");
  resultBox.innerHTML = "";
  document.getElementById("result-count").textContent = resultRows.length + " rows";
  document.getElementById("result-empty").style.display = resultRows.length ? "none" : "block";
  const secondColumn = data.rel === "network" ? "" : " episodes";
  for (let i = resultRows.length; i >= Math.max(1, resultRows.length - RESULT_ROWS_SHOWN + 1); i--) {
    const row = document.createElement("div");
    row.className = "sql-row";
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = "#" + i;
    const text = document.createElement("span");
    text.className = "sql";
    text.textContent = resultRows[i - 1][0] + "  →  " + resultRows[i - 1][1] + secondColumn;
    row.appendChild(n);
    row.appendChild(text);
    resultBox.appendChild(row);
  }

  // Finished state.
  document.getElementById("done-grid").style.display = done ? "grid" : "none";
  if (done) {
    document.getElementById("measured-ms").textContent = Math.round(serverMs * 100) / 100 + " ms";
    const otherLabel = other.total + (other.total === 1 ? " query" : " queries");
    document.getElementById("other-summary").textContent = otherLabel + " · " + other.ms + " ms (saved benchmark)";
  }

  document.getElementById("play-btn").textContent = playing ? "Pause" : done ? "Replay" : "Play";
}

function stop() {
  playing = false;
}

function resetState() {
  runId++;
  sqlList = [];
  resultRows = [];
  serverMs = 0;
  done = false;
  busy = false;
  errorText = "";
}

async function step() {
  stop();
  await runNextQueries(1);
}

async function togglePlay() {
  if (playing) {
    stop();
    render();
    return;
  }
  if (done) resetState(); // Replay: run it all again from the start
  playing = true;
  render();
  while (playing && !done) {
    // Bigger batches as the count grows, so long runs still finish quickly.
    const batch = Math.min(200, Math.max(1, Math.round(sqlList.length * 0.012 * PLAY_SPEED)));
    await runNextQueries(batch);
    await sleep(60);
  }
  playing = false;
  render();
}

function reset() {
  stop();
  resetState();
  render();
}

document.getElementById("step-btn").addEventListener("click", step);
document.getElementById("play-btn").addEventListener("click", togglePlay);
document.getElementById("reset-btn").addEventListener("click", reset);

render();
