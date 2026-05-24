const messages = document.querySelector("#messages");
const results = document.querySelector("#results");
const statusBox = document.querySelector("#status");
const chatHistory = [];
const previewMarkerPattern = /\n?\[\[DIRECTUM_ACTION_ITEM_PREVIEW:([\s\S]*?)\]\]\s*$/;

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
  return div;
}

function parseAssistantResponse(text) {
  const markerMatch = text.match(previewMarkerPattern);
  if (!markerMatch) {
    return {text, preview: null};
  }

  try {
    return {
      text: text.slice(0, markerMatch.index).trimEnd(),
      preview: JSON.parse(markerMatch[1]),
    };
  } catch (error) {
    return {text, preview: null};
  }
}

function setPreviewButtonsEnabled(card, enabled) {
  card.querySelectorAll("button").forEach((button) => {
    button.disabled = !enabled;
  });
}

function previewRow(label, value) {
  const row = document.createElement("div");
  row.className = "preview-row";

  const labelElement = document.createElement("span");
  labelElement.className = "preview-label";
  labelElement.textContent = label;

  const valueElement = document.createElement("span");
  valueElement.textContent = value || "-";

  row.append(labelElement, valueElement);
  return row;
}

function renderActionItemPreview(preview, messageElement) {
  const payload = preview.payload || {};
  const display = preview.display || {};
  const card = document.createElement("div");
  card.className = "preview-card";

  const title = document.createElement("div");
  title.className = "preview-title";
  title.textContent = "Черновик поручения";

  const rows = document.createElement("div");
  rows.className = "preview-rows";
  rows.append(
    previewRow("Исполнитель", display.performer_name || payload.performer_id),
    previewRow("Тема", payload.subject),
    previewRow("Текст", payload.action_text),
    previewRow("Срок", payload.deadline)
  );

  const actions = document.createElement("div");
  actions.className = "preview-actions";

  const confirmButton = document.createElement("button");
  confirmButton.type = "button";
  confirmButton.className = "preview-confirm";
  confirmButton.textContent = "Создать поручение";

  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.className = "preview-cancel";
  cancelButton.textContent = "Отмена";

  const status = document.createElement("div");
  status.className = "preview-status";

  actions.append(confirmButton, cancelButton);
  card.append(title, rows, actions, status);
  messageElement.appendChild(card);

  confirmButton.addEventListener("click", () => confirmActionItemPreview(preview, card, status));
  cancelButton.addEventListener("click", () => {
    setPreviewButtonsEnabled(card, false);
    status.textContent = "Создание отменено.";
  });
}

async function confirmActionItemPreview(preview, card, status) {
  setPreviewButtonsEnabled(card, false);
  status.textContent = "Создаю поручение...";

  try {
    const response = await fetch("/api/directum/action-items", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({...preview.payload, confirm: true}),
    });
    const result = await response.json();
    if (!response.ok || result.success === false) {
      status.textContent = result.detail || result.message || "Не удалось создать поручение.";
      setPreviewButtonsEnabled(card, true);
      return;
    }

    const idText = result.directum_id ? ` ID: ${result.directum_id}` : "";
    status.textContent = `Поручение создано.${idText}`;
  } catch (error) {
    status.textContent = "Не удалось создать поручение. Проверьте подключение к Directum RX.";
    setPreviewButtonsEnabled(card, true);
  }
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
  const parsedAnswer = parseAssistantResponse(answer);
  const assistantMessage = addMessage(parsedAnswer.text, "assistant");
  if (parsedAnswer.preview?.type === "action_item") {
    renderActionItemPreview(parsedAnswer.preview, assistantMessage);
  }
  chatHistory.push({role: "user", content: text});
  chatHistory.push({role: "assistant", content: parsedAnswer.text});
});

loadStatus();
