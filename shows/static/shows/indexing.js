// Plain DOM JavaScript, no framework. The Run button asks Django to run
// EXPLAIN (ANALYZE, BUFFERS) on PostgreSQL twice (index off, index on) and
// shows what PostgreSQL really measured.
//
// The numbers (rows, pages, milliseconds, plan steps) are real. The coloured
// squares are only a DRAWING of those numbers: each square stands for a group
// of table pages, and a lit square means "pages in this group were touched".

const data = JSON.parse(document.getElementById("indexing-data").textContent);
const facts = data.experiments[data.exp];
const LANES = ["off", "on"];
const MAX_CELLS = 240;       // the grid never has more squares than this
const ANIMATION_TICKS = 80;  // 80 ticks of 30 ms = about 2.4 seconds
const TICK_MS = 30;

const cellCount = Math.max(1, Math.min(MAX_CELLS, facts.tablePages));
const pagesPerCell = Math.max(1, facts.tablePages / cellCount);

let results = { off: null, on: null };
let litCells = { off: [], on: [] };  // which squares light up for each lane, in order
let timer = null;
let running = false;

function el(id) {
  return document.getElementById(id);
}

function formatNumber(n) {
  return Number(n).toLocaleString();
}

function formatMs(ms) {
  return (Math.round(ms * 100) / 100) + " ms";
}

// Which squares light up. A sequential scan reads every page in order. An
// index touches only a few pages, so the squares are spread evenly.
function pickCells(result) {
  const pages = Math.min(result.buffers, facts.tablePages);
  const wanted = Math.max(1, Math.min(cellCount, Math.ceil(pages / pagesPerCell)));
  const cells = [];
  for (let k = 0; k < wanted; k++) {
    cells.push(result.index_used ? Math.floor((k + 0.5) * cellCount / wanted) : k);
  }
  return cells;
}

function buildGrids() {
  LANES.forEach((lane) => {
    const grid = el(lane + "-grid");
    grid.innerHTML = "";
    for (let i = 0; i < cellCount; i++) {
      const cell = document.createElement("span");
      cell.className = "page-cell";
      grid.appendChild(cell);
    }
  });
}

function showFacts() {
  el("facts-title").textContent = facts.title;
  el("facts-filter").textContent = facts.filter;
  el("fact-table").textContent = facts.table;
  el("fact-rows").textContent = formatNumber(facts.tableRows) + " rows";
  el("fact-pages").textContent = formatNumber(facts.tablePages);
  el("fact-index").textContent = facts.indexName;
  el("fact-index-size").textContent = facts.indexExists ? "size " + facts.indexSize : "index does not exist in the database";
  el("fact-note").textContent = facts.note;
}

// Draws one lane when the animation is `progress` of the way through (0 to 1).
function renderLane(lane, progress) {
  const result = results[lane];
  const cells = el(lane + "-grid").children;
  const lit = litCells[lane];

  // Squares: light up the first `shown` entries of this lane's list.
  const shown = result ? Math.ceil(lit.length * progress) : 0;
  for (let i = 0; i < cells.length; i++) cells[i].className = "page-cell";
  for (let k = 0; k < shown; k++) cells[lit[k]].className = "page-cell lit-" + lane;

  if (!result) {
    el(lane + "-scan").textContent = "not run yet";
    el(lane + "-scan").className = "plan-chip";
    el(lane + "-note").textContent = "";
    el(lane + "-examined").textContent = "–";
    el(lane + "-buffers").textContent = "–";
    el(lane + "-returned").textContent = "–";
    el(lane + "-legend").textContent = "";
    el(lane + "-plan").textContent = "Not run yet.";
    return;
  }

  // Counters count up with the animation and end on the exact measured value.
  el(lane + "-examined").textContent = formatNumber(Math.round(result.rows_examined * progress));
  el(lane + "-buffers").textContent = formatNumber(Math.round(result.buffers * progress));
  el(lane + "-returned").textContent = formatNumber(Math.round(result.rows_returned * progress));

  const chip = el(lane + "-scan");
  chip.textContent = result.scan_type;
  chip.className = result.index_used ? "plan-chip good" : "plan-chip";
  el(lane + "-note").textContent = result.index_used ? "uses " + result.index_name : "";

  el(lane + "-legend").textContent = "Each square is about " + Math.round(pagesPerCell * 10) / 10 +
    " table page(s). Lit squares: pages PostgreSQL touched (" + formatNumber(result.buffers_hit) +
    " already in memory, " + formatNumber(result.buffers_read) + " read from disk).";

  const lines = result.nodes.map((n) => {
    let text = "  ".repeat(n.depth) + n.node_type;
    if (n.relation) text += " on " + n.relation;
    if (n.index_name) text += " using " + n.index_name;
    if (n.condition) text += "  [" + n.condition + "]";
    text += "  rows=" + n.actual_rows;
    if (n.rows_removed) text += " removed=" + n.rows_removed;
    text += "  " + formatMs(n.ms);
    return text;
  });
  el(lane + "-plan").textContent = lines.join("\n") + "\n\nPlanning: " + formatMs(result.planning_ms) +
    "   Execution: " + formatMs(result.execution_ms) + "   Cost estimate: " + result.total_cost;
}

function renderSummary() {
  const off = results.off;
  const on = results.on;
  el("summary").style.display = off && on ? "flex" : "none";
  if (!(off && on)) return;

  const slowest = Math.max(off.execution_ms, on.execution_ms, 0.001);
  el("bar-off").style.width = (off.execution_ms / slowest) * 100 + "%";
  el("bar-on").style.width = (on.execution_ms / slowest) * 100 + "%";
  el("ms-off").textContent = formatMs(off.execution_ms);
  el("ms-on").textContent = formatMs(on.execution_ms);

  let verdict;
  if (on.index_used) {
    verdict = "PostgreSQL used the index: it examined " + formatNumber(on.rows_examined) + " rows instead of " +
      formatNumber(off.rows_examined) + " and touched " + formatNumber(on.buffers) + " pages instead of " +
      formatNumber(off.buffers) + ".";
  } else {
    verdict = "PostgreSQL ignored the index (" + on.scan_type + "). This filter matches " +
      formatNumber(on.rows_returned) + " of " + formatNumber(facts.tableRows) +
      " rows, so reading the whole table in order is cheaper than jumping around through the index.";
  }
  el("verdict").textContent = verdict;
}

function renderAll(progress) {
  LANES.forEach((lane) => renderLane(lane, progress));
  renderSummary();
}

async function fetchPlan(index) {
  const url = data.runUrl + "?exp=" + data.exp + "&index=" + index;
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "HTTP " + response.status);
  return body;
}

function stopAnimation() {
  clearInterval(timer);
  timer = null;
}

function animate() {
  stopAnimation();
  let tick = 0;
  timer = setInterval(() => {
    tick++;
    const progress = Math.min(1, tick / ANIMATION_TICKS);
    renderAll(progress);
    if (progress >= 1) {
      stopAnimation();
      running = false;
      el("run-btn").disabled = false;
      el("run-btn").textContent = "Run again";
      el("status").textContent = "Done. Every number above was measured by PostgreSQL just now.";
    }
  }, TICK_MS);
}

async function run() {
  if (running) return;
  running = true;
  el("run-btn").disabled = true;
  stopAnimation();
  results = { off: null, on: null };
  renderAll(0);
  el("status").textContent = "Running EXPLAIN ANALYZE on PostgreSQL (index off, then index on)...";
  try {
    // One after the other so the first run does not compete with the second.
    results.off = await fetchPlan("off");
    results.on = await fetchPlan("on");
  } catch (error) {
    running = false;
    results = { off: null, on: null };
    renderAll(0);
    el("run-btn").disabled = false;
    el("status").textContent = "Could not run the plans: " + error.message;
    return;
  }
  el("sql-text").textContent = results.on.sql;
  litCells.off = pickCells(results.off);
  litCells.on = pickCells(results.on);
  el("status").textContent = "Drawing what PostgreSQL measured...";
  animate();
}

function reset() {
  stopAnimation();
  running = false;
  results = { off: null, on: null };
  el("run-btn").disabled = false;
  el("run-btn").textContent = "Run";
  el("sql-text").textContent = "Press Run to see the SQL Django sends.";
  el("status").textContent = "Nothing has run yet.";
  renderAll(0);
}

el("run-btn").addEventListener("click", run);
el("reset-btn").addEventListener("click", reset);

showFacts();
buildGrids();
renderAll(0);
