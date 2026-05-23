async function loadMetrics() {
  const response = await fetch("/api/metrics");
  const data = await response.json();
  document.querySelector("#chat-requests").textContent = data.chat_requests;
  document.querySelector("#previews").textContent = data.action_item_previews;
  document.querySelector("#confirmed").textContent = data.action_item_confirmed;
  document.querySelector("#errors").textContent = data.errors;

  const calls = document.querySelector("#tool-calls");
  calls.innerHTML = "";
  data.latest_tool_calls.forEach((call) => {
    const div = document.createElement("div");
    div.className = "result-card";
    div.textContent = `${call.name} · ${call.success ? "OK" : "ERROR"} · ${call.duration_ms || 0} ms`;
    calls.appendChild(div);
  });
}

loadMetrics();
