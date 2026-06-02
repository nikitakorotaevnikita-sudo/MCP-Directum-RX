const messages = document.querySelector("#messages");
const results = document.querySelector("#results");
const statusBox = document.querySelector("#status");
const llmHeaderStatus = document.querySelector("#llm-header-status");
const chatInput = document.querySelector("#chat-input");
const promptChips = document.querySelector("#prompt-chips");
const promptDialog = document.querySelector("#prompt-dialog");
const promptEditorList = document.querySelector("#prompt-editor-list");
const promptPanel = document.querySelector(".prompt-panel");
const promptToggle = document.querySelector("#prompt-toggle");
const chatHistory = [];
const previewMarkerPattern = /\n?\[\[DIRECTUM_ACTION_ITEM_PREVIEW:([\s\S]*?)\]\]\s*$/;
const analyticsMarkerPattern = /\n?\[\[DIRECTUM_ANALYTICS:([\s\S]*?)\]\]\s*$/;
const SVG_NS = "http://www.w3.org/2000/svg";
const promptStorageKey = "directum.quickPrompts";
const promptCollapsedKey = "directum.promptsCollapsed";
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
    if (llmHeaderStatus) llmHeaderStatus.textContent = `${data.llm.provider} / ${data.llm.model}`;
  } catch (error) {
    statusBox.textContent = "offline";
    if (llmHeaderStatus) llmHeaderStatus.textContent = "LLM: нет связи";
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
      // Быстрый промпт не только вставляется, но и сразу отправляется в чат.
      chatInput.value = prompt.text;
      document.querySelector("#chat-form").dispatchEvent(
        new Event("submit", {bubbles: true, cancelable: true})
      );
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
  const previewMatch = text.match(previewMarkerPattern);
  if (previewMatch) {
    try {
      return {
        text: text.slice(0, previewMatch.index).trimEnd(),
        preview: JSON.parse(previewMatch[1]),
        analytics: null,
      };
    } catch (error) {
      return {text, preview: null, analytics: null};
    }
  }

  const analyticsMatch = text.match(analyticsMarkerPattern);
  if (analyticsMatch) {
    try {
      return {
        text: text.slice(0, analyticsMatch.index).trimEnd(),
        preview: null,
        analytics: JSON.parse(analyticsMatch[1]),
      };
    } catch (error) {
      return {text, preview: null, analytics: null};
    }
  }

  return {text, preview: null, analytics: null};
}

// ── Аналитические чарты (рендерим SVG вручную: без CDN/фреймворков) ─────────

const CHART_TONE_VARS = {
  work: "--blue",
  "due-soon": "--amber",
  overdue: "--red",
  ok: "--green",
  late: "--amber",
};

function toneColor(tone) {
  const cssVar = CHART_TONE_VARS[tone] || "--accent";
  const value = getComputedStyle(document.body).getPropertyValue(cssVar).trim();
  return value || "#0B5FAE";
}

function svgEl(name, attrs = {}) {
  const el = document.createElementNS(SVG_NS, name);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
  return el;
}

function renderBarChart(chart) {
  const wrap = document.createElement("div");
  wrap.className = "analytics-chart";
  if (chart.title) {
    const heading = document.createElement("div");
    heading.className = "analytics-chart-title";
    heading.textContent = chart.title;
    wrap.appendChild(heading);
  }

  const bars = Array.isArray(chart.bars) ? chart.bars : [];
  const maxValue = Math.max(1, ...bars.map((b) => Number(b.value) || 0));
  const rowH = 30;
  const gap = 10;
  const labelW = 120;
  const valueW = 44;
  const width = 460;
  const trackW = width - labelW - valueW;
  const height = bars.length * (rowH + gap);

  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width: "100%",
    height: height,
    role: "img",
  });

  bars.forEach((bar, index) => {
    const value = Number(bar.value) || 0;
    const y = index * (rowH + gap);
    const color = toneColor(bar.tone);

    const label = svgEl("text", {x: 0, y: y + rowH / 2 + 4, class: "analytics-bar-label"});
    label.textContent = bar.label || "";
    svg.appendChild(label);

    svg.appendChild(
      svgEl("rect", {
        x: labelW, y, width: trackW, height: rowH, rx: 5, class: "analytics-bar-track",
      })
    );

    const barW = Math.round((value / maxValue) * trackW);
    const fill = svgEl("rect", {
      x: labelW, y, width: Math.max(value > 0 ? 3 : 0, barW), height: rowH, rx: 5,
    });
    fill.setAttribute("fill", color);
    svg.appendChild(fill);

    const valueText = svgEl("text", {
      x: width, y: y + rowH / 2 + 4, "text-anchor": "end", class: "analytics-bar-value",
    });
    valueText.textContent = value;
    svg.appendChild(valueText);

    // Drill-down: колонка с поручениями кликабельна и открывает модалку.
    const items = Array.isArray(bar.items) ? bar.items : [];
    if (items.length > 0) {
      const hit = svgEl("rect", {
        x: 0, y, width, height: rowH, fill: "transparent",
        class: "analytics-bar-hit", role: "button", tabindex: "0",
        "aria-label": `${bar.label}: ${value}. Открыть список поручений`,
      });
      const open = () => openAnalyticsItemsModal(bar.label, items);
      hit.addEventListener("click", open);
      hit.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
      svg.appendChild(hit);
    }
  });

  wrap.appendChild(svg);
  return wrap;
}

// ── Drill-down модалка по колонке аналитики ────────────────────────────────

function formatAnalyticsDeadline(raw) {
  if (!raw) return "не указан";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return String(raw);
  return date.toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function ensureAnalyticsModal() {
  let dialog = document.querySelector("#analytics-modal");
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = "analytics-modal";
  dialog.className = "prompt-dialog analytics-modal";
  dialog.innerHTML =
    '<div class="prompt-dialog-shell">' +
    '<div class="prompt-dialog-header">' +
    '<h2 class="analytics-modal-title"></h2>' +
    '<button class="btn btn-ghost btn-sm" type="button" data-close>Закрыть</button>' +
    "</div>" +
    '<div class="analytics-modal-list"></div>' +
    "</div>";
  dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
  // <dialog> при закрытии возвращает фокус на колонку, с которой открыли,
  // и на SVG-rect остаётся обводка-«выделение». Снимаем фокус после закрытия.
  dialog.addEventListener("close", () => {
    const active = document.activeElement;
    if (active && typeof active.blur === "function") {
      active.blur();
    }
  });
  document.body.appendChild(dialog);
  return dialog;
}

function openAnalyticsItemsModal(label, items) {
  const dialog = ensureAnalyticsModal();
  dialog.querySelector(".analytics-modal-title").textContent = `${label} · ${items.length}`;
  const list = dialog.querySelector(".analytics-modal-list");
  list.innerHTML = "";

  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "analytics-modal-item";

    const head = document.createElement("div");
    head.className = "analytics-modal-item-head";
    if (item.url) {
      const link = document.createElement("a");
      link.className = "preview-link";
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = item.subject || "Поручение";
      head.appendChild(link);
    } else {
      const span = document.createElement("strong");
      span.textContent = item.subject || "Поручение";
      head.appendChild(span);
    }
    row.appendChild(head);

    const meta = document.createElement("div");
    meta.className = "analytics-modal-meta";
    meta.append(
      analyticsMetaPart("Статус", item.status || "—"),
      analyticsMetaPart("Срок", formatAnalyticsDeadline(item.deadline)),
      analyticsMetaPart("Ответственный", item.performer || "—")
    );
    row.appendChild(meta);

    const reportBody = document.createElement("div");
    reportBody.className = "analytics-report-body";

    if (item.id != null) {
      const reportBtn = document.createElement("button");
      reportBtn.type = "button";
      reportBtn.className = "btn btn-sm analytics-report-btn";
      reportBtn.textContent = "Отчёт";
      reportBtn.addEventListener("click", () => loadAnalyticsReport(item.id, reportBtn, reportBody));
      row.appendChild(reportBtn);
    }
    row.appendChild(reportBody);

    list.appendChild(row);
  });

  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }
}

function analyticsMetaPart(label, value) {
  const part = document.createElement("span");
  part.className = "analytics-modal-meta-part";
  const strong = document.createElement("span");
  strong.className = "analytics-modal-meta-label";
  strong.textContent = `${label}: `;
  part.appendChild(strong);
  part.append(value);
  return part;
}

async function loadAnalyticsReport(itemId, button, container) {
  // Переиспользуем чат-путь: вывод модели падает в модалку, а не в чат.
  button.disabled = true;
  container.classList.add("loading");
  container.textContent = "Готовлю отчёт...";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({message: `отчёт поручение #${itemId}`, history: []}),
    });
    const answer = await response.text();
    const parsed = parseAssistantResponse(answer);
    container.classList.remove("loading");
    container.innerHTML = "";
    if (looksLikeMarkdown(parsed.text)) {
      container.innerHTML = marked.parse(parsed.text);
    } else {
      container.textContent = parsed.text;
    }
  } catch (error) {
    container.classList.remove("loading");
    container.textContent = "Не удалось получить отчёт. Проверьте подключение.";
  } finally {
    button.disabled = false;
  }
}

function renderGaugeChart(chart) {
  const wrap = document.createElement("div");
  wrap.className = "analytics-chart analytics-gauge";

  const value = Math.max(0, Math.min(100, Number(chart.value) || 0));
  const size = 120;
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - value / 100);
  const color = value >= 80 ? toneColor("ok") : value >= 50 ? toneColor("due-soon") : toneColor("overdue");

  const svg = svgEl("svg", {viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "img"});
  svg.appendChild(
    svgEl("circle", {
      cx: size / 2, cy: size / 2, r: radius, fill: "none",
      "stroke-width": stroke, class: "analytics-gauge-track",
    })
  );
  const arc = svgEl("circle", {
    cx: size / 2, cy: size / 2, r: radius, fill: "none", "stroke-width": stroke,
    "stroke-dasharray": circumference, "stroke-dashoffset": offset, "stroke-linecap": "round",
    transform: `rotate(-90 ${size / 2} ${size / 2})`,
  });
  arc.setAttribute("stroke", color);
  svg.appendChild(arc);

  const valueText = svgEl("text", {
    x: size / 2, y: size / 2 + 6, "text-anchor": "middle", class: "analytics-gauge-value",
  });
  valueText.textContent = `${value}${chart.unit || ""}`;
  svg.appendChild(valueText);

  wrap.appendChild(svg);
  if (chart.title) {
    const caption = document.createElement("div");
    caption.className = "analytics-chart-title";
    caption.textContent = chart.title;
    wrap.appendChild(caption);
  }
  return wrap;
}

function renderAnalytics(analytics) {
  const block = document.createElement("div");
  block.className = "analytics-block";

  if (analytics.title) {
    const title = document.createElement("div");
    title.className = "analytics-block-title";
    title.textContent = analytics.title;
    block.appendChild(title);
  }
  if (analytics.subtitle) {
    const subtitle = document.createElement("div");
    subtitle.className = "analytics-block-subtitle";
    subtitle.textContent = analytics.subtitle;
    block.appendChild(subtitle);
  }

  const charts = Array.isArray(analytics.charts) ? analytics.charts : [];
  const grid = document.createElement("div");
  grid.className = "analytics-charts";
  charts.forEach((chart) => {
    if (chart.type === "gauge") {
      grid.appendChild(renderGaugeChart(chart));
    } else {
      grid.appendChild(renderBarChart(chart));
    }
  });
  block.appendChild(grid);
  return block;
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

  const doc = display.document;
  if (doc && (doc.name || doc.number)) {
    const docLabel = [doc.name, doc.number ? `№ ${doc.number}` : "", doc.date]
      .filter(Boolean).join(" · ");
    if (doc.url) {
      const docRow = document.createElement("div");
      docRow.className = "preview-row";
      const lab = document.createElement("span");
      lab.className = "preview-label";
      lab.textContent = "Документ";
      const link = document.createElement("a");
      link.className = "preview-link";
      link.href = doc.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = docLabel || "Документ";
      docRow.append(lab, link);
      rows.appendChild(docRow);
    } else {
      rows.appendChild(previewRow("Документ", docLabel));
    }
  }

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

function attachActionItemReportLinks(container) {
    container.querySelectorAll('a[href^="#action-item-"]').forEach((link) => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const id = link.getAttribute("href").replace("#action-item-", "");
            const text = `отчёт поручение #${id}`;
            chatInput.value = text;
            document.querySelector("#chat-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
        });
    });
}

function attachDocumentActionLinks(container) {
    container.querySelectorAll('a[href^="#document-"]').forEach((link) => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const id = link.getAttribute("href").replace("#document-", "");
            // Подставляем формулировку — пользователь дописывает текст и исполнителя, затем отправляет.
            chatInput.value = `Выдай поручение по документу #${id}: `;
            chatInput.focus();
        });
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
  attachActionItemReportLinks(assistantMessage);
  attachDocumentActionLinks(assistantMessage);
  if (parsedAnswer.analytics) {
    // Гистограмма идёт перед списком/разбором.
    assistantMessage.insertBefore(renderAnalytics(parsedAnswer.analytics), assistantMessage.firstChild);
  }
  if (parsedAnswer.preview?.type === "action_item" || parsedAnswer.preview?.type === "task") {
    renderActionItemPreview(parsedAnswer.preview, assistantMessage);
  }
  chatHistory.push({role: "user", content: text});
  chatHistory.push({role: "assistant", content: answer});
});

function applyPromptCollapsed(collapsed) {
  if (!promptPanel || !promptToggle) return;
  promptPanel.classList.toggle("collapsed", collapsed);
  promptToggle.textContent = collapsed ? "▸" : "▾";
  promptToggle.setAttribute("aria-expanded", String(!collapsed));
  promptToggle.setAttribute("aria-label", collapsed ? "Развернуть панель" : "Свернуть панель");
}

function setupPromptToggle() {
  if (!promptToggle || !promptPanel) return;
  applyPromptCollapsed(localStorage.getItem(promptCollapsedKey) === "1");
  promptToggle.addEventListener("click", () => {
    const collapsed = !promptPanel.classList.contains("collapsed");
    localStorage.setItem(promptCollapsedKey, collapsed ? "1" : "0");
    applyPromptCollapsed(collapsed);
  });
}

renderPromptChips();
setupPromptToggle();
loadStatus();
