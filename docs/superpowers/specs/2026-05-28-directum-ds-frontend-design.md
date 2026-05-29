# Spec: Переписать фронт под Directum DS (Platform.ОГВ)

- **Дата:** 2026-05-28
- **Проект:** MCP-Directum-RX
- **Автор:** Korotaev_NO + Claude (brainstorming)
- **Статус:** draft, ждёт ревью пользователя

---

## 1. Цель

Привести фронт прототипа MCP-Directum-RX (два экрана — `index.html` чат и `backoffice.html` метрики) в соответствие с **Directum Design System / Platform.ОГВ**, чтобы все прототипы (этот, а в будущем и другие) использовали единую визуальную систему.

UX и функциональность сохраняются: все кнопки, формы, диалоги, preview-карточки, prompt chips, чат — работают как сейчас. Меняются только стили, классы и обёртка разметки под DS-паттерны.

**Тема:** `data-theme="state-1"` (default Госуслуги-стиль, accent `#0B5FAE`).

---

## 2. Источники

- **Playbook:** `C:\Users\Korotaev_NO\Desktop\Obsidian vault\AI Agent Hub\playbooks\directum-ds-frontend.md`
- **Reference DS:** `C:\Users\Korotaev_NO\Desktop\Проекты\Class-OG-Final\design_handoff_platform_ogv\reference\`
  - `tokens.css` — цвета, размеры, тени (источник истины, не редактируем)
  - `styles.css` — компонентные стили (источник истины, не редактируем)
  - `CLAUDE.md`, `design_guide.md` — правила
  - `logo.svg` — Directum логотип

---

## 3. Архитектура CSS

**Подход A** — reference копируется как есть + отдельный кастомный слой:

```
src/static/
├── fonts/                ← НОВЫЙ: Inter .woff2 (5 weight: 400/500/600/700/800, latin+cyrillic)
├── fonts.css             ← НОВЫЙ: @font-face блоки (Inter)
├── tokens.css            ← НОВЫЙ: копия из reference (НЕ редактировать)
├── styles.css            ← НОВЫЙ: копия из reference (НЕ редактировать)
├── assistant.css         ← НОВЫЙ: кастомные стили чата/preview/диалога/форм бэкофиса
├── logo.svg              ← из reference (или текущий, если идентичен)
├── marked.min.js         ← НОВЫЙ: локальная копия (замена CDN)
├── index.html            ← переписан по DS-обёртке
├── backoffice.html       ← переписан по DS-обёртке + sidebar
├── app.js                ← минимальные правки (если выявятся при тестах)
└── backoffice.js         ← добавлен IntersectionObserver для активного nav
```

Удаляется: `src/static/style.css` (778 строк).

**Порядок подключения CSS в каждом HTML:**

```html
<link rel="stylesheet" href="/static/fonts.css?v=YYYYMMDD">
<link rel="stylesheet" href="/static/tokens.css?v=YYYYMMDD">
<link rel="stylesheet" href="/static/styles.css?v=YYYYMMDD">
<link rel="stylesheet" href="/static/assistant.css?v=YYYYMMDD">
```

`?v=YYYYMMDD` — cache-bust при изменениях. На момент первого деплоя — `v=20260528`.

---

## 4. Разметка `index.html`

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Платформа.ОГВ — Умный ассистент Directum RX</title>
  <link rel="stylesheet" href="/static/fonts.css?v=20260528">
  <link rel="stylesheet" href="/static/tokens.css?v=20260528">
  <link rel="stylesheet" href="/static/styles.css?v=20260528">
  <link rel="stylesheet" href="/static/assistant.css?v=20260528">
  <script src="/static/marked.min.js"></script>
</head>
<body data-theme="state-1">
  <header class="app-header">
    <a class="brand" href="/" aria-label="Directum">
      <img class="brand-logo" src="/static/logo.svg" alt="Directum">
    </a>
    <div class="app-title">
      <h1>Умный ассистент Directum RX</h1>
    </div>
    <a class="btn btn-ghost btn-sm" href="/backoffice">Backoffice</a>
  </header>

  <aside class="sidebar app-sidebar" aria-label="Directum RX">
    <div id="status" class="status">Проверка...</div>
    <nav class="nav-list" aria-label="Разделы Directum">
      <button class="nav-item active" type="button" data-action="my">Мои задания</button>
      <button class="nav-item" type="button" data-action="overdue">Просроченные</button>
      <button class="nav-item" type="button" data-action="assigned">Поручения мне</button>
      <button class="nav-item" type="button" data-action="created">Поручения от меня</button>
      <button class="nav-item" type="button" data-action="create">Создать поручение</button>
      <button class="nav-item" type="button" data-action="meetings">📅 Мои совещания</button>
    </nav>
    <section class="result-section" aria-label="Результаты Directum">
      <h2>Результаты</h2>
      <div id="results" class="result-list"></div>
    </section>
  </aside>

  <main class="appeal-shell chat-shell">
    <section class="prompt-panel" aria-label="Быстрые промпты">
      <div class="section-heading">
        <h2>Быстрые промпты</h2>
        <p>Шаблоны запросов к ассистенту</p>
      </div>
      <button class="btn btn-ghost btn-sm" id="prompt-settings" type="button">Настроить</button>
      <div id="prompt-chips" class="prompt-chips"></div>
    </section>

    <section class="appeal-workspace chat-workspace">
      <section id="messages" class="messages" aria-live="polite"></section>
      <form id="chat-form" class="chat-form">
        <label class="field chat-input-field">
          <span class="sr-only">Спросите про поручения</span>
          <input id="chat-input" type="text" placeholder="Спросите про поручения..." autocomplete="off">
        </label>
        <button class="btn" type="submit">Отправить</button>
      </form>
    </section>
  </main>

  <dialog id="prompt-dialog" class="prompt-dialog">
    <form method="dialog" class="prompt-dialog-shell">
      <div class="prompt-dialog-header">
        <h2>Настройка промптов</h2>
        <button class="btn btn-ghost btn-sm" value="cancel" type="submit">Закрыть</button>
      </div>
      <div id="prompt-editor-list" class="prompt-editor-list"></div>
      <div class="prompt-dialog-actions button-row">
        <button class="btn btn-ghost" id="prompt-add" type="button">Добавить</button>
        <button class="btn" id="prompt-save" value="default" type="submit">Сохранить</button>
      </div>
    </form>
  </dialog>

  <script src="/static/app.js?v=20260528"></script>
</body>
</html>
```

**Отступления от playbook:**
- Эмодзи `📅` сохранена в пункте «📅 Мои совещания» по явному решению пользователя (playbook §4 запрещает эмодзи как иконки). Зафиксировать в этом месте как осознанное исключение.

**Удалено:**
- `<p class="eyebrow">демонстрационный прототип</p>` — не используется в DS.
- `<span class="brand-fallback">Directum</span>` и `onerror` хак на `<img>` — DS поставляет валидный `logo.svg`, fallback не нужен.

---

## 5. Разметка `backoffice.html`

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Платформа.ОГВ — Backoffice</title>
  <link rel="stylesheet" href="/static/fonts.css?v=20260528">
  <link rel="stylesheet" href="/static/tokens.css?v=20260528">
  <link rel="stylesheet" href="/static/styles.css?v=20260528">
  <link rel="stylesheet" href="/static/assistant.css?v=20260528">
</head>
<body data-theme="state-1">
  <header class="app-header">
    <a class="brand" href="/" aria-label="Directum">
      <img class="brand-logo" src="/static/logo.svg" alt="Directum">
    </a>
    <div class="app-title"><h1>Backoffice</h1></div>
    <a class="btn btn-ghost btn-sm" href="/">Ассистент</a>
  </header>

  <aside class="sidebar app-sidebar" aria-label="Разделы backoffice">
    <nav class="nav-list" aria-label="Навигация">
      <a href="#metrics-section"    class="nav-item active">Метрики</a>
      <a href="#tool-calls-section" class="nav-item">Tool calls</a>
      <a href="#llm-conn-section"   class="nav-item">LLM connection</a>
      <a href="#rx-conn-section"    class="nav-item">Directum RX connection</a>
    </nav>
    <section class="result-section" aria-label="Статус">
      <h2>Статус</h2>
      <div class="result-list">
        <div class="result-card">Версия прототипа: 0.4.x</div>
      </div>
    </section>
  </aside>

  <main class="appeal-shell backoffice-shell">
    <section class="prompt-panel" aria-label="Сводка">
      <div class="section-heading">
        <h2>Метрики прототипа</h2>
        <p>Использование чата и tool calls Directum</p>
      </div>
    </section>

    <section class="appeal-workspace backoffice-workspace">

      <section id="metrics-section" class="kpi-grid" aria-label="Ключевые показатели">
        <article class="kpi accent-orange">
          <h2 class="kpi-label">Chat requests</h2>
          <p class="kpi-value" id="chat-requests">0</p>
        </article>
        <article class="kpi accent-amber">
          <h2 class="kpi-label">Preview</h2>
          <p class="kpi-value" id="previews">0</p>
        </article>
        <article class="kpi accent-green">
          <h2 class="kpi-label">Confirmed</h2>
          <p class="kpi-value" id="confirmed">0</p>
        </article>
        <article class="kpi accent-red">
          <h2 class="kpi-label">Errors</h2>
          <p class="kpi-value" id="errors">0</p>
        </article>
      </section>

      <section id="llm-conn-section" class="tool-section connection-panel">
        <div class="section-heading">
          <h2>LLM connection</h2>
          <p id="llm-status">Loading current model...</p>
        </div>
        <form id="llm-form" class="connection-form llm-form">
          <label class="field"><span>LLM provider</span>
            <select id="llm-provider" name="provider" required>
              <option value="ollama">Ollama</option>
              <option value="openrouter">OpenRouter</option>
              <option value="openai-compatible">OpenAI compatible</option>
              <option value="ario">Ario</option>
            </select>
          </label>
          <label class="field"><span>LLM base URL</span>
            <input id="llm-base-url" name="base_url" type="url" autocomplete="url" required>
          </label>
          <label class="field"><span>LLM API key</span>
            <input id="llm-api-key" name="api_key" type="password" autocomplete="off" placeholder="Keep current">
          </label>
          <label class="field"><span>LLM model</span>
            <input id="llm-model" name="model" required>
          </label>
          <label class="field"><span>Tool calling</span>
            <select id="llm-tool-calling" name="tool_calling" required>
              <option value="auto">Auto</option>
              <option value="enabled">Enabled</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <div class="button-row">
            <button class="btn btn-ghost" type="button" id="llm-test">Проверить</button>
            <button class="btn" type="submit">Применить</button>
          </div>
        </form>
      </section>

      <section id="rx-conn-section" class="tool-section connection-panel">
        <div class="section-heading">
          <h2>Directum RX connection</h2>
          <p id="rx-status">Loading current connection...</p>
        </div>
        <form id="connection-form" class="connection-form">
          <label class="field"><span>Server URL</span>
            <input id="rx-url" name="base_url" type="url" autocomplete="url" required>
          </label>
          <label class="field"><span>Login</span>
            <input id="rx-login" name="username" autocomplete="username" required>
          </label>
          <label class="field"><span>Password</span>
            <input id="rx-password" name="password" type="password" autocomplete="current-password" required>
          </label>
          <div class="button-row">
            <button class="btn btn-ghost" type="button" id="rx-test">Проверить</button>
            <button class="btn" type="submit">Применить</button>
          </div>
        </form>
      </section>

      <section id="tool-calls-section" class="tool-section">
        <div class="section-heading">
          <h2>Tool calls</h2>
          <p>Последние вызовы инструментов LLM</p>
        </div>
        <div id="tool-calls" class="result-list"></div>
      </section>

    </section>
  </main>

  <script src="/static/backoffice.js?v=20260528"></script>
</body>
</html>
```

**ID и классы**, на которые опирается `backoffice.js`, сохранены: `#llm-form`, `#connection-form`, `#llm-provider`, `#llm-base-url`, `#llm-api-key`, `#llm-model`, `#llm-tool-calling`, `#llm-status`, `#llm-test`, `#rx-url`, `#rx-login`, `#rx-password`, `#rx-status`, `#rx-test`, `#chat-requests`, `#previews`, `#confirmed`, `#errors`, `#tool-calls`.

---

## 6. Содержимое `assistant.css`

Только то, чего нет в reference DS. Все значения через `var(--…)` из `tokens.css`.

**Блоки:**

1. **Reset/utilities**
   - `html { scroll-behavior: smooth; }`
   - `.sr-only` — visually-hidden для accessible-имени чат-инпута

2. **Chat workspace (`.chat-workspace`, `.messages`, `.message`)**
   - `.messages` — flex column, gap 12, padding 16, скролл `overflow-y: auto`
   - `.message` — `border-radius: var(--radius)`, padding 12 16, font-size 14, line-height 1.5
   - `.message.user` — фон `var(--accent-soft)`, align right, max-width 70%
   - `.message.assistant` — фон `var(--surface-2)`, align left, max-width 80%
   - `.message.system` — фон `var(--amber-soft)`, центр, font-size 13

3. **Chat form (`.chat-form`, `.chat-input-field`)**
   - Sticky bottom, padding 16, border-top `1px solid var(--border)`, background `var(--surface)`
   - `display: grid; grid-template-columns: 1fr auto; gap: 12`
   - `.chat-input-field` — `.field` без uppercase span, padding 0
   - `.chat-input-field span` — скрыт через `.sr-only`

4. **Prompt chips (`.prompt-chips`, `.prompt-chip`)**
   - `.prompt-chips` — flex wrap, gap 8, margin-top 12
   - `.prompt-chip` — `border-radius: 999px`, `border: 1px solid var(--border)`, padding 6 14, font 12/500, background `var(--surface)`, color `var(--text)`, cursor pointer
   - `:hover` — background `var(--accent-soft)`, border-color `var(--accent)`
   - `:focus-visible` — outline 2px `var(--accent)`, outline-offset 2

5. **Prompt panel (`.prompt-panel`)**
   - Расширение DS-карточки. `display: grid; grid-template-columns: 1fr auto; align-items: start; gap: 12`
   - `#prompt-chips` занимает `grid-column: 1 / -1`

6. **Prompt dialog (`.prompt-dialog`, `.prompt-dialog-shell`, `.prompt-dialog-header`, `.prompt-editor-list`, `.prompt-editor-row`, `.prompt-delete`)**
   - `.prompt-dialog` — `border: 1px solid var(--border)`, `border-radius: var(--radius)`, padding 24, max-width 640, `box-shadow: var(--shadow-md)`
   - `::backdrop` — `rgba(1, 12, 28, .4)`
   - `.prompt-dialog-shell` — flex column gap 16
   - `.prompt-dialog-header` — flex space-between, align-items center
   - `.prompt-editor-list` — flex column gap 12, max-height 60vh, overflow-y auto
   - `.prompt-editor-row` — grid `1fr 2fr auto`, gap 8, padding 12, border `1px solid var(--border)`, border-radius `var(--radius-sm)`, background `var(--surface-2)`
   - `.prompt-text-field` — grid-column от 1 до -1 (если ряд маленький)
   - `.prompt-delete` — иконка удаления в правом верхнем углу строки

7. **Preview cards (`.preview-card`, `.preview-row`, `.preview-label`, `.preview-title`, `.preview-rows`, `.preview-actions`, `.preview-confirm`, `.preview-cancel`, `.preview-status`, `.preview-link`)**
   - `.preview-card` — `.card`-style (background `var(--surface)`, padding `var(--card-pad)`, border-radius `var(--radius)`, shadow-sm), плюс `border-left: 4px solid var(--amber)` как индикатор «требует подтверждения»
   - `.preview-title` — 15px / 700 / `var(--title)` / margin-bottom 12
   - `.preview-rows` — flex column gap 8, padding-block 12
   - `.preview-row` — grid `120px 1fr`, gap 12, font-size 13
   - `.preview-label` — `var(--muted)`, font 11px / 600 / uppercase / letter-spacing .04em
   - `.preview-actions` — `display: flex; gap: 10; justify-content: flex-end; margin-top: 16`
   - `.preview-confirm` — расширение `.btn` с зелёным акцентом: background `var(--green)`, border `var(--green)`, color `#fff`
   - `.preview-cancel` — `.btn.btn-ghost` через классы в HTML
   - `.preview-status` — alert-стиль: border-radius `var(--radius-sm)`, padding 10 14, font 13
     - `.success` — background `var(--green-soft)`, color `#1F7A1D`
     - `.error` — background `var(--red-soft)`, color `var(--red)`
   - `.preview-link` — `color: var(--accent); text-decoration: none; border-bottom: 1px solid transparent`, hover — border-color `var(--accent)`

8. **Sidebar дополнения (`.status`, `.result-section`, `.result-card.ok/.error`)**
   - `.status` — padding 8 12, margin 0 12 16, font 12, border-radius `var(--radius-sm)`
     - default — `var(--muted)`
     - `.status.ok` — `var(--green-soft)` / `#1F7A1D`
     - `.status.error` — `var(--red-soft)` / `var(--red)`
   - `.result-section h2` — uppercase 11 / 600 / `var(--muted)` / margin-top 24
   - `.result-card.ok` — `border-left: 3px solid var(--green)`
   - `.result-card.error` — `border-left: 3px solid var(--red)`

9. **Backoffice специфика (`.connection-panel`, `.connection-form`, `.backoffice-shell`)**
   - `.connection-form` — `display: grid; grid-template-columns: repeat(2, 1fr); gap: 14; margin-top: 16`
   - `.connection-form .button-row` — `grid-column: 1 / -1; justify-content: flex-end`
   - `.backoffice-shell .appeal-workspace` — `gap: 16` (override default 0)
   - `.kpi-grid` — `display: grid; grid-template-columns: repeat(4, 1fr); gap: 14` (если не покрыто в DS)

10. **Responsive (только desktop, без mobile drawer)**
    - `@media (max-width: 1200px) { .connection-form { grid-template-columns: 1fr; } .kpi-grid { grid-template-columns: repeat(2, 1fr); } }`

**Чего НЕТ:**
- Никаких hex-значений
- Никаких шрифтовых размеров вне DS-шкалы (11/12/12.5/13/14/15/16/18/22/26)
- Никаких теней кроме `var(--shadow-sm/md/hover)`
- Никаких новых анимаций (только `.spinner` из DS + `slideUp` для toast из DS)

Целевой объём: ~250–300 строк.

---

## 7. Шрифты — Inter локально

`src/static/fonts/` содержит `.woff2` для Inter в 5 weight'ах: 400, 500, 600, 700, 800. Cyrillic + latin subset (для русского UI). Источник: https://rsms.me/inter/ или https://gwfh.mranftl.com/fonts/inter.

`src/static/fonts.css`:

```css
@font-face {
  font-family: 'Inter';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url('/static/fonts/inter-400.woff2') format('woff2');
  unicode-range: U+0000-024F, U+0400-04FF;
}
/* + 500, 600, 700, 800 — аналогично */
```

---

## 8. Правки JS

### `app.js`
**Функциональных изменений нет.** Все ID и классы, используемые JS, сохранены в новой разметке.

**Проверка совместимости** (после рендера новой разметки):
- `document.querySelector("#chat-input")` находит инпут (теперь обёрнут в `<label class="field chat-input-field">`)
- `document.querySelector("#prompt-editor-list")` находит контейнер строк
- `new Event("submit", {bubbles: true, cancelable: true})` на `#chat-form` срабатывает (форма не изменена структурно)

Если выявится — точечный фикс.

### `backoffice.js`
**Добавляется ~15 строк** для подсветки активного `.nav-item` в sidebar при скролле:

```js
(function initSidebarActiveLink() {
  const sections = document.querySelectorAll("main section[id]");
  const links = document.querySelectorAll(".app-sidebar .nav-item[href^='#']");
  if (!sections.length || !links.length) return;
  const map = new Map(Array.from(links).map(l => [l.getAttribute("href").slice(1), l]));
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      const link = map.get(e.target.id);
      if (!link) return;
      if (e.isIntersecting) {
        links.forEach(l => l.classList.remove("active"));
        link.classList.add("active");
      }
    });
  }, { rootMargin: "-30% 0px -60% 0px" });
  sections.forEach(s => io.observe(s));
})();
```

`html { scroll-behavior: smooth; }` уже в `assistant.css`.

### `marked.min.js` локально
Скачиваем актуальную версию marked в `src/static/marked.min.js`. В `index.html` ссылка на CDN заменяется локальной: `<script src="/static/marked.min.js"></script>`.

---

## 9. Удаляется

- `src/static/style.css` — полностью (778 строк), функция перекрыта связкой `tokens.css` + `styles.css` + `assistant.css`.
- `src/static/index.html` — старый `<p class="eyebrow">демонстрационный прототип</p>`, `<span class="brand-fallback">Directum</span>`, `onerror` хак на `<img>` логотипа.
- CDN-ссылка на `https://cdn.jsdelivr.net/npm/marked/marked.min.js` — заменяется локальным файлом.

---

## 10. Тестирование

1. **Baseline (до изменений):**
   ```powershell
   & ".venv\Scripts\python.exe" -m pytest tests/e2e/ -v
   ```
   Зафиксировать список проходящих E2E.

2. **После каждого крупного этапа:**
   - Запуск приложения (`launch.bat`), визуальная проверка обоих экранов.
   - Открыть в DevTools console → ошибок нет.

3. **Финал:**
   - `pytest tests/ -v --cov=src --cov-report=term-missing` — все тесты проходят, покрытие ≥70%.
   - Playwright E2E — все тесты идут, упавшие селекторы починены.
   - Скриншоты обоих экранов в `tmp/screenshots/index-state1.png` и `tmp/screenshots/backoffice-state1.png` для документации результата.

4. **Чеклист DS compliance** (из playbook §9, перед коммитом):
   - [ ] `tokens.css` подключён перед `styles.css`
   - [ ] `data-theme="state-1"` на `<body>`
   - [ ] Inter weight 400–800 импортированы (локально)
   - [ ] Нет hex-значений цветов в `assistant.css`
   - [ ] Коды/ID — через `<span class="code">`
   - [ ] Иконки — inline SVG, stroke 1.7, round caps, viewBox 24×24 (исключение — эмодзи `📅` в «Мои совещания», явно согласовано)
   - [ ] Между `<textarea>` и кнопками — `.button-row`
   - [ ] Нет `style={{color: ...}}` или inline hex
   - [ ] Тени только через `var(--shadow-*)`
   - [ ] UX не изменён
   - [ ] Визуальная проверка обоих экранов в state-1

---

## 11. Порядок исполнения (этапы)

1. **Подготовка**
   - (по согласованию) создать ветку
   - Запустить baseline E2E, зафиксировать состояние
   - Скачать Inter `.woff2` (5 weight, latin+cyrillic) в `src/static/fonts/`
   - Скачать `marked.min.js` локально в `src/static/marked.min.js`

2. **Копирование DS**
   - `cp reference/tokens.css → src/static/tokens.css`
   - `cp reference/styles.css → src/static/styles.css`
   - `cp reference/logo.svg → src/static/logo.svg` (если отличается от текущего)
   - Создать `src/static/fonts.css` с `@font-face` блоками

3. **Удаление старого**
   - `rm src/static/style.css`

4. **Создать `assistant.css`** по плану из §6.

5. **Переписать `index.html`** по разметке §4.

6. **Переписать `backoffice.html`** по разметке §5.

7. **Правки JS**
   - `app.js` — проверить совместимость, минимальные точечные правки если выявятся
   - `backoffice.js` — добавить IntersectionObserver-блок

8. **Верификация**
   - `launch.bat` → визуальная проверка
   - Скриншоты в `tmp/screenshots/`
   - `pytest tests/ -v --cov=src` → ≥70%, все тесты идут
   - DS-compliance чеклист из §10.4

9. **Коммит** — по запросу пользователя.

---

## 12. Out of scope

- Тёмная тема (`state-3`) — не настраиваем в этой итерации.
- Mobile/tablet layout (drawer-sidebar) — desktop-only прототип.
- Изменения функциональности (новые tool calls, новые экраны) — не входят.
- Замена иконки `📅` на SVG в «Мои совещания» — оставлена эмодзи по решению пользователя.
- Sidebar в backoffice как tab-switcher (hide/show разделов) — реализуем как jump-to-anchor с активной подсветкой.

---

## 13. Open questions

(Нет — все вопросы закрыты в брейншторме.)

---

## 14. Связанные документы

- Playbook: `C:\Users\Korotaev_NO\Desktop\Obsidian vault\AI Agent Hub\playbooks\directum-ds-frontend.md`
- Reference DS: `C:\Users\Korotaev_NO\Desktop\Проекты\Class-OG-Final\design_handoff_platform_ogv\reference\`
- Проект CLAUDE.md: `C:\Users\Korotaev_NO\Desktop\Проекты\MCP-Directum-RX\CLAUDE.md`
