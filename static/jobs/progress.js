"use strict";
(() => {
  const panel = document.querySelector("[data-job-progress]");
  if (!panel || !["PENDING", "RUNNING"].includes(panel.dataset.status)) return;
  async function refresh() {
    try {
      const response = await fetch(panel.dataset.jobProgress, {
        credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}
      });
      if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) return;
      const data = await response.json();
      if (data.status !== panel.dataset.status || String(data.attempts) !== panel.dataset.attempts) {
        window.location.reload();
        return;
      }
      panel.querySelector("[data-job-status]").textContent = data.status_label;
      panel.querySelector("[data-job-count]").textContent = `${data.current} / ${data.total}`;
      panel.querySelector("progress").max = data.total || 1;
      panel.querySelector("progress").value = data.current;
      if (!data.terminal) window.setTimeout(refresh, 2000);
    } catch {
      // A manual refresh remains available; polling stops when connectivity is lost.
    }
  }
  window.setTimeout(refresh, 2000);
})();
