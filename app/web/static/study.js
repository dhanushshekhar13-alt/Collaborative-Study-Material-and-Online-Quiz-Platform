// StudyHub web UI, card UI-2 (#63): study materials, the question bank, building and
// publishing quizzes, attempting them with a timer, results and My Quiz History.
//
// Each page registers itself in window.StudyHubPages under its first hash segment;
// app.js's router calls it as page(ui, restOfPath, queryParams) with the shared
// helpers (h, render, alertBox, show, field, busy, go, ...). As everywhere in the UI,
// server text goes in with textContent only, and every message comes from the API.
"use strict";

(() => {
  const pages = (window.StudyHubPages = window.StudyHubPages || {});

  const MATERIAL_TYPES = [
    ["notes", "Notes"],
    ["pdf", "PDF"],
    ["presentation", "Presentation"],
    ["link", "Link"],
    ["other", "Other"],
  ];
  const QUESTION_TYPES = [
    ["multiple_choice", "Multiple choice"],
    ["true_false", "True/False"],
    ["short_answer", "Short answer"],
    ["descriptive", "Descriptive"],
  ];
  const DIFFICULTIES = [
    ["easy", "Easy"],
    ["medium", "Medium"],
    ["hard", "Hard"],
  ];
  const REPORT_REASONS = [
    ["duplicate", "Duplicate content"],
    ["incorrect", "Incorrect answer or information"],
    ["inappropriate", "Inappropriate"],
    ["misleading", "Misleading"],
    ["spam", "Spam"],
    ["other", "Other"],
  ];
  const ANSWER_STATUS = {
    correct: "Correct",
    incorrect: "Incorrect",
    unattempted: "Unattempted",
    pending_evaluation: "Waiting for evaluation",
    evaluated: "Evaluated",
  };
  const label = (pairs, value) => (pairs.find(([v]) => v === value) || [value, value])[1];

  // ------------------------------------------------------------- helpers ----

  function options(h, pairs, placeholder) {
    return [
      placeholder !== undefined ? h("option", { value: "" }, placeholder) : null,
      ...pairs.map(([value, text]) => h("option", { value }, text)),
    ];
  }

  function blankToNull(value) {
    const text = (value || "").trim();
    return text ? text : null;
  }

  // The caller's channels, for "post to" and "scope" pickers.
  async function myChannels() {
    try {
      return await API.get("/channels/mine");
    } catch {
      return [];
    }
  }

  function channelSelect(h, channels, name, generalLabel, selected) {
    const select = h(
      "select",
      { name },
      h("option", { value: "" }, generalLabel),
      channels.map((c) => h("option", { value: String(c.id) }, `${c.name} (${c.type_label})`)),
    );
    // A channel the caller has not joined is not in the list, so it falls back to "".
    select.value = selected ? String(selected) : "";
    return select;
  }

  function stars(value) {
    if (value === null || value === undefined) return "No ratings yet";
    return `★ ${Number(value).toFixed(1)}`;
  }

  function when(iso) {
    return iso ? new Date(iso).toLocaleString() : "—";
  }

  function clock(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "material";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // ----------------------------------------------------------- materials ----

  function materialRow(ui, m) {
    const { h } = ui;
    return h(
      "li",
      { class: "item material", "data-id": String(m.id) },
      h(
        "div",
        {},
        h("a", { href: `#/materials/${m.id}`, class: "item-title" }, m.title),
        " ",
        h("span", { class: `badge ${m.content_style}` }, m.content_label),
        h("div", { class: "tags" }, m.tags.map((t) => h("span", { class: "badge tag" }, t))),
        h(
          "div",
          { class: "muted" },
          [
            m.uploader_name ? `by ${m.uploader_name}` : null,
            m.rating_count ? `${stars(m.rating_average)} (${m.rating_count})` : "No ratings yet",
            `${m.helpful_count} found helpful`,
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      ),
      h("div", { class: "row-actions" }, h("a", { href: `#/materials/${m.id}`, class: "button secondary" }, "View")),
    );
  }

  function uploadForm(ui, channels, presetChannel, onUploaded) {
    const { h, field, alertBox, show, busy } = ui;
    const status = alertBox();
    const type = h("select", { name: "material_type" }, options(h, MATERIAL_TYPES, "Select a type"));
    const file = h("input", { name: "file", type: "file", accept: ".pdf,.doc,.docx,.ppt,.pptx,.txt,.md,.png,.jpg,.jpeg" });
    const url = h("input", { name: "url", type: "url", maxlength: 1000, placeholder: "https://" });
    const fileField = field("File", file, "PDF, Word, PowerPoint, text, PNG or JPEG");
    const urlField = field("Link URL", url);
    urlField.hidden = true;
    type.addEventListener("change", () => {
      fileField.hidden = type.value === "link";
      urlField.hidden = type.value !== "link";
    });
    const form = h(
      "form",
      { class: "card upload-material", novalidate: true },
      h("h2", {}, "Upload Material"),
      status,
      field("Title", h("input", { name: "title", maxlength: 200 }), "Optional: the file name is used if blank"),
      field("Material type", type),
      fileField,
      urlField,
      field("Subject", h("input", { name: "subject", maxlength: 100 })),
      field("Topic", h("input", { name: "topic", maxlength: 100 })),
      field("Semester", h("input", { name: "semester", maxlength: 20 })),
      field("Share in", channelSelect(h, channels, "channel_id", "General (everyone)", presetChannel)),
      field("Description", h("textarea", { name: "description", maxlength: 5000, rows: 3 })),
      h("button", { type: "submit" }, "Upload"),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button[type=submit]"), async () => {
        const body = new FormData();
        for (const key of ["title", "material_type", "subject", "topic", "semester", "channel_id", "description"]) {
          const value = blankToNull(form.elements[key].value);
          if (value !== null) body.append(key, value);
        }
        if (type.value === "link") {
          const link = blankToNull(url.value);
          if (link) body.append("url", link);
        } else if (file.files.length) {
          body.append("file", file.files[0]);
        }
        try {
          const created = await API.upload("/materials", body);
          form.reset();
          fileField.hidden = false;
          urlField.hidden = true;
          show(status, `Uploaded "${created.title}"`, "success");
          if (onUploaded) onUploaded(created);
        } catch (err) {
          show(status, err.message, "error"); // e.g. "Subject is required"
        }
      });
    });
    return form;
  }

  pages.materials = async (ui, rest, params) => {
    if (rest[0]) return materialPage(ui, encodeURIComponent(rest[0]));
    const { h, render, alertBox, show } = ui;
    const channels = await myChannels();
    const scope = params.get("channel");
    const list = h("ul", { class: "items" });
    const status = alertBox();
    const total = h("p", { class: "muted result-count" });
    const q = h("input", { name: "q", type: "search", placeholder: "Keyword", maxlength: 100, value: params.get("q") || "" });
    const subject = h("input", { name: "subject", placeholder: "Subject", maxlength: 100, value: params.get("subject") || "" });
    const topic = h("input", { name: "topic", placeholder: "Topic", maxlength: 100, value: params.get("topic") || "" });
    const search = h("form", { class: "search", role: "search" }, q, subject, topic, h("button", { type: "submit" }, "Search"));

    async function load() {
      const query = new URLSearchParams({ limit: "50" });
      if (q.value.trim()) query.set("q", q.value.trim());
      if (subject.value.trim()) query.set("subject", subject.value.trim());
      if (topic.value.trim()) query.set("topic", topic.value.trim());
      if (scope) query.set("channel_id", scope);
      try {
        const page = await API.get(`/materials?${query}`);
        show(status, "");
        total.textContent = page.total ? `${page.total} material${page.total === 1 ? "" : "s"}` : "";
        list.replaceChildren(
          ...(page.items.length
            ? page.items.map((m) => materialRow(ui, m))
            : [h("li", { class: "empty muted" }, page.empty_state || "No materials match your search.")]),
        );
      } catch (err) {
        show(status, err.message, "error");
      }
    }

    search.addEventListener("submit", (event) => {
      event.preventDefault();
      load();
    });
    const channel = scope ? channels.find((c) => String(c.id) === scope) : null;
    render(
      h("h1", {}, channel ? `Materials in ${channel.name}` : "Study materials"),
      scope ? h("p", {}, h("a", { href: "#/materials" }, "← All materials")) : null,
      h("div", { class: "grid" }, h("section", { class: "card" }, search, status, total, list), uploadForm(ui, channels, scope, load)),
    );
    await load();
  };

  async function previewInto(ui, box, m) {
    const { h } = ui;
    const blob = await API.blob(`/materials/${m.id}/preview`);
    const type = (m.file && m.file.content_type) || "";
    if (type.startsWith("text/")) {
      box.replaceChildren(h("pre", { class: "preview-text" }, await blob.text()));
    } else if (type === "image/png" || type === "image/jpeg") {
      const img = h("img", { class: "preview-image", alt: `Preview of ${m.title}` });
      img.src = URL.createObjectURL(new Blob([blob], { type }));
      box.replaceChildren(img);
    } else if (type === "application/pdf") {
      const frame = h("iframe", { class: "preview-frame", title: `Preview of ${m.title}` });
      frame.src = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
      box.replaceChildren(frame);
    } else {
      box.replaceChildren(h("p", { class: "muted" }, "Preview isn't available for this file type. Download it instead"));
    }
    box.hidden = false;
  }

  function ratingSummary(m) {
    return `${m.rating_count ? `Average rating ${stars(m.rating_average)} from ${m.rating_count} rating${m.rating_count === 1 ? "" : "s"}` : "No ratings yet"} · ${m.helpful_count} found helpful`;
  }

  async function materialPage(ui, id) {
    const { h, render, alertBox, show, field, busy } = ui;
    let m;
    try {
      m = await API.get(`/materials/${id}`);
    } catch (err) {
      render(h("h1", {}, "Material"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/materials" }, "Back to materials"));
      return;
    }
    const mine = ui.me() && m.uploader_id === ui.me().account.id;
    const summary = h("p", { class: "rating-summary" }, ratingSummary(m));
    const fileStatus = alertBox();
    const previewBox = h("div", { class: "preview", hidden: true });
    const fileActions = h("div", { class: "row-actions wrap" });
    if (m.url) fileActions.append(h("a", { href: m.url, target: "_blank", rel: "noopener noreferrer", class: "button" }, "Open link"));
    if (m.preview_url) {
      const view = h("button", { type: "button", class: "secondary" }, "View");
      view.addEventListener("click", () =>
        busy(view, async () => {
          try {
            await previewInto(ui, previewBox, m);
          } catch (err) {
            show(fileStatus, err.message, "error");
          }
        }),
      );
      fileActions.append(view);
    }
    if (m.download_url) {
      const download = h("button", { type: "button" }, "Download");
      download.addEventListener("click", () =>
        busy(download, async () => {
          try {
            saveBlob(await API.blob(`/materials/${m.id}/download`), m.file && m.file.filename);
          } catch (err) {
            show(fileStatus, err.message, "error");
          }
        }),
      );
      fileActions.append(download);
    }

    // Rate and review (ST_07)
    const rateStatus = alertBox();
    const starInputs = [1, 2, 3, 4, 5].map((n) =>
      h(
        "label",
        { class: "star", title: `${n} star${n === 1 ? "" : "s"}` },
        h("input", { type: "radio", name: "rating", value: String(n), checked: m.my_rating === n }),
        h("span", {}, `${n}★`),
      ),
    );
    const rateForm = h(
      "form",
      { class: "card rate-material", novalidate: true },
      h("h2", {}, "Rate and review"),
      rateStatus,
      h("fieldset", { class: "stars" }, h("legend", {}, "Star rating"), starInputs),
      field("Review", h("textarea", { name: "review", maxlength: 2000, rows: 3 }, m.my_review || ""), "Optional"),
      h("button", { type: "submit" }, "Submit rating"),
    );
    rateForm.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(rateForm.querySelector("button"), async () => {
        const picked = rateForm.querySelector("input[name=rating]:checked");
        try {
          m = await API.put(`/materials/${m.id}/review`, {
            rating: picked ? Number(picked.value) : null,
            review: blankToNull(rateForm.elements.review.value),
          });
          summary.textContent = ratingSummary(m);
          show(rateStatus, "Thanks! Your rating was saved", "success");
          loadReviews();
        } catch (err) {
          show(rateStatus, err.message, "error"); // "Select a rating from 1 to 5 stars"
        }
      });
    });

    // Helpful
    const helpful = h("button", { type: "button", class: "secondary helpful", "aria-pressed": String(m.marked_helpful) });
    const helpfulText = () => {
      helpful.textContent = m.marked_helpful ? `Helpful ✓ (${m.helpful_count})` : `Mark as helpful (${m.helpful_count})`;
      helpful.setAttribute("aria-pressed", String(m.marked_helpful));
    };
    helpfulText();
    helpful.addEventListener("click", () =>
      busy(helpful, async () => {
        try {
          const r = m.marked_helpful ? await API.del(`/materials/${m.id}/helpful`) : await API.put(`/materials/${m.id}/helpful`);
          m.marked_helpful = r.marked_helpful;
          m.helpful_count = r.helpful_count;
          helpfulText();
          summary.textContent = ratingSummary(m);
          show(rateStatus, "");
        } catch (err) {
          show(rateStatus, err.message, "error");
        }
      }),
    );

    // Report (ST_08)
    const reportStatus = alertBox();
    const reportForm = h(
      "form",
      { class: "card report-material", novalidate: true },
      h("h2", {}, "Report"),
      reportStatus,
      field("Reason", h("select", { name: "reason" }, options(h, REPORT_REASONS, "Select a reason"))),
      field("Details", h("textarea", { name: "details", maxlength: 2000, rows: 2 }), "Optional"),
      h("button", { type: "submit", class: "secondary" }, "Submit report"),
    );
    reportForm.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(reportForm.querySelector("button"), async () => {
        try {
          const r = await API.post(`/materials/${m.id}/reports`, {
            reason: blankToNull(reportForm.elements.reason.value),
            details: blankToNull(reportForm.elements.details.value),
          });
          reportForm.reset();
          show(reportStatus, `${r.message} (${when(r.reported_at)})`, "success"); // "Report submitted for moderation"
        } catch (err) {
          show(reportStatus, err.message, "error"); // "Select a reason for reporting this material"
        }
      });
    });

    const reviews = h("ul", { class: "reviews" });
    async function loadReviews() {
      try {
        const page = await API.get(`/materials/${m.id}/reviews`);
        reviews.replaceChildren(
          ...(page.items.length
            ? page.items.map((r) =>
                h("li", {}, h("strong", {}, r.author_name || "Someone"), r.rating ? ` · ${r.rating}★` : null, h("p", {}, r.review)),
              )
            : [h("li", { class: "muted" }, "No written reviews yet.")]),
        );
      } catch (err) {
        reviews.replaceChildren(h("li", { class: "muted" }, err.message));
      }
    }

    render(
      h("p", {}, h("a", { href: m.channel_id ? `#/materials?channel=${m.channel_id}` : "#/materials" }, "← Materials")),
      h(
        "section",
        { class: "card material-detail" },
        h("h1", {}, m.title, " ", h("span", { class: `badge ${m.content_style}` }, m.content_label)),
        h("div", { class: "tags" }, m.tags.map((t) => h("span", { class: "badge tag" }, t))),
        h("p", { class: "muted" }, [m.uploader_name ? `Shared by ${m.uploader_name}` : null, m.created_at ? when(m.created_at) : null, m.file ? m.file.filename : null].filter(Boolean).join(" · ")),
        m.description ? h("p", {}, m.description) : null,
        summary,
        fileStatus,
        fileActions,
        previewBox,
      ),
      h(
        "div",
        { class: "grid even" },
        mine
          ? h("section", { class: "card" }, h("h2", {}, "Rate and review"), h("p", { class: "muted" }, "You can't rate or mark your own material as helpful"))
          : h("div", {}, rateForm, h("section", { class: "card" }, h("h2", {}, "Helpful?"), helpful)),
        reportForm,
      ),
      h("section", { class: "card" }, h("h2", {}, "Reviews"), reviews),
    );
    await loadReviews();
  }

  // ------------------------------------------------------- question bank ----

  function questionRow(ui, q) {
    const { h, alertBox, show, busy } = ui;
    const status = alertBox();
    const rating = h("select", { name: "rating", "aria-label": "Your rating" }, options(h, [5, 4, 3, 2, 1].map((n) => [String(n), `${n}★`]), "Rate"));
    if (q.my_rating) rating.value = String(q.my_rating);
    const rate = h("button", { type: "button", class: "secondary" }, "Rate");
    rate.addEventListener("click", () =>
      busy(rate, async () => {
        if (!rating.value) return show(status, "Select a rating from 1 to 5 stars", "error");
        try {
          const updated = await API.put(`/questions/${q.id}/rating`, { rating: Number(rating.value) });
          show(status, `Rating saved · ${stars(updated.rating_average)} (${updated.rating_count})`, "success");
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    const reason = h("select", { name: "reason", "aria-label": "Report reason" }, options(h, REPORT_REASONS, "Report…"));
    const report = h("button", { type: "button", class: "secondary" }, "Report");
    report.addEventListener("click", () =>
      busy(report, async () => {
        if (!reason.value) return show(status, "Select a reason for reporting this question", "error");
        try {
          const r = await API.post(`/questions/${q.id}/reports`, { reason: reason.value });
          show(status, r.message, "success");
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    return h(
      "li",
      { class: "item question", "data-id": String(q.id) },
      h(
        "div",
        {},
        h("strong", { class: "item-title" }, q.text),
        h(
          "div",
          { class: "tags" },
          [q.subject, q.topic, label(DIFFICULTIES, q.difficulty), label(QUESTION_TYPES, q.question_type)].map((t) => h("span", { class: "badge tag" }, t)),
        ),
        q.options.length ? h("ol", { type: "A", class: "options" }, q.options.map((o) => h("li", {}, o))) : null,
        h("div", { class: "muted" }, [q.author_name ? `by ${q.author_name}` : null, q.rating_count ? `${stars(q.rating_average)} (${q.rating_count})` : "No ratings yet"].filter(Boolean).join(" · ")),
        status,
      ),
      h("div", { class: "row-actions wrap" }, rating, rate, reason, report),
    );
  }

  function addQuestionForm(ui, channels, presetChannel, onAdded) {
    const { h, field, alertBox, show, busy } = ui;
    const status = alertBox();
    const type = h("select", { name: "question_type" }, options(h, QUESTION_TYPES));
    const optionRows = [0, 1, 2, 3].map((i) =>
      h(
        "div",
        { class: "option-row" },
        h("input", { type: "radio", name: "correct", value: String(i), "aria-label": `Option ${i + 1} is correct` }),
        h("input", { name: `option${i}`, maxlength: 500, placeholder: `Option ${i + 1}` }),
      ),
    );
    const mcq = h("fieldset", { class: "mcq" }, h("legend", {}, "Options (select the correct answer)"), optionRows);
    const tf = h(
      "fieldset",
      { class: "tf", hidden: true },
      h("legend", {}, "Correct answer"),
      ["True", "False"].map((v) => h("label", { class: "inline" }, h("input", { type: "radio", name: "tf", value: v }), ` ${v}`)),
    );
    const written = h(
      "div",
      { hidden: true },
      field("Correct answer", h("input", { name: "answer", maxlength: 5000 }), "Short answer: the expected answer. Descriptive: a model answer"),
    );
    const concepts = field("Expected concepts", h("input", { name: "concepts", maxlength: 1000 }), "Descriptive only, separated by commas");
    concepts.hidden = true;
    type.addEventListener("change", () => {
      mcq.hidden = type.value !== "multiple_choice";
      tf.hidden = type.value !== "true_false";
      written.hidden = !["short_answer", "descriptive"].includes(type.value);
      concepts.hidden = type.value !== "descriptive";
    });
    const form = h(
      "form",
      { class: "card add-question", novalidate: true },
      h("h2", {}, "Add Question"),
      status,
      field("Question", h("textarea", { name: "text", maxlength: 5000, rows: 3 })),
      field("Type", type),
      mcq,
      tf,
      written,
      concepts,
      field("Subject", h("input", { name: "subject", maxlength: 100 })),
      field("Topic", h("input", { name: "topic", maxlength: 100 })),
      field("Difficulty", h("select", { name: "difficulty" }, options(h, DIFFICULTIES))),
      field("Add to", channelSelect(h, channels, "channel_id", "General question bank", presetChannel)),
      field("Explanation", h("textarea", { name: "explanation", maxlength: 5000, rows: 2 }), "Optional: shown with the answer"),
      h("button", { type: "submit" }, "Submit question"),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button[type=submit]"), async () => {
        const els = form.elements;
        const body = {
          text: els.text.value,
          subject: els.subject.value,
          topic: els.topic.value,
          difficulty: els.difficulty.value,
          question_type: type.value,
          explanation: blankToNull(els.explanation.value),
          channel_id: els.channel_id.value ? Number(els.channel_id.value) : null,
          correct_answer: null,
        };
        if (type.value === "multiple_choice") {
          body.options = [0, 1, 2, 3].map((i) => els[`option${i}`].value);
          const picked = form.querySelector("input[name=correct]:checked");
          body.correct_answer = picked ? blankToNull(body.options[Number(picked.value)]) : null;
        } else if (type.value === "true_false") {
          const picked = form.querySelector("input[name=tf]:checked");
          body.correct_answer = picked ? picked.value : null;
        } else {
          body.correct_answer = blankToNull(els.answer.value);
          if (type.value === "descriptive") {
            body.expected_concepts = els.concepts.value.split(",").map((c) => c.trim()).filter(Boolean);
          }
        }
        try {
          await API.post("/questions", body);
          form.reset();
          type.dispatchEvent(new Event("change"));
          show(status, "Question added to the bank", "success");
          if (onAdded) onAdded();
        } catch (err) {
          show(status, err.message, "error"); // "Please select the correct answer"
        }
      });
    });
    return form;
  }

  pages.questions = async (ui, rest, params) => {
    const { h, render, alertBox, show } = ui;
    const channels = await myChannels();
    const scope = params.get("channel");
    const list = h("ul", { class: "items" });
    const status = alertBox();
    const q = h("input", { name: "q", type: "search", placeholder: "Keyword", maxlength: 100, value: params.get("q") || "" });
    const subject = h("input", { name: "subject", placeholder: "Subject", maxlength: 100 });
    const topic = h("input", { name: "topic", placeholder: "Topic", maxlength: 100 });
    const difficulty = h("select", { name: "difficulty" }, options(h, DIFFICULTIES, "Any difficulty"));
    const qtype = h("select", { name: "type" }, options(h, QUESTION_TYPES, "Any type"));
    const search = h("form", { class: "search", role: "search" }, q, subject, topic, difficulty, qtype, h("button", { type: "submit" }, "Search"));

    async function load() {
      const query = new URLSearchParams();
      for (const [key, el] of [["q", q], ["subject", subject], ["topic", topic], ["difficulty", difficulty], ["type", qtype]]) {
        if (el.value.trim()) query.set(key, el.value.trim());
      }
      if (scope) query.set("channel_id", scope);
      try {
        const found = await API.get(`/questions${query.toString() ? `?${query}` : ""}`);
        show(status, "");
        list.replaceChildren(
          ...(found.length ? found.map((x) => questionRow(ui, x)) : [h("li", { class: "empty muted" }, "No questions match your search.")]),
        );
      } catch (err) {
        show(status, err.message, "error");
      }
    }

    search.addEventListener("submit", (event) => {
      event.preventDefault();
      load();
    });
    const channel = scope ? channels.find((c) => String(c.id) === scope) : null;
    render(
      h("h1", {}, channel ? `Question bank: ${channel.name}` : "Question bank"),
      scope ? h("p", {}, h("a", { href: "#/questions" }, "← General question bank")) : null,
      h("div", { class: "grid" }, h("section", { class: "card" }, search, status, list), addQuestionForm(ui, channels, scope, load)),
    );
    await load();
  };

  // ------------------------------------------------------------- quizzes ----

  function quizMeta(quiz) {
    return [quiz.subject, quiz.topic, quiz.difficulty, `${quiz.question_count} questions`, `${quiz.duration_minutes} min`, quiz.creator_name ? `by ${quiz.creator_name}` : null]
      .filter(Boolean)
      .join(" · ");
  }

  function quizRow(ui, quiz) {
    const { h } = ui;
    return h(
      "li",
      { class: "item quiz", "data-id": String(quiz.id) },
      h("div", {}, h("strong", { class: "item-title" }, quiz.title), h("div", { class: "muted" }, quizMeta(quiz))),
      h("div", { class: "row-actions" }, h("a", { href: `#/quizzes/${quiz.id}/attempt`, class: "button" }, "Start quiz")),
    );
  }

  function draftRow(ui, quiz, channels, reload) {
    const { h, alertBox, show, busy } = ui;
    const status = alertBox();
    const published = quiz.status !== "draft";
    const where = channelSelect(h, channels, "publish_to", "General (everyone)", quiz.channel_id);
    const publish = h("button", { type: "button" }, "Publish");
    publish.addEventListener("click", () =>
      busy(publish, async () => {
        try {
          const channelId = where.value ? Number(where.value) : null;
          const done = await API.post(`/quizzes/${quiz.id}/publish`, { channel_id: channelId });
          const target = channelId ? channels.find((c) => c.id === channelId) : null;
          show(status, `Published "${done.title}" to ${target ? target.name : "the general quiz list"}`, "success");
          setTimeout(reload, 1200);
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    const target = quiz.channel_id ? channels.find((c) => c.id === quiz.channel_id) : null;
    return h(
      "li",
      { class: "item my-quiz", "data-id": String(quiz.id) },
      h(
        "div",
        {},
        h("strong", { class: "item-title" }, quiz.title),
        " ",
        h("span", { class: `badge ${published ? "published" : "draft"}` }, published ? "Published" : "Draft"),
        h("div", { class: "muted" }, quizMeta(quiz), published ? ` · ${target ? target.name : "General"}` : ""),
        status,
      ),
      published
        ? h("div", { class: "row-actions" }, h("a", { href: `#/quizzes/${quiz.id}/attempt`, class: "button secondary" }, "Start quiz"))
        : h("div", { class: "row-actions wrap" }, h("label", { class: "inline" }, "Publish to ", where), publish),
    );
  }

  function createQuizForm(ui, channels, presetChannel, onSaved) {
    const { h, field, alertBox, show, busy } = ui;
    const status = alertBox();
    const preview = h("div", { class: "quiz-preview", hidden: true });
    let criteria = null;
    let chosen = [];
    const save = h("button", { type: "button", class: "save-quiz" }, "Save quiz");
    const form = h(
      "form",
      { class: "card create-quiz", novalidate: true },
      h("h2", {}, "Create Quiz"),
      status,
      field("Title", h("input", { name: "title", maxlength: 200 }), "Optional: named after the topic if blank"),
      field("Subject", h("input", { name: "subject", maxlength: 100 })),
      field("Topic", h("input", { name: "topic", maxlength: 100 })),
      field("Difficulty", h("select", { name: "difficulty" }, options(h, DIFFICULTIES))),
      field("Number of questions", h("input", { name: "question_count", type: "number", min: 1, max: 100, value: 5 })),
      field("Duration (minutes)", h("input", { name: "duration_minutes", type: "number", min: 1, max: 300, value: 15 })),
      field("Also use questions from", channelSelect(h, channels, "channel_id", "General question bank only", presetChannel)),
      h("button", { type: "submit" }, "Generate quiz"),
      preview,
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button[type=submit]"), async () => {
        const els = form.elements;
        criteria = {
          subject: blankToNull(els.subject.value),
          topic: blankToNull(els.topic.value),
          difficulty: els.difficulty.value,
          question_count: Number(els.question_count.value) || 0,
          duration_minutes: Number(els.duration_minutes.value) || 0,
          channel_id: els.channel_id.value ? Number(els.channel_id.value) : null,
        };
        try {
          const picked = await API.post("/quizzes/preview", criteria);
          chosen = picked.questions;
          show(status, "");
          preview.replaceChildren(
            h("h3", {}, `${chosen.length} questions · ${criteria.duration_minutes} minutes`),
            h("ol", { class: "preview-questions" }, chosen.map((q) => h("li", {}, q.text, " ", h("span", { class: "muted" }, `(${label(DIFFICULTIES, q.difficulty)}, ${label(QUESTION_TYPES, q.question_type)})`)))),
            save,
          );
          preview.hidden = false;
        } catch (err) {
          preview.hidden = true;
          show(status, err.message, "error"); // e.g. too few matching questions, or "Select a topic"
        }
      });
    });
    save.addEventListener("click", () =>
      busy(save, async () => {
        try {
          const quiz = await API.post("/quizzes", {
            ...criteria,
            title: blankToNull(form.elements.title.value),
            question_ids: chosen.map((q) => q.id),
          });
          form.reset();
          preview.hidden = true;
          show(status, `Saved "${quiz.title}" as a draft. Publish it from My quizzes.`, "success");
          if (onSaved) onSaved(quiz);
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    return form;
  }

  pages.quizzes = async (ui, rest, params) => {
    if (rest[0] && rest[1] === "attempt") return attemptPage(ui, encodeURIComponent(rest[0]));
    const { h, render, alertBox, show } = ui;
    const channels = await myChannels();
    const scope = params.get("channel") || "";
    const status = alertBox();
    const list = h("ul", { class: "items" });
    const mineList = h("ul", { class: "items" });
    const scopeSelect = channelSelect(h, channels, "scope", "General quizzes", scope);
    scopeSelect.addEventListener("change", () => ui.go(scopeSelect.value ? `#/quizzes?channel=${scopeSelect.value}` : "#/quizzes"));

    async function load() {
      try {
        const [available, mine] = await Promise.all([
          API.get(`/quizzes${scope ? `?channel_id=${encodeURIComponent(scope)}` : ""}`),
          API.get("/quizzes?mine=true"),
        ]);
        show(status, "");
        list.replaceChildren(...(available.length ? available.map((x) => quizRow(ui, x)) : [h("li", { class: "empty muted" }, "No quizzes published here yet.")]));
        mineList.replaceChildren(
          ...(mine.length ? mine.map((x) => draftRow(ui, x, channels, load)) : [h("li", { class: "empty muted" }, "You have not created any quizzes yet.")]),
        );
      } catch (err) {
        show(status, err.message, "error");
      }
    }

    render(
      h("h1", {}, "Quizzes"),
      h(
        "div",
        { class: "grid" },
        h(
          "div",
          {},
          h("section", { class: "card quiz-list" }, h("label", { class: "field" }, h("span", {}, "Show quizzes in"), scopeSelect), status, list),
          h("section", { class: "card my-quizzes" }, h("h2", {}, "My quizzes"), mineList),
        ),
        createQuizForm(ui, channels, scope, load),
      ),
    );
    await load();
  };

  // ------------------------------------------------------------ attempts ----

  function answerInput(h, q, onChange) {
    const name = `q${q.question_id}`;
    if (q.options && q.options.length) {
      return h(
        "div",
        { class: "choices" },
        q.options.map((o) =>
          h("label", { class: "choice" }, h("input", { type: "radio", name, value: o, checked: q.response === o, onchange: onChange }), " ", o),
        ),
      );
    }
    const attrs = { name, maxlength: 5000, oninput: onChange, "aria-label": `Answer to question ${q.position}` };
    return q.question_type === "descriptive"
      ? h("textarea", { ...attrs, rows: 4 }, q.response || "")
      : h("input", { ...attrs, value: q.response || "" });
  }

  function collect(form, questions) {
    const answers = {};
    for (const q of questions) {
      const name = `q${q.question_id}`;
      const checked = form.querySelector(`input[name="${name}"]:checked`);
      const field = form.elements[name];
      let value = null;
      if (q.options && q.options.length) value = checked ? checked.value : null;
      else if (field) value = blankToNull(field.value);
      answers[q.question_id] = value;
    }
    return answers;
  }

  function unansweredText(n) {
    return n === 1 ? "1 question is unanswered" : `${n} questions are unanswered`;
  }

  async function attemptPage(ui, quizId) {
    const { h, render, alertBox, show, busy, go } = ui;
    let attempt;
    try {
      attempt = await API.post(`/quizzes/${quizId}/attempts`);
    } catch (err) {
      render(h("h1", {}, "Quiz"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/quizzes" }, "Back to quizzes"));
      return;
    }
    if (attempt.status !== "in_progress") return go(`#/attempts/${attempt.id}`);

    const status = alertBox();
    const timer = h("strong", { class: "timer", role: "timer", "aria-live": "off" }, "");
    const progress = h("span", { class: "progress muted" });
    const warning = h("p", { class: "alert info unanswered", role: "status", hidden: true });
    const questions = attempt.questions;
    let finished = false;
    let saveTimer = null;

    const form = h("form", { class: "attempt", novalidate: true });
    const submit = h("button", { type: "submit" }, "Submit quiz");
    const confirmBox = h("div", { class: "confirm-submit", hidden: true });

    function refresh() {
      const answers = collect(form, questions);
      const missing = Object.values(answers).filter((v) => v === null).length;
      progress.textContent = `${questions.length - missing} of ${questions.length} answered`;
      warning.textContent = missing ? unansweredText(missing) : "";
      warning.hidden = !missing;
      return answers;
    }

    async function save() {
      clearTimeout(saveTimer);
      saveTimer = null;
      if (finished) return;
      try {
        await API.put(`/attempts/${attempt.id}/answers`, { answers: collect(form, questions) });
      } catch (err) {
        if (err.status === 409) return finish(); // the timer closed it on the server
        show(status, err.message, "error");
      }
    }

    function changed() {
      refresh();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 500);
      confirmBox.hidden = true;
    }

    function finish() {
      if (finished) return;
      finished = true;
      clearInterval(tick);
      go(`#/attempts/${attempt.id}`);
    }

    async function submitNow(confirm) {
      const response = await API.post(`/attempts/${attempt.id}/submit`, { answers: collect(form, questions), confirm }).catch((err) => err);
      if (!(response instanceof Error)) return finish();
      if (response.status === 409 && response.body && response.body.unanswered_count !== undefined) {
        // "2 questions are unanswered": the "warn" rule asks for confirmation first.
        confirmBox.replaceChildren(
          h("p", { class: "alert info" }, response.message, ". Submit anyway?"),
          h("div", { class: "row-actions" }, confirmButton, h("button", { type: "button", class: "secondary", onclick: () => (confirmBox.hidden = true) }, "Keep answering")),
        );
        confirmBox.hidden = false;
        return;
      }
      if (response.status === 409) return finish(); // already submitted (e.g. by the timer)
      show(status, response.message, "error"); // the "block" rule refuses it
    }

    const confirmButton = h("button", { type: "button", class: "submit-anyway" }, "Submit anyway");
    confirmButton.addEventListener("click", () => busy(confirmButton, () => submitNow(true)));

    // Time is up: the server owns the clock. Save what is on screen, then read the
    // attempt until the server has closed it ("auto-submitted"), so only the answers
    // saved before the limit are scored and the rest are unattempted (ST_11).
    async function timeUp() {
      if (finished) return;
      submit.disabled = true;
      for (const el of form.elements) el.disabled = true;
      show(status, "Time is up. Submitting your answers…", "info");
      await save();
      for (let i = 0; i < 12 && !finished; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        try {
          const current = await API.get(`/attempts/${attempt.id}`);
          if (current.status !== "in_progress") return finish();
        } catch {
          /* keep trying */
        }
      }
      if (!finished) await submitNow(true);
    }

    const deadline = attempt.remaining_seconds === null ? null : Date.now() + attempt.remaining_seconds * 1000;
    function updateTimer() {
      if (!document.body.contains(timer)) {
        clearInterval(tick); // the user navigated away; the attempt keeps running on the server
        return;
      }
      if (deadline === null) {
        timer.textContent = "No time limit";
        return;
      }
      const left = (deadline - Date.now()) / 1000;
      timer.textContent = `Time left ${clock(Math.ceil(left))}`;
      timer.classList.toggle("low", left <= 60);
      if (left <= 0) {
        clearInterval(tick);
        timeUp();
      }
    }

    questions.forEach((q) => {
      form.append(
        h(
          "fieldset",
          { class: "card attempt-question", "data-question": String(q.question_id) },
          h("legend", {}, `Question ${q.position}`, h("span", { class: "muted" }, ` · ${q.points} point${q.points === 1 ? "" : "s"}`)),
          h("p", { class: "question-text" }, q.text),
          answerInput(h, q, changed),
        ),
      );
    });
    form.append(warning, confirmBox, h("div", { class: "row-actions" }, submit));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(submit, async () => {
        clearTimeout(saveTimer);
        await submitNow(false);
      });
    });

    render(
      h("div", { class: "attempt-head card" }, h("h1", {}, attempt.quiz_title), h("div", { class: "attempt-status" }, timer, progress)),
      status,
      form,
    );
    refresh();
    const tick = setInterval(updateTimer, 1000);
    updateTimer();
  }

  pages.attempts = async (ui, rest) => {
    const { h, render } = ui;
    if (!rest[0]) return ui.go("#/history");
    let a;
    try {
      a = await API.get(`/attempts/${encodeURIComponent(rest[0])}`);
    } catch (err) {
      render(h("h1", {}, "Result"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/history" }, "My Quiz History"));
      return;
    }
    if (a.status === "in_progress" || !a.result) {
      render(h("h1", {}, a.quiz_title), h("p", { class: "alert info" }, "This attempt is still running."), h("a", { href: `#/quizzes/${a.quiz_id}/attempt`, class: "button" }, "Resume quiz"));
      return;
    }
    const r = a.result;
    const text = Object.fromEntries(a.questions.map((q) => [q.question_id, q.text]));
    render(
      h("h1", {}, `Result: ${a.quiz_title}`),
      h(
        "section",
        { class: "card result" },
        h("p", { class: "score" }, h("span", { class: "muted" }, "Your score "), h("strong", {}, r.display)),
        a.auto_submitted ? h("p", { class: "alert info auto-submitted" }, "Time ran out, so the answers you had saved were submitted automatically.") : null,
        h(
          "div",
          { class: "tiles" },
          [["Correct", r.correct], ["Incorrect", r.incorrect], ["Unattempted", r.unattempted], ["Awaiting evaluation", r.pending_evaluation]].map(([k, v]) =>
            h("div", { class: `tile tile-${k.toLowerCase().replace(/\s+/g, "-")}` }, h("span", { class: "muted" }, k), h("strong", {}, String(v))),
          ),
        ),
        h("p", { class: "muted" }, r.breakdown.explanation),
        h("p", { class: "muted" }, `Submitted ${when(r.submitted_at)}`),
      ),
      h(
        "section",
        { class: "card" },
        h("h2", {}, "Answers"),
        h(
          "ol",
          { class: "answers" },
          r.answers.map((x) =>
            h(
              "li",
              { class: `answer ${x.status}` },
              h("p", { class: "question-text" }, text[x.question_id] || ""),
              h("p", {}, "Your answer: ", h("strong", {}, x.response || "—"), " ", h("span", { class: `badge status-${x.status}` }, ANSWER_STATUS[x.status] || x.status), x.score !== null ? ` · ${x.score}/${x.points}` : ""),
              x.correct_answer ? h("p", { class: "muted" }, `Correct answer: ${x.correct_answer}`) : null,
              x.explanation ? h("p", { class: "muted" }, x.explanation) : null,
              x.evaluation ? h("p", { class: "muted" }, `${x.evaluation.display}: ${x.evaluation.summary}`) : null,
            ),
          ),
        ),
      ),
      h("p", {}, h("a", { href: "#/history" }, "My Quiz History"), " · ", h("a", { href: "#/quizzes" }, "More quizzes")),
    );
  };

  pages.history = async (ui) => {
    const { h, render } = ui;
    const history = await API.get("/attempts");
    const pct = (v) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(1).replace(/\.0$/, "")}%`);
    if (!history.attempt_count) {
      render(h("h1", {}, "My Quiz History"), h("section", { class: "card empty-state" }, h("p", {}, "No quiz attempts yet."), h("a", { href: "#/quizzes" }, "Find a quiz")));
      return;
    }
    render(
      h("h1", {}, "My Quiz History"),
      h(
        "div",
        { class: "tiles" },
        [["Attempts", history.attempt_count], ["Average accuracy", pct(history.average_accuracy)], ["Best", pct(history.best_percentage)]].map(([k, v]) =>
          h("div", { class: "tile" }, h("span", { class: "muted" }, k), h("strong", {}, String(v))),
        ),
      ),
      h(
        "section",
        { class: "card table-wrap" },
        h(
          "table",
          { class: "history" },
          h("thead", {}, h("tr", {}, ["Quiz", "Topic", "Submitted", "Score", ""].map((t) => h("th", {}, t)))),
          h(
            "tbody",
            {},
            history.attempts.map((e) =>
              h(
                "tr",
                { "data-attempt": String(e.attempt_id) },
                h("td", {}, e.quiz_title, e.auto_submitted ? h("span", { class: "badge" }, " auto-submitted") : null),
                h("td", {}, e.topic),
                h("td", {}, when(e.submitted_at)),
                h("td", {}, e.display),
                h("td", {}, h("a", { href: `#/attempts/${e.attempt_id}` }, "View")),
              ),
            ),
          ),
        ),
      ),
    );
  };
})();
