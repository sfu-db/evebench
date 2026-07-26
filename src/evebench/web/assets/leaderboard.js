let summaries = [];
let selectedDataset = "";
let valueMode = "ratio";
let sortColumn = "neq";
let sortDirection = "desc";

function percent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value);
  return element.innerHTML;
}

function verdictValue(row, verdict) {
  return valueMode === "count"
    ? row[`${verdict}_count`]
    : row[`${verdict}_ratio`];
}

function displayVerdict(row, verdict) {
  const value = verdictValue(row, verdict);
  return valueMode === "count" ? value.toLocaleString() : percent(value);
}

function sortValue(row) {
  if (["eq", "neq", "unknown", "timeout"].includes(sortColumn)) {
    return row[`${sortColumn}_ratio`];
  }
  return row[sortColumn];
}

function compareRows(left, right) {
  const leftValue = sortValue(left);
  const rightValue = sortValue(right);
  const missingLeft = leftValue === null || leftValue === undefined;
  const missingRight = rightValue === null || rightValue === undefined;

  if (missingLeft !== missingRight) return missingLeft ? 1 : -1;
  if (missingLeft && missingRight) return 0;

  const comparison = typeof leftValue === "string"
    ? leftValue.localeCompare(rightValue, undefined, { sensitivity: "base" })
    : leftValue - rightValue;
  return sortDirection === "asc" ? comparison : -comparison;
}

function updateSortHeaders() {
  document.querySelectorAll("th[data-sort]").forEach((heading) => {
    const active = heading.dataset.sort === sortColumn;
    heading.setAttribute(
      "aria-sort",
      active ? (sortDirection === "asc" ? "ascending" : "descending") : "none",
    );
    heading.querySelector(".sort-indicator").textContent =
      active ? (sortDirection === "asc" ? "↑" : "↓") : "";
  });
}

function renderBoard(dataset) {
  selectedDataset = dataset;
  const rows = summaries
    .filter((row) => row.dataset === dataset)
    .sort((left, right) => compareRows(left, right)
      || left.display_name.localeCompare(right.display_name));
  const body = document.querySelector("#board-body");
  updateSortHeaders();
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty-board">No persisted evaluations yet.</td></tr>`;
    document.querySelector("#snapshot-label").textContent = "";
    return;
  }
  body.innerHTML = rows.map((row, index) => `
    <tr>
      <td class="rank">${index + 1}</td>
      <td class="method">${escapeHtml(row.display_name)}<small>${escapeHtml(row.checker_id)}</small></td>
      <td class="score outcome-eq">${displayVerdict(row, "eq")}</td>
      <td class="score outcome-neq">${displayVerdict(row, "neq")}</td>
      <td class="score outcome-unknown">${displayVerdict(row, "unknown")}</td>
      <td class="score outcome-timeout">${displayVerdict(row, "timeout")}</td>
      <td class="score">${row.median_duration_ms === null ? "—" : `${row.median_duration_ms.toFixed(0)}ms`}</td>
      <td><span class="coverage">${row.evaluated} / ${row.total_cases}</span></td>
    </tr>`).join("");
  const first = rows[0];
  const state = first.run_status === "completed" ? "complete" : first.run_status;
  document.querySelector("#snapshot-label").textContent =
    `run ${first.run_id} · ${state} · ${rows.length} backends`;
}

async function loadBoard() {
  const select = document.querySelector("#dataset");
  try {
    const response = await fetch("/v1/benchmarks/summary");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    summaries = await response.json();
    const datasets = [...new Set(summaries.map((row) => row.dataset))];
    select.innerHTML = datasets.length
      ? datasets.map((name) => `<option>${name}</option>`).join("")
      : "<option>No benchmark runs</option>";
    renderBoard(select.value);
  } catch (error) {
    select.innerHTML = "<option>Unavailable</option>";
    document.querySelector("#board-body").innerHTML =
      `<tr><td colspan="8" class="empty-board">Could not load benchmark results.</td></tr>`;
    document.querySelector("#snapshot-label").textContent = error.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const select = document.querySelector("#dataset");
  select.addEventListener("change", () => renderBoard(select.value));
  document.querySelectorAll('input[name="value-mode"]').forEach((input) => {
    input.addEventListener("change", (event) => {
      valueMode = event.target.value;
      renderBoard(selectedDataset);
    });
  });
  document.querySelectorAll("th[data-sort] button").forEach((button) => {
    button.addEventListener("click", () => {
      const nextColumn = button.closest("th").dataset.sort;
      if (sortColumn === nextColumn) {
        sortDirection = sortDirection === "asc" ? "desc" : "asc";
      } else {
        sortColumn = nextColumn;
        sortDirection = ["display_name", "median_duration_ms"].includes(nextColumn)
          ? "asc"
          : "desc";
      }
      renderBoard(selectedDataset);
    });
  });
  loadBoard();
});
