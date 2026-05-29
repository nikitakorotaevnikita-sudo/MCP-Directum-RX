const rxStatusEl = document.querySelector("#rx-status");
const rxForm = document.querySelector("#connection-form");
const rxUrlInput = document.querySelector("#rx-url");
const rxLoginInput = document.querySelector("#rx-login");
const rxPasswordInput = document.querySelector("#rx-password");
const rxTestButton = document.querySelector("#rx-test");

const llmStatusEl = document.querySelector("#llm-status");
const llmForm = document.querySelector("#llm-form");
const llmProviderInput = document.querySelector("#llm-provider");
const llmBaseUrlInput = document.querySelector("#llm-base-url");
const llmApiKeyInput = document.querySelector("#llm-api-key");
const llmModelInput = document.querySelector("#llm-model");
const llmToolCallingInput = document.querySelector("#llm-tool-calling");
const llmTestButton = document.querySelector("#llm-test");

const providerDefaults = {
  ario: {
    base_url: "https://llm.ario.directum360.ru/v1",
    model: "Qwen/Qwen3.6-35B-A3B",
  },
  openrouter: {
    base_url: "https://openrouter.ai/api/v1",
    model: "google/gemma-4-26b-a4b-it:free",
  },
  ollama: {
    base_url: "http://localhost:11434/v1",
    model: "qwen3:8b",
  },
};

function rxConnectionPayload() {
  return {
    base_url: rxUrlInput.value.trim(),
    username: rxLoginInput.value.trim(),
    password: rxPasswordInput.value,
  };
}

function applyProviderDefaults() {
  const defaults = providerDefaults[llmProviderInput.value];
  if (!defaults) {
    return;
  }
  llmBaseUrlInput.value = defaults.base_url;
  llmModelInput.value = defaults.model;
}

function llmConnectionPayload() {
  return {
    provider: llmProviderInput.value,
    base_url: llmBaseUrlInput.value.trim(),
    api_key: llmApiKeyInput.value,
    model: llmModelInput.value.trim(),
    tool_calling: llmToolCallingInput.value,
  };
}

function describeRxConnection(data, prefix) {
  const user = data.current_user ? ` User: ${data.current_user.name}.` : "";
  rxStatusEl.textContent = `${prefix}: ${data.base_url}.${user}`;
}

function describeLlmConnection(data, prefix) {
  llmStatusEl.textContent = `${prefix}: ${data.provider} / ${data.model} / ${data.base_url}.`;
}

async function loadRxConnectionStatus() {
  const response = await fetch("/api/directum/connection/status");
  const data = await response.json();
  rxUrlInput.value = data.base_url || "";
  rxStatusEl.textContent = data.auth_configured
    ? `Configured: ${data.base_url}.`
    : "Directum RX connection is not configured.";
}

async function loadLlmConnectionStatus() {
  const response = await fetch("/api/llm/connection/status");
  const data = await response.json();
  llmProviderInput.value = data.provider;
  llmBaseUrlInput.value = data.base_url || "";
  llmModelInput.value = data.model || "";
  llmToolCallingInput.value = data.tool_calling || "auto";
  describeLlmConnection(data, data.api_key_configured ? "Configured" : "Configured without API key");
}

async function postRxConnection(url, prefix) {
  rxStatusEl.textContent = "Checking Directum RX connection...";
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(rxConnectionPayload()),
  });
  const data = await response.json();
  if (!response.ok) {
    rxStatusEl.textContent = data.detail || "Connection check failed.";
    return;
  }
  describeRxConnection(data, prefix);
  if (url.endsWith("/apply")) {
    rxPasswordInput.value = "";
  }
}

async function postLlmConnection(url, prefix) {
  llmStatusEl.textContent = "Checking LLM connection...";
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(llmConnectionPayload()),
  });
  const data = await response.json();
  if (!response.ok) {
    llmStatusEl.textContent = data.detail || "LLM check failed.";
    return;
  }
  describeLlmConnection(data, prefix);
  if (url.endsWith("/apply")) {
    llmApiKeyInput.value = "";
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

rxTestButton.addEventListener("click", () => {
  postRxConnection("/api/directum/connection/test", "Connection ok");
});

rxForm.addEventListener("submit", (event) => {
  event.preventDefault();
  postRxConnection("/api/directum/connection/apply", "Applied");
});

llmTestButton.addEventListener("click", () => {
  postLlmConnection("/api/llm/connection/test", "Connection ok");
});

llmForm.addEventListener("submit", (event) => {
  event.preventDefault();
  postLlmConnection("/api/llm/connection/apply", "Applied");
});

llmProviderInput.addEventListener("change", applyProviderDefaults);

loadLlmConnectionStatus();
loadRxConnectionStatus();
loadMetrics();

// Подсветка активного пункта sidebar при скролле
(function initSidebarActiveLink() {
  const sections = document.querySelectorAll("main section[id]");
  const links    = document.querySelectorAll(".app-sidebar .nav-item[href^='#']");
  if (!sections.length || !links.length) return;
  const map = new Map(Array.from(links).map(l => [l.getAttribute("href").slice(1), l]));
  const io  = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      const link = map.get(e.target.id);
      if (!link) return;
      if (e.isIntersecting) {
        links.forEach(l => l.classList.remove("active"));
        link.classList.add("active");
      }
    });
  }, { rootMargin: "-30% 0px -60% 0px", threshold: 0 });
  sections.forEach(s => io.observe(s));
})();
