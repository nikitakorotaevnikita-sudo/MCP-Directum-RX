const messages = document.querySelector("#messages");
const results = document.querySelector("#results");
const statusBox = document.querySelector("#status");
const chatInput = document.querySelector("#chat-input");
const promptChips = document.querySelector("#prompt-chips");
const promptDialog = document.querySelector("#prompt-dialog");
const promptEditorList = document.querySelector("#prompt-editor-list");
const chatHistory = [];
const previewMarkerPattern = /\n?\[\[DIRECTUM_ACTION_ITEM_PREVIEW:([\s\S]*?)\]\]\s*$/;
const promptStorageKey = "directum.quickPrompts";
const defaultPrompts = [
  {
    title: "Аналитика исходящих",
    text: "Дай аналитику по исходящим поручениям",
  },
  {
    title: "Исходящие поручения",
    text: "Дай сводку по исходящим поручениям",
  },
  {
    title: "Мои задания",
    text: "Посмотри мои задания",
  },
];

let quickPrompts = loadQuickPrompts();

async function loadStatus() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    statusBox.textContent = `${data.status} · ${data.llm.provider} · ${data.llm.model}`;
  } catch (error) {
    statusBox.textContent = "offline";
  }
}

function addMessage(text, role = "assistant", isMarkdown = false) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "assistant" && isMarkdown) {
    div.innerHTML = marked.parse(text);
  } else {
    div.textContent = text;
  }
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function looksLikeMarkdown(text) {
  const mdPatterns = [
    /\*\*.+?\*\*/,    // **bold**
    /__.+?__/,        // __bold__
    /\*.+?\*/,        // *italic*
    /_.+?_/,          // _italic_
    /^#+\s/m,         // # headings
    /^[-*]\s/m,       // - or * bullet lists
    /^\d+\.\s/m,      // 1. numbered lists
    /```[\s\S]*?```/, // ```code blocks```
    /`[^`]+`/,        // `inline code`
    /\[.+?\]\(.+?\)/, // [text](url) links
  ];
  return mdPatterns.some((p) => p.test(text));
}

function loadQuickPrompts() {
  try {
    const raw = localStorage.getItem(promptStorageKey);
    if (!raw) {
      return defaultPrompts;
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return defaultPrompts;
    }
    const prompts = parsed
      .map((item) => ({
        title: String(item.title || "").trim(),
        text: String(item.text || "").trim(),
      }))
      .filter((item) => item.title && item.text);
    return prompts.length ? prompts : defaultPrompts;
  } catch (error) {
    return defaultPrompts;
  }
}

function saveQuickPrompts(prompts) {
  quickPrompts = prompts;
  localStorage.setItem(promptStorageKey, JSON.stringify(quickPrompts));
  renderPromptChips();
}

function renderPromptChips() {
  promptChips.innerHTML = "";
  quickPrompts.forEach((prompt) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "prompt-chip";
    button.textContent = prompt.title;
    button.title = prompt.text;
    button.addEventListener("click", () => {
      chatInput.value = prompt.text;
      chatInput.focus();
    });
    promptChips.appendChild(button);
  });
}

function promptEditorRow(prompt = {title: "", text: ""}) {
  const row = document.createElement("div");
  row.className = "prompt-editor-row";

  const titleField = document.createElement("label");
  titleField.className = "field";
  titleField.innerHTML = "<span>Название</span>";
  const titleInput = document.createElement("input");
  titleInput.className = "prompt-title-input";
  titleInput.value = prompt.title;
  titleInput.maxLength = 48;
  titleField.appendChild(titleInput);

  const textField = document.createElement("label");
  textField.className = "field prompt-text-field";
  textField.innerHTML = "<span>Промпт</span>";
  const textInput = document.createElement("textarea");
  textInput.className = "prompt-text-input";
  textInput.value = prompt.text;
  textInput.rows = 3;
  textField.appendChild(textInput);

  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "btn btn-ghost prompt-delete";
  deleteButton.textContent = "Удалить";
  deleteButton.addEventListener("click", () => row.remove());

  row.append(titleField, textField, deleteButton);
  return row;
}

function renderPromptEditor() {
  promptEditorList.innerHTML = "";
  quickPrompts.forEach((prompt) => promptEditorList.appendChild(promptEditorRow(prompt)));
}

function collectPromptEditorItems() {
  return Array.from(promptEditorList.querySelectorAll(".prompt-editor-row"))
    .map((row) => ({
      title: row.querySelector(".prompt-title-input").value.trim(),
      text: row.querySelector(".prompt-text-input").value.trim(),
    }))
    .filter((item) => item.title && item.text);
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
  const isTask = preview.type === "task";
  const card = document.createElement("div");
  card.className = "preview-card";

  const title = document.createElement("div");
  title.className = "preview-title";
  title.textContent = isTask ? "Черновик задачи" : "Черновик поручения";

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
  confirmButton.textContent = isTask ? "Создать задачу" : "Создать поручение";

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
  const isTask = preview.type === "task";
  setPreviewButtonsEnabled(card, false);
  status.textContent = isTask ? "Создаю задачу..." : "Создаю поручение...";

  try {
    const response = await fetch(isTask ? "/api/directum/tasks" : "/api/directum/action-items", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({...preview.payload, confirm: true}),
    });
    const result = await response.json();
    if (!response.ok || result.success === false) {
      status.textContent = result.detail || result.message || (isTask ? "Не удалось создать задачу." : "Не удалось создать поручение.");
      setPreviewButtonsEnabled(card, true);
      return;
    }

    const idText = result.directum_id ? ` ID: ${result.directum_id}` : "";
    status.textContent = `${isTask ? "Задача создана" : "Поручение создано"}.${idText}`;
    if (result.url) {
      const link = document.createElement("a");
      link.className = "preview-link";
      link.href = result.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = isTask ? "Открыть задачу" : "Открыть поручение";
      status.append(" ");
      status.appendChild(link);
    }
  } catch (error) {
    status.textContent = isTask
      ? "Не удалось создать задачу. Проверьте подключение к Directum RX."
      : "Не удалось создать поручение. Проверьте подключение к Directum RX.";
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
    if (item.url) {
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = title;
      card.appendChild(link);
      if (meta) {
        card.append(` · ${meta}`);
      }
    } else {
      card.textContent = meta ? `${title} · ${meta}` : title;
    }
    results.appendChild(card);
  });
}

function renderMeetingResults(items) {
  results.innerHTML = "";
  if (!Array.isArray(items) || items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "result-card";
    empty.textContent = "Совещаний не запланировано.";
    results.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "result-card";
    const startRaw = item.start_date || "";
    const startStr = startRaw
      ? new Date(startRaw).toLocaleString("ru-RU", {
          day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
        })
      : "";
    const subject = item.subject || "Совещание";
    const place = item.place || "";
    const heading = document.createElement("strong");
    heading.textContent = startStr ? `${startStr} — ${subject}` : subject;
    card.appendChild(heading);
    if (place) {
      card.append(` · ${place}`);
    }
    if (item.client_card_url) {
      const br = document.createElement("br");
      card.appendChild(br);
      const link = document.createElement("a");
      link.href = item.client_card_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Открыть карточку";
      card.appendChild(link);
    }
    results.appendChild(card);
  });
}

async function quickAction(action) {
  const endpoints = {
    my: "/api/directum/assignments/my",
    overdue: "/api/directum/assignments/overdue",
    assigned: "/api/directum/action-items/assigned-to-me",
    created: "/api/directum/action-items/created-by-me",
    meetings: "/api/directum/meetings/upcoming",
  };

  if (action === "create") {
    addMessage("Укажите тему, исполнителя и текст поручения в чате. Сначала будет подготовлен preview.", "assistant");
    return;
  }

  const response = await fetch(endpoints[action]);
  const data = await response.json();
  if (!response.ok) {
    renderResults(data);
    return;
  }
  if (action === "meetings") {
    renderMeetingResults(data);
  } else {
    renderResults(data);
  }
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => quickAction(button.dataset.action));
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

document.querySelector("#prompt-settings").addEventListener("click", () => {
  renderPromptEditor();
  promptDialog.showModal();
});

document.querySelector("#prompt-add").addEventListener("click", () => {
  promptEditorList.appendChild(promptEditorRow());
  promptEditorList.querySelector(".prompt-editor-row:last-child .prompt-title-input").focus();
});

promptDialog.addEventListener("submit", (event) => {
  if (event.submitter?.id !== "prompt-save") {
    return;
  }
  const prompts = collectPromptEditorItems();
  saveQuickPrompts(prompts.length ? prompts : defaultPrompts);
});

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) {
    return;
  }

  chatInput.value = "";
  addMessage(text, "user");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({message: text, history: chatHistory.slice(-12)}),
  });
  const answer = await response.text();
  const parsedAnswer = parseAssistantResponse(answer);
  const isMd = looksLikeMarkdown(parsedAnswer.text);
  const assistantMessage = addMessage(parsedAnswer.text, "assistant", isMd);
  if (parsedAnswer.preview?.type === "action_item" || parsedAnswer.preview?.type === "task") {
    renderActionItemPreview(parsedAnswer.preview, assistantMessage);
  }
  chatHistory.push({role: "user", content: text});
  chatHistory.push({role: "assistant", content: answer});
});

renderPromptChips();
loadStatus();
