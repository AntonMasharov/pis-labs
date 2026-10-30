"use strict";

const root = document.querySelector("#root");
const notice = document.querySelector("#notice");
const statuses = {
  SUBMITTED: "Подана",
  UNDER_REVIEW: "На проверке",
  NEEDS_SUPPLEMENT: "Требует дополнения",
  COMPLETED: "Завершена",
};
const countries = [
  "Азербайджан",
  "Таджикистан",
  "Узбекистан",
  "Молдова",
  "Украина",
  "Другое",
];
const titles = {
  5.1: "Подтверждение знаний",
  5.2: "Цветные фотографии",
  5.3: "ИНН",
};
const state = {
  user: null,
  csrf: "",
  applications: [],
  mode: "login",
  challenge: null,
  phone: "",
};
let noticeTimer;
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const formatDate = (value) =>
  new Date(value).toLocaleString("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Yekaterinburg",
  });
const shortId = (value) => value.slice(0, 8).toUpperCase();
const badge = (status) =>
  `<span class="badge ${escapeHtml(status)}">${escapeHtml(statuses[status])}</span>`;
const brand =
  '<div class="brand"><span class="brand-icon">✓</span>Комплект</div>';

function deadlineHtml(application) {
  const overdue =
    application.status !== "COMPLETED" &&
    Date.now() > Date.parse(application.dueAt);
  const label = overdue ? "Срок истёк" : "Срок исполнения";
  return `<span class="${overdue ? "deadline-overdue" : ""}">${label}: до ${formatDate(application.dueAt)}</span>`;
}

function notify(message, success = false) {
  clearTimeout(noticeTimer);
  notice.textContent = message;
  notice.className = success ? "success" : "";
  notice.hidden = false;
  noticeTimer = setTimeout(
    () => {
      notice.hidden = true;
    },
    success ? 4500 : 10000,
  );
}

async function api(path, body) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.user) {
      state.user = null;
      authScreen();
    }
    throw new Error(result.error || "Не удалось выполнить запрос.");
  }
  return result;
}

function on(selector, event, handler) {
  const element = document.querySelector(selector);
  if (element)
    element.addEventListener(event, async (e) => {
      if (event === "submit") e.preventDefault();
      const buttons = [...element.querySelectorAll("button")];
      if (element.matches("button")) buttons.push(element);
      const disabledStates = buttons.map((button) => button.disabled);
      buttons.forEach((button) => {
        button.disabled = true;
      });
      try {
        await handler(e);
      } catch (error) {
        notify(error.message);
      } finally {
        buttons.forEach((button, index) => {
          button.disabled = disabledStates[index];
        });
      }
    });
}

function authScreen() {
  const register = state.mode === "register";
  root.innerHTML = `<div class="auth-layout"><section class="auth-story">${brand}<div>
    <div class="eyebrow">Подготовка документов для патента</div>
    <h1>Каждый документ.<br>На своём месте.</h1>
    <p>Подготовьте комплект, отправьте его специалисту и получите заключение о комплектности.</p>
    <div class="steps"><div class="step"><b>01 / Анкета</b>Индивидуальный перечень</div><div class="step"><b>02 / Проверка</b>Помощь специалиста</div><div class="step"><b>03 / Результат</b>Заключение и история</div></div>
    </div><footer>Учебный сервис · ПИС · Раздел 5<br>Экзамен, получение ИНН и выдача патента выполняются вне сервиса.</footer></section>
    <section class="auth-card"><div><div class="eyebrow">Личный кабинет</div><h2>${state.challenge ? "Подтвердите телефон" : "Добро пожаловать"}</h2>
    <p class="muted">${state.challenge ? `Код для ${escapeHtml(state.phone)}` : "Войдите, чтобы продолжить подготовку комплекта."}</p>
    ${
      !state.challenge
        ? `<div class="tabs"><button id="login-tab" class="${!register ? "active" : ""}" type="button">Вход</button><button id="register-tab" class="${register ? "active" : ""}" type="button">Регистрация</button></div>
    <form id="phone-form"><label>Номер телефона<input id="phone" type="tel" placeholder="+7 999 123-45-67" autocomplete="tel" required value="${escapeHtml(state.phone || "")}"></label><button class="submit">Получить SMS-код</button></form>`
        : `<div class="callout success">Учебный код: <strong>123456</strong><br><small>Действует 5 минут. SMS не отправляется.</small></div><form id="code-form"><label>Код из SMS<input id="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6 цифр" required></label><button class="submit">${register ? "Зарегистрироваться" : "Войти"}</button></form><div class="actions"><button id="change-phone" class="link-button">Изменить телефон / запросить код заново</button></div>`
    }
    <div class="help">${register ? "Регистрация создаёт кабинет заявителя." : "Специалист входит по заранее созданному номеру."}<br>Условия сервиса соответствуют учебному заданию.</div></div></section></div>`;
  on("#login-tab", "click", () => {
    state.mode = "login";
    authScreen();
  });
  on("#register-tab", "click", () => {
    state.mode = "register";
    authScreen();
  });
  on("#change-phone", "click", () => {
    state.challenge = null;
    authScreen();
  });
  on("#phone-form", "submit", async () => {
    state.phone = document.querySelector("#phone").value;
    const result = await api("/auth/code", {
      phone: state.phone,
      mode: state.mode,
    });
    state.challenge = result.challengeId;
    authScreen();
    document.querySelector("#code").focus();
  });
  on("#code-form", "submit", async () => {
    await api("/auth/verify", {
      challengeId: state.challenge,
      code: document.querySelector("#code").value,
    });
    state.challenge = null;
    await bootstrap();
  });
}

function shell(content, active = "all") {
  const specialist = state.user.role === "SPECIALIST";
  root.innerHTML = `<div class="layout"><aside class="sidebar">${brand}<div class="eyebrow">${specialist ? "Рабочий кабинет" : "Личный кабинет"}</div>
    <nav><a href="#" class="${active === "all" ? "active" : ""}">${specialist ? "Очередь заявок" : "Мои заявки"}</a>${!specialist ? `<a href="#new" class="${active === "new" ? "active" : ""}">Подать заявку</a>` : ""}<a href="#completed" class="${active === "completed" ? "active" : ""}">Оказанные услуги</a></nav>
    <div class="side-foot">УЧЕБНЫЙ ПРОЕКТ<strong>Подготовка документов<br>для патента · Раздел 5</strong><br>Команда «ААА» · ПИС</div></aside>
    <main class="main"><header class="topbar"><span>Подготовка документов для патента</span><div class="user"><span class="avatar">${specialist ? "С" : "З"}</span><span>${escapeHtml(state.user.phone)}<br><small>${specialist ? "Специалист по проверке комплекта" : "Заявитель"}</small></span><button id="logout" class="secondary">Выйти</button></div></header><div class="content">${content}</div></main></div>`;
  on("#logout", "click", async () => {
    await api("/logout", {});
    state.user = null;
    state.mode = "login";
    state.challenge = null;
    state.phone = "";
    location.hash = "";
    authScreen();
  });
}

async function dashboard(completed = false) {
  const specialist = state.user.role === "SPECIALIST";
  const result = await api("/applications");
  state.applications = result.applications;
  const all = state.applications;
  const shown = all.filter((a) =>
    completed
      ? a.status === "COMPLETED"
      : !specialist || a.status !== "COMPLETED",
  );
  shell(
    `<div class="page-head"><div><div class="eyebrow">${specialist ? "Проверка комплектов" : "Ваши документы"}</div><h1>${completed ? "Оказанные услуги" : specialist ? "Очередь заявок" : "Мои заявки"}</h1><p class="muted">${completed ? "Завершённые обращения и выданные заключения." : specialist ? "Проверьте документы и помогите заявителю собрать полный комплект." : "Все обращения, документы и результаты — в одном кабинете."}</p></div><button id="refresh" class="secondary">Обновить</button></div>
    ${!specialist && !completed ? `<section class="panel hero"><div class="eyebrow">Одна услуга · Три пункта</div><h2>Подготовьте комплект для патента</h2><p>Сертификат о знаниях, цветные фотографии и ИНН. Заполните анкету — мы подскажем, что нужно именно вам.</p><a class="button" href="#new">Начать подготовку <span>→</span></a><div class="steps"><div class="step"><b>01 / Заполните анкету</b>Определим перечень</div><div class="step"><b>02 / Приложите документы</b>Специалист проверит комплект</div><div class="step"><b>03 / Получите заключение</b>Результат сохранится в кабинете</div></div></section>` : ""}
    <div class="stats"><div class="stat"><b>${all.filter((a) => a.status !== "COMPLETED").length}</b><span>В работе</span></div><div class="stat"><b>${all.filter((a) => a.status === "NEEDS_SUPPLEMENT").length}</b><span>Требуют дополнения</span></div><div class="stat"><b>${all.filter((a) => a.status === "COMPLETED").length}</b><span>Завершены</span></div></div>
    <section class="panel"><h2>${completed ? "Архив результатов" : specialist ? "Ожидают действий" : "Обращения"}</h2>${shown.length ? shown.map((a) => `<article class="list-item"><div><h3><a href="#application/${a.id}">Подготовка комплекта · № ${shortId(a.id)}</a></h3><div class="meta">Подана ${formatDate(a.submittedAt)}<br>${deadlineHtml(a)}${specialist ? " · " + escapeHtml(a.applicantPhone) : ""}</div></div><div class="right">${badge(a.status)}<a href="#application/${a.id}">Открыть →</a></div></article>`).join("") : `<div class="empty"><b>${completed ? "Завершённых услуг пока нет" : "Заявок пока нет"}</b><span class="muted">${specialist ? "Новые обращения появятся здесь после подачи." : completed ? "Выданное заключение появится здесь после проверки." : "Начните с анкеты: это займёт несколько минут."}</span></div>`}</section>`,
    completed ? "completed" : "all",
  );
  on("#refresh", "click", () => dashboard(completed));
}

function questionnaire(q = {}) {
  return `<fieldset><legend>Анкета заявителя</legend><label>Гражданство<select id="citizenship" required><option value="">Выберите гражданство</option>${countries.map((c) => `<option ${q.citizenship === c ? "selected" : ""}>${c}</option>`).join("")}</select></label>
    <label class="check"><input id="isHighlyQualified" type="checkbox" ${q.isHighlyQualified ? "checked" : ""}>Я высококвалифицированный специалист</label>
    <label class="check"><input id="isResettlementParticipant" type="checkbox" ${q.isResettlementParticipant ? "checked" : ""}>Я участник программы переселения</label>
    <label class="check"><input id="hasLanguageDocument" type="checkbox" ${q.hasLanguageDocument ? "checked" : ""}>Есть документ, подтверждающий знание языка, истории и законодательства</label>
    <label class="check"><input id="hasInn" type="checkbox" ${q.hasInn ? "checked" : ""}>У меня уже есть ИНН</label>
    <label id="inn-label" ${q.hasInn ? "" : "hidden"}>Номер ИНН<input id="inn" inputmode="numeric" maxlength="12" pattern="[0-9]{12}" value="${escapeHtml(q.inn || "")}" ${q.hasInn ? "required" : ""}><small>12 цифр. Отдельный файл ИНН не обязателен.</small></label></fieldset>`;
}

function readQuestionnaire() {
  const q = {
    citizenship: document.querySelector("#citizenship").value,
    inn: "",
  };
  for (const key of [
    "isHighlyQualified",
    "isResettlementParticipant",
    "hasLanguageDocument",
    "hasInn",
  ])
    q[key] = document.querySelector("#" + key).checked;
  if (q.hasInn) q.inn = document.querySelector("#inn").value.trim();
  return q;
}

function uploads(version) {
  return `<fieldset><legend>Документы</legend><p class="muted">PDF, PNG или JPEG. До 5 МБ на файл, до 12 файлов и 15 МБ на версию. Недостающие документы можно предоставить после запроса специалиста.</p>
  ${version && version.documents.length ? `<h3>Сохранить файлы предыдущей версии</h3>${version.documents.map((d) => `<label class="check"><input type="checkbox" name="keep" value="${d.id}" checked>${escapeHtml(d.code)} · ${escapeHtml(d.fileName)}</label>`).join("")}<hr>` : ""}
  ${Object.keys(titles)
    .map(
      (c) =>
        `<label>${c} · ${titles[c]}<input id="file-${c.replace(".", "")}" type="file" multiple accept=".pdf,.png,.jpg,.jpeg"><small>${c === "5.1" ? "Сертификат или имеющийся документ о знаниях." : c === "5.2" ? "Цветные фотографии формата 3 × 4 см." : "Необязательное вложение."}</small></label>`,
    )
    .join("")}</fieldset>`;
}

function readFileContent(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("Не удалось прочитать файл."));
    reader.readAsDataURL(file);
  });
}

class SupplementForm {
  constructor(application = null) {
    this.application = application;
  }

  async submit() {
    const questionnaire = readQuestionnaire();
    const newDocuments = [];
    for (const code of Object.keys(titles)) {
      const files = [
        ...document.querySelector("#file-" + code.replace(".", "")).files,
      ];
      for (const file of files) {
        if (!file.size || file.size > 5 * 1024 * 1024)
          throw new Error("Файл пуст или больше 5 МБ: " + file.name);
        const content = await readFileContent(file);
        const kind =
          code === "5.1"
            ? questionnaire.hasLanguageDocument
              ? "Документ о знаниях"
              : "Сертификат"
            : code === "5.2"
              ? "Фото 3 × 4 см"
              : "Документ ИНН";
        newDocuments.push({
          code,
          kind,
          fileName: file.name,
          mediaType: file.type,
          content,
        });
      }
    }
    const command = {
      questionnaire,
      newDocuments,
      selectedDocumentIds: [
        ...document.querySelectorAll("[name=keep]:checked"),
      ].map((e) => e.value),
    };
    let path = "/applications";
    if (this.application) {
      command.sourceVersionId = this.application.latest.id;
      command.requestId = this.application.request.id;
      path += `/${this.application.id}/supplement`;
    }
    const result = await api(path, command);
    this.showResult(result);
  }

  showResult(result) {
    notify(
      this.application
        ? "Дополнение принято. Создана новая версия."
        : "Заявка принята и доступна специалисту.",
      true,
    );
    location.hash = "application/" + result.id;
  }
}

function applicationForm(a = null) {
  const form = new SupplementForm(a);
  const q = a ? a.latest.questionnaire : {};
  shell(
    `<div class="page-head"><div><div class="eyebrow">${a ? "Ответ специалисту" : "Новая услуга"}</div><h1>${a ? "Дополнить заявку" : "Подготовка комплекта"}</h1><p class="muted">${a ? "Новая версия сохранится вместе с предыдущими." : "Заполните анкету и приложите доступные документы."}</p></div><a href="${a ? "#application/" + a.id : "#"}">← Назад</a></div>
    ${a ? `<div class="callout"><strong>Запрос специалиста</strong><br>${escapeHtml(a.request.instructions)}</div>` : ""}
    <div class="grid"><form id="application-form"><section class="panel">${questionnaire(q)}<div class="actions"><button id="calculate" class="secondary" type="button">Показать индивидуальный перечень</button></div><div id="calculated" aria-live="polite"></div></section><section class="panel">${uploads(a?.latest)}<div class="actions"><button id="submit-application">${a ? "Отправить дополнение" : "Подать заявку"}</button><a href="${a ? "#application/" + a.id : "#"}">Отмена</a></div></section></form>
    <aside><section class="panel aside-note"><div class="eyebrow">Как это работает</div><h3>Срок исполнения</h3><p>Учебный срок — 3 календарных дня с подачи, включая время дополнения. Дополнение не переносит срок. Конкретная дата появится после подачи.</p><h3>Проверяем комплектность</h3><p>Специалист сопоставит сведения и файлы с вашим перечнем. При недостатках вы получите запрос дополнения.</p><h3>Результат услуги</h3><p>Заключение о комплектности документов. Выдача патента в услугу не входит.</p><h3>Учебные условия</h3><p>Раздел 5 для граждан пяти указанных стран без статуса ВКС и участника программы переселения.</p></section></aside></div>`,
    "new",
  );
  on("#hasInn", "change", () => {
    const enabled = document.querySelector("#hasInn").checked;
    document.querySelector("#inn-label").hidden = !enabled;
    document.querySelector("#inn").required = enabled;
    if (!enabled) document.querySelector("#inn").value = "";
  });
  document.querySelector("#application-form").addEventListener("change", () => {
    document.querySelector("#calculated").innerHTML = "";
  });
  on("#calculate", "click", async () => {
    const result = await api("/checklist", {
      questionnaire: readQuestionnaire(),
    });
    document.querySelector("#calculated").innerHTML =
      "<hr>" + checklistHtml(result.checklist);
  });
  on("#application-form", "submit", () => form.submit());
}

function checklistHtml(items) {
  return items
    .map(
      (i) =>
        `<div class="rule"><div class="rule-code">${escapeHtml(i.code)}</div><div><h3>${escapeHtml(i.requiredAction)}</h3><p>${escapeHtml(i.basis)}</p></div></div>`,
    )
    .join("");
}

function versionHtml(v) {
  const q = v.questionnaire;
  return `<dl class="facts"><div><dt>Гражданство</dt><dd>${escapeHtml(q.citizenship)}</dd></div><div><dt>ИНН</dt><dd>${q.hasInn ? escapeHtml(q.inn) : "Отсутствует"}</dd></div><div><dt>Документ о знаниях</dt><dd>${q.hasLanguageDocument ? "Есть" : "Нужно получить сертификат"}</dd></div><div><dt>Особые статусы</dt><dd>ВКС: ${q.isHighlyQualified ? "да" : "нет"} · Переселение: ${q.isResettlementParticipant ? "да" : "нет"}</dd></div></dl>
    ${checklistHtml(v.checklist)}<h3 class="actions">Приложенные документы</h3>${v.documents.length ? v.documents.map((d) => `<div class="file"><span class="rule-code">${escapeHtml(d.code)}</span><div><a href="/api/documents/${d.id}">${escapeHtml(d.fileName)}</a><br><small>${escapeHtml(d.kind)} · ${Math.ceil(d.size / 1024)} КБ</small></div></div>`).join("") : '<p class="muted">Файлы пока не приложены.</p>'}`;
}

function reviewHtml(a) {
  const r = a.review;
  if (a.status !== "UNDER_REVIEW" || r.specialistId !== state.user.id)
    return "";
  return `<section class="panel"><h2>Оценка комплектности</h2><p class="muted">Можно сохранить частичную оценку. Перед решением оцените все три пункта.</p><form id="assessment-form">
    ${Object.keys(titles)
      .map((code) => {
        const f = r.findings.find((f) => f.code === code) || {};
        return `<div class="finding"><h3>${code} · ${titles[code]}</h3><div class="columns"><label>Оценка<select name="assessment-${code}"><option value="">Не оценено</option><option value="CONFORMS" ${f.assessment === "CONFORMS" ? "selected" : ""}>Соответствует</option><option value="NEEDS_CORRECTION" ${f.assessment === "NEEDS_CORRECTION" ? "selected" : ""}>Требует исправления</option></select></label><label>Комментарий<textarea name="comment-${code}" maxlength="2000" placeholder="Опишите недостаток или подтвердите соответствие">${escapeHtml(f.comment || "")}</textarea></label></div></div>`;
      })
      .join("")}
    <div class="actions"><button>Сохранить оценки</button><small>Статус останется «На проверке»</small></div></form></section>
    <section class="panel"><h2>Решение специалиста</h2><p class="muted">Решение использует последние сохранённые оценки. Сначала сохраните изменения выше.</p><form id="decision-form"><label>Инструкции или текст заключения<textarea id="decision-text" required maxlength="4000" placeholder="Укажите, что исправить, или сформулируйте заключение о комплектности"></textarea></label><div class="actions"><button name="decision" value="request" class="danger">Запросить дополнение</button><button name="decision" value="complete">Выдать заключение</button></div></form></section>`;
}

function archivedReviewHtml(review) {
  const findings = review.findings
    .map(
      (finding) => `<p class="preserve">
      <strong>${escapeHtml(finding.code)} · ${assessmentLabel(finding.assessment)}</strong><br>${escapeHtml(finding.comment)}</p>`,
    )
    .join("");
  const request = review.request
    ? `<div class="callout"><strong>Запрос дополнения</strong><br>${escapeHtml(review.request.instructions)}</div>`
    : "";
  return `<h3 class="actions">Проверка · ${escapeHtml(review.specialistPhone)}</h3>${findings}${request}`;
}

function previousVersionsHtml(versions) {
  if (versions.length < 2) return "";
  const items = versions
    .slice(0, -1)
    .reverse()
    .map(
      (version) => `
    <details>
      <summary>Версия ${version.number} · ${formatDate(version.submittedAt)}</summary><br>
      ${versionHtml(version)}
      ${version.reviews.map(archivedReviewHtml).join("")}
    </details>`,
    )
    .join("");
  return `<section class="panel"><h2>Предыдущие версии</h2>${items}</section>`;
}

function assessmentLabel(assessment) {
  return assessment === "CONFORMS" ? "Соответствует" : "Требует исправления";
}

async function applicationDetail(id) {
  const a = await api("/applications/" + id);
  const specialist = state.user.role === "SPECIALIST";
  const assessments = a.latest.reviews.flatMap((r) => r.findings);
  shell(`<div class="page-head"><div><div class="eyebrow">Заявка № ${shortId(a.id)}</div><h1>Подготовка комплекта</h1><p class="muted">Подана ${formatDate(a.submittedAt)}<br>${deadlineHtml(a)}${specialist ? " · " + escapeHtml(a.applicantPhone) : ""}</p></div>${badge(a.status)}</div>
    ${a.status === "NEEDS_SUPPLEMENT" ? `<div class="callout"><strong>Требуется дополнение</strong><p>${escapeHtml(a.request.instructions)}</p>${!specialist ? `<a class="button" href="#supplement/${a.id}">Дополнить заявку →</a>` : ""}</div>` : ""}
    ${a.conclusion ? `<div class="callout success"><div class="eyebrow">Услуга завершена</div><h2>Заключение о комплектности</h2><p>${escapeHtml(a.conclusion.conclusionText)}</p><small>Выдано ${formatDate(a.conclusion.issuedAt)} · № ${shortId(a.conclusion.id)} · Версия ${a.latest.number}</small></div>` : ""}
    <p class="muted">Учебный срок — 3 календарных дня с подачи заявки, включая время дополнения. Дополнение не переносит срок. Время — Екатеринбург (UTC+5).</p>
    <div class="grid"><div><section class="panel"><div class="version-head"><h2>Анкета и документы</h2><span class="badge">Версия ${a.latest.number}</span></div>${versionHtml(a.latest)}</section>
    ${specialist && a.status === "SUBMITTED" ? '<section class="panel"><h2>Начните проверку</h2><p class="muted">Заявка будет закреплена за вами до решения по этой версии.</p><button id="start-review">Начать проверку</button></section>' : ""}
    ${specialist ? reviewHtml(a) : ""}
    ${assessments.length ? `<section class="panel"><h2>Сохранённые оценки</h2>${assessments.map((f) => `<div class="finding"><strong>${escapeHtml(f.code)} · ${assessmentLabel(f.assessment)}</strong><p class="preserve muted">${escapeHtml(f.comment || "Без комментария")}</p></div>`).join("")}</section>` : ""}
    ${previousVersionsHtml(a.versions)}</div>
    <aside><section class="panel"><h2>История статусов</h2><ol class="timeline">${a.history.map((e) => `<li><b>${escapeHtml(statuses[e.newStatus])}</b><small>${formatDate(e.occurredAt)}<br>${e.actorRole === "SPECIALIST" ? "Специалист" : "Заявитель"} · ${escapeHtml(e.actorPhone)}</small><p>${escapeHtml(e.comment)}</p></li>`).join("")}</ol></section><button id="refresh-detail" class="secondary">Обновить заявку</button></aside></div>`);
  const command = () => ({
    sourceVersionId: a.latest.id,
    revision: a.review?.revision,
  });
  on("#refresh-detail", "click", () => applicationDetail(id));
  on("#start-review", "click", async () => {
    await api("/applications/" + id + "/start", command());
    await applicationDetail(id);
    notify("Проверка начата.", true);
  });
  on("#assessment-form", "submit", async () => {
    const form = document.querySelector("#assessment-form");
    const findings = Object.keys(titles)
      .filter((c) => form.elements["assessment-" + c].value)
      .map((code) => ({
        code,
        assessment: form.elements["assessment-" + code].value,
        comment: form.elements["comment-" + code].value,
      }));
    await api("/applications/" + id + "/assess", { ...command(), findings });
    await applicationDetail(id);
    notify("Оценки сохранены. Решение ещё не принято.", true);
  });
  on("#decision-form", "submit", async (e) => {
    const action = e.submitter.value;
    await api("/applications/" + id + "/" + action, {
      ...command(),
      text: document.querySelector("#decision-text").value,
    });
    await applicationDetail(id);
    notify(
      action === "request"
        ? "Запрос направлен заявителю."
        : "Заключение выдано.",
      true,
    );
  });
}

async function route() {
  if (!state.user) return authScreen();
  const [page, id] = location.hash.slice(1).split("/");
  try {
    if (page === "application" && id) return await applicationDetail(id);
    if (page === "supplement" && id && state.user.role === "APPLICANT") {
      const a = await api("/applications/" + id);
      if (a.status !== "NEEDS_SUPPLEMENT")
        throw new Error(
          "Дополнение сейчас недоступно. Откройте актуальную заявку.",
        );
      return applicationForm(a);
    }
    if (page === "new" && state.user.role === "APPLICANT")
      return applicationForm();
    await dashboard(page === "completed");
  } catch (error) {
    if (state.user)
      shell(
        '<section class="panel"><h1>Не удалось открыть страницу</h1><p>' +
          escapeHtml(error.message) +
          '</p><a href="#">К списку заявок →</a></section>',
      );
    notify(error.message);
  }
}

async function bootstrap() {
  try {
    const data = await api("/me");
    state.user = data.user;
    state.csrf = data.csrf;
    await route();
  } catch {
    state.user = null;
    authScreen();
  }
}
window.addEventListener("hashchange", route);
bootstrap();
