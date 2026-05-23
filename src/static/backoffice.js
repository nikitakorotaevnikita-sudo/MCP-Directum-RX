const statusEl = document.querySelector("#rx-status");
const form = document.querySelector("#connection-form");
const urlInput = document.querySelector("#rx-url");
const loginInput = document.querySelector("#rx-login");
const passwordInput = document.querySelector("#rx-password");
const testButton = document.querySelector("#rx-test");

function connectionPayload() {
  return {
    base_url: urlInput.value.trim(),
    username: loginInput.value.trim(),
    password: passwordInput.value,
  };
}

function describeConnection(data, prefix) {
  const user = data.current_user ? ` User: ${data.current_user.name}.` : "";
  statusEl.textContent = `${prefix}: ${data.base_url}.${user}`;
}

async function loadConnectionStatus() {
  const response = await fetch("/api/directum/connection/status");
  const data = await response.json();
  urlInput.value = data.base_url || "";
  statusEl.textContent = data.auth_configured
    ? `Configured: ${data.base_url}.`
    : "Directum RX connection is not configured.";
}

async function postConnection(url, prefix) {
  statusEl.textContent = "Checking Directum RX connection...";
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(connectionPayload()),
  });
  const data = await response.json();
  if (!response.ok) {
    statusEl.textContent = data.detail || "Connection check failed.";
    return;
  }
  describeConnection(data, prefix);
  if (url.endsWith("/apply")) {
    passwordInput.value = "";
  }
}

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
    div.textContent = `${call.name} / ${call.success ? "OK" : "ERROR"} / ${call.duration_ms || 0} ms`;
    calls.appendChild(div);
  });
}

testButton.addEventListener("click", () => {
  postConnection("/api/directum/connection/test", "Connection ok");
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  postConnection("/api/directum/connection/apply", "Applied");
});

loadConnectionStatus();
loadMetrics();
