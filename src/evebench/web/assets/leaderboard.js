let summaries = [];

function percent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function renderBoard(dataset) {
  const rows = summaries.filter((row) => row.dataset === dataset);
  const body = document.querySelector("#board-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty-board">No persisted evaluations yet.</td></tr>`;
    document.querySelector("#snapshot-label").textContent = "";
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td class="method">${row.display_name}<small>${row.checker_id}</small></td>
      <td class="score outcome-eq">${percent(row.eq_ratio)}</td>
      <td class="score outcome-neq">${percent(row.neq_ratio)}</td>
      <td class="score outcome-unknown">${percent(row.unknown_ratio)}</td>
      <td class="score outcome-timeout">${percent(row.timeout_ratio)}</td>
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
      `<tr><td colspan="7" class="empty-board">Could not load benchmark results.</td></tr>`;
    document.querySelector("#snapshot-label").textContent = error.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const select = document.querySelector("#dataset");
  select.addEventListener("change", () => renderBoard(select.value));
  loadBoard();
});
