// Plain DOM JavaScript, no framework. Drives the step/play/reset animation
// for the CURRENT relationship + mode (switching those reloads the page,
// see the links in explainer.html) using the real measured numbers Django
// put into the "explainer-data" JSON script tag.

const data = JSON.parse(document.getElementById("explainer-data").textContent);
const rel = data.relationships[data.rel];
const current = rel[data.mode];
const other = rel[data.mode === "naive" ? "opt" : "naive"];
const PLAY_SPEED = 5; // matches the design's default speed (range was 1-10)

// SQL text shown in the log. Close enough to the real SQL to teach the
// shape of each query; column lists are shortened with "...".
const SQL = {
  shows: 'SELECT ... FROM "shows_show" ORDER BY "shows_show"."id" ASC',
  join: 'SELECT ... FROM "shows_show" LEFT OUTER JOIN "shows_network" ON ("shows_show"."network_id" = "shows_network"."id") ORDER BY "shows_show"."id" ASC',
  network: 'SELECT ... FROM "shows_network" WHERE "shows_network"."id" = %s LIMIT 21',
  episode: 'SELECT ... FROM "shows_episode" WHERE "shows_episode"."show_id" = %s',
  episodesIn: 'SELECT ... FROM "shows_episode" WHERE "shows_episode"."show_id" IN (%s, %s, ... ' + data.showCount + ' ids)',
};

let count = 0;
let playing = false;
let timer = null;

function sqlForStep(i) {
  if (i === 1) {
    if (data.rel === "network" && data.mode === "opt") {
      return { sql: SQL.join, note: "Shows and their networks in one JOIN", kind: "base" };
    }
    return { sql: SQL.shows, note: "Loads all " + data.showCount + " shows", kind: "base" };
  }
  if (data.mode === "opt") {
    return {
      sql: SQL.episodesIn,
      note: "prefetch_related: every episode for all " + data.showCount + " shows, attached in Python",
      kind: "good",
    };
  }
  if (data.rel === "network") {
    return {
      sql: SQL.network,
      note: "show.network, lazy load for show " + (i - 1) + " of " + data.withNetworkCount + " with a network",
      kind: "bad",
    };
  }
  return {
    sql: SQL.episode,
    note: "show.episodes.all() for show " + (i - 1) + " of " + data.showCount,
    kind: "bad",
  };
}

function lineNoteFor(count) {
  if (count === 0) return "Press Step to run the next query.";
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
  const total = current.total;
  const done = count >= total;

  document.getElementById("fn-name").textContent = current.fn + "()";
  document.getElementById("exp-id").textContent = "experiment " + current.code;
  document.getElementById("count").textContent = count;
  document.getElementById("total").textContent = total;
  document.getElementById("footnote").textContent = data.rel === "network" ? rel.footnote : "";

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
      const q = sqlForStep(i);
      const row = document.createElement("div");
      row.className = "sql-row";
      if (i === count) row.style.background = "var(--soft)";
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = "#" + i;
      const body = document.createElement("div");
      const sqlLine = document.createElement("span");
      sqlLine.className = "sql";
      sqlLine.textContent = q.sql;
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

  // Finished state.
  document.getElementById("done-grid").style.display = done ? "grid" : "none";
  if (done) {
    document.getElementById("measured-ms").textContent = current.ms + " ms";
    const otherLabel = other.total + (other.total === 1 ? " query" : " queries");
    document.getElementById("other-summary").textContent = otherLabel + " · " + other.ms + " ms";
  }

  document.getElementById("play-btn").textContent = playing ? "Pause" : done ? "Replay" : "Play";
}

function stop() {
  clearInterval(timer);
  timer = null;
  playing = false;
}

function step() {
  stop();
  count = Math.min(current.total, count + 1);
  render();
}

function tick() {
  const add = Math.max(1, Math.round(count * 0.012 * PLAY_SPEED));
  count = Math.min(current.total, count + add);
  render();
  if (count >= current.total) stop();
}

function togglePlay() {
  if (playing) {
    stop();
    render();
    return;
  }
  if (count >= current.total) count = 0;
  playing = true;
  timer = setInterval(tick, 60);
  render();
}

function reset() {
  stop();
  count = 0;
  render();
}

document.getElementById("step-btn").addEventListener("click", step);
document.getElementById("play-btn").addEventListener("click", togglePlay);
document.getElementById("reset-btn").addEventListener("click", reset);

render();
