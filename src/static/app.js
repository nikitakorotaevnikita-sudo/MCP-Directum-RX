const messages = document.querySelector("#messages");
const results = document.querySelector("#results");
const statusBox = document.querySelector("#status");
const chatHistory = [];

async function loadStatus() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    statusBox.textContent = `${data.status} · ${data.llm.provider} · ${data.llm.model}`;
  } catch (error) {
    statusBox.textContent = "offline";
  }
}

function addMessage(text, role = "assistant") {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function renderResults(items) {
  results.innerHTML = "";
  const list = Array.isArray(items) ? items : [items];
  list.forEach((item) => {
    const card = document.createElement("div");
    card.className = "result-card";
    const title = item.subject || item.name || item.message || item.detail || "Result";
    const meta = item.status || item.mode || item.entity_type || "";
    card.textContent = meta ? `${title} · ${meta}` : title;
    results.appendChild(card);
  });
}

async function quickAction(action) {
  const endpoints = {
    my: "/api/directum/assignments/my",
    overdue: "/api/directum/assignments/overdue",
    assigned: "/api/directum/action-items/assigned-to-me",
    created: "/api/directum/action-items/created-by-me",
  };

  if (action === "create") {
    addMessage("Укажите тему, исполнителя и текст поручения в чате. Сначала будет подготовлен preview.", "assistant");
    return;
  }

  const response = await fetch(endpoints[action]);
  renderResults(await response.json());
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => quickAction(button.dataset.action));
});

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#chat-input");
  const text = input.value.trim();
  if (!text) {
    return;
  }

  input.value = "";
  addMessage(text, "user");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({message: text, history: chatHistory.slice(-12)}),
  });
  const answer = await response.text();
  addMessage(answer, "assistant");
  chatHistory.push({role: "user", content: text});
  chatHistory.push({role: "assistant", content: answer});
});

loadStatus();
