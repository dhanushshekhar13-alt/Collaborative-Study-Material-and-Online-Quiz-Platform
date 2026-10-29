// StudyHub web UI, card UI-3 (#64): channel chat with threads, direct messages with
// block and report, Ask AI with the generated-question review screen, the notification
// bell and settings, the moderation queue and the admin pages.
//
// Pages register in window.StudyHubPages like UI-2's (study.js); app.js's router calls
// them as page(ui, restOfPath, queryParams) with the shared helpers. The channel page
// (app.js) mounts the chat with window.StudyHubChat, and the nav refreshes the bell
// with window.StudyHubBell. Server text goes in with textContent only, and every
// message the user reads (refusals, validation) is the API's own `detail`.
"use strict";

(() => {
  const pages = (window.StudyHubPages = window.StudyHubPages || {});

  const REPORT_REASONS = [
    ["inappropriate", "Inappropriate content"],
    ["spam", "Spam"],
    ["misleading", "Misleading"],
    ["incorrect", "Incorrect information"],
    ["duplicate", "Duplicate"],
    ["other", "Other"],
  ];
  const DIFFICULTIES = [
    ["easy", "Easy"],
    ["medium", "Medium"],
    ["hard", "Hard"],
  ];
  const TARGET_LABEL = {
    material: "Material",
    question: "Question",
    direct_message: "Direct message",
    channel_message: "Chat message",
    channel_post: "Channel post",
  };
  const label = (pairs, value) => (pairs.find(([v]) => v === value) || [value, value])[1];
  const POLL_MS = 5000;

  function when(value) {
    return value ? new Date(value).toLocaleString() : "";
  }

  function blankToNull(value) {
    const text = (value || "").trim();
    return text ? text : null;
  }

  // Only links into this UI are followed; anything else from the server is shown as text.
  function uiLink(link) {
    if (!link) return null;
    if (link.startsWith("#/")) return link;
    if (link.startsWith("/app/#/")) return link.slice("/app/".length);
    if (link.startsWith("/api/v1/")) return `#/${link.slice("/api/v1/".length)}`;
    return null;
  }

  function sharedCard(h, card) {
    if (!card) return null;
    const href = card.available ? uiLink(card.web_path) : null;
    const body = [
      h("span", { class: `badge kind-${card.type}` }, card.type),
      " ",
      h("strong", {}, card.title || "No longer available"),
      card.subtitle ? h("span", { class: "muted" }, ` · ${card.subtitle}`) : null,
    ];
    return href
      ? h("a", { class: "shared-card", href, "data-type": card.type }, body)
      : h("span", { class: "shared-card unavailable", "data-type": card.type }, body);
  }

  // Polls `tick` while `node` is on the page; stops once the page has moved on.
  function poll(node, tick) {
    const timer = window.setInterval(() => {
      if (!node.isConnected) return window.clearInterval(timer);
      tick().catch(() => {});
    }, POLL_MS);
  }

  // A textarea and a Send button that stays disabled while the box is blank (UT_20).
  function composer(h, placeholder, onSend, { buttonText = "Send" } = {}) {
    const text = h("textarea", { name: "body", rows: 2, maxlength: 2000, placeholder, "aria-label": placeholder });
    const send = h("button", { type: "submit", disabled: true }, buttonText);
    const form = h("form", { class: "composer", novalidate: true }, text, send);
    text.addEventListener("input", () => {
      send.disabled = !text.value.trim();
    });
    text.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!send.disabled) form.requestSubmit();
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      send.disabled = true;
      try {
        if (await onSend(text.value)) text.value = "";
      } finally {
        send.disabled = !text.value.trim();
        text.focus();
      }
    });
    return form;
  }

  // ------------------------------------------------- channel chat (S10.x) ----
  // Mounted on the channel page. Top-level messages oldest first; "Load older"
  // pages back with `before`; new messages arrive by polling with `after`. Each
  // message opens its thread inline. `?message=<id>` (a notification link) opens the
  // thread holding that message.

  window.StudyHubChat = (ui, channel, params) => {
    const { h, alertBox, show } = ui;
    const status = alertBox();
    const list = h("ol", { class: "chat-messages", "aria-live": "polite" });
    const older = h("button", { type: "button", class: "secondary older", hidden: true }, "Load older messages");
    const section = h(
      "section",
      { class: "card chat", "data-channel": String(channel.id) },
      h("h2", {}, "Chat"),
      status,
      older,
      list,
    );
    let oldest = null;
    let newest = null;

    function messageItem(m) {
      const thread = h("div", { class: "thread", hidden: true });
      const toggle = h("button", { type: "button", class: "link reply-toggle" }, replyLabel(m));
      const item = h(
        "li",
        { class: "message", "data-id": String(m.id) },
        messageHead(h, m),
        m.body ? h("p", { class: "body" }, m.body) : null,
        sharedCard(h, m.shared_resource),
        h("div", { class: "row-actions" }, toggle),
        thread,
      );
      item.refresh = (updated) => {
        toggle.textContent = replyLabel(updated);
      };
      toggle.addEventListener("click", () => {
        if (thread.hidden) openThread(m.id, item, thread);
        else thread.hidden = true;
      });
      item.openThread = () => openThread(m.id, item, thread);
      return item;
    }

    function replyLabel(m) {
      return m.reply_count ? `${m.reply_count} ${m.reply_count === 1 ? "reply" : "replies"} · Reply` : "Reply";
    }

    async function openThread(id, item, thread, focusId) {
      const replies = h("ol", { class: "replies" });
      const threadStatus = alertBox();
      let last = null;
      const render = (rows) => {
        for (const r of rows) {
          replies.append(h("li", { class: "message reply", "data-id": String(r.id) }, messageHead(h, r), r.body ? h("p", { class: "body" }, r.body) : null, sharedCard(h, r.shared_resource)));
          last = r.id;
        }
      };
      try {
        const page = await API.get(`/channels/${channel.id}/messages/${id}/replies?limit=100`);
        render(page.items);
        item.refresh(page.parent);
      } catch (err) {
        show(threadStatus, err.message, "error");
      }
      const reply = composer(h, "Reply in thread", async (body) => {
        try {
          await API.post(`/channels/${channel.id}/messages`, { body, parent_id: id });
          const page = await API.get(`/channels/${channel.id}/messages/${id}/replies${last ? `?after=${last}` : ""}`);
          render(page.items);
          item.refresh(page.parent);
          show(threadStatus, "");
          return true;
        } catch (err) {
          show(threadStatus, err.message, "error");
          return false;
        }
      }, { buttonText: "Reply" });
      thread.replaceChildren(replies, threadStatus, reply);
      thread.hidden = false;
      const target = focusId ? replies.querySelector(`[data-id="${CSS.escape(String(focusId))}"]`) : null;
      if (target) target.classList.add("highlight");
      (target || thread).scrollIntoView({ block: "nearest" });
    }

    function add(rows, { prepend = false } = {}) {
      const nodes = rows.map(messageItem);
      if (prepend) list.prepend(...nodes);
      else list.append(...nodes);
      if (rows.length) {
        if (oldest === null || rows[0].id < oldest) oldest = rows[0].id;
        if (newest === null || rows[rows.length - 1].id > newest) newest = rows[rows.length - 1].id;
      }
    }

    async function load() {
      let page;
      try {
        page = await API.get(`/channels/${channel.id}/messages?limit=50`);
      } catch (err) {
        // IT_20: a non-member reads the API's "Join the channel to view this chat".
        show(status, err.message, "error");
        return;
      }
      add(page.items);
      older.hidden = !page.has_more;
      if (!page.items.length) list.append(h("li", { class: "empty muted" }, "No messages yet. Say hello!"));
      section.append(
        composer(h, "Write a message", async (body) => {
          try {
            const sent = await API.post(`/channels/${channel.id}/messages`, { body });
            list.querySelector(".empty")?.remove();
            if (!list.querySelector(`[data-id="${sent.id}"]`)) add([sent]);
            show(status, "");
            list.lastElementChild?.scrollIntoView({ block: "nearest" });
            return true;
          } catch (err) {
            show(status, err.message, "error"); // e.g. "Message cannot be empty"
            return false;
          }
        }),
      );
      poll(section, async () => {
        const fresh = await API.get(`/channels/${channel.id}/messages${newest ? `?after=${newest}` : ""}`);
        const unseen = fresh.items.filter((m) => !list.querySelector(`[data-id="${m.id}"]`));
        if (unseen.length) {
          list.querySelector(".empty")?.remove();
          add(unseen);
        }
      });
      const focus = Number(params && params.get("message"));
      if (focus) {
        try {
          const page = await API.get(`/channels/${channel.id}/messages/${focus}/replies?limit=100`);
          const parent = list.querySelector(`[data-id="${page.parent.id}"]`);
          if (parent && page.parent.id !== focus) await openThreadOn(parent, focus);
          else if (parent) {
            parent.classList.add("highlight");
            parent.scrollIntoView({ block: "nearest" });
          }
        } catch {
          /* the message is gone; the chat is still shown */
        }
      }
    }

    function openThreadOn(item, focusId) {
      return openThread(Number(item.dataset.id), item, item.querySelector(".thread"), focusId);
    }

    older.addEventListener("click", async () => {
      older.disabled = true;
      try {
        const page = await API.get(`/channels/${channel.id}/messages?limit=50&before=${oldest}`);
        add(page.items, { prepend: true });
        older.hidden = !page.has_more;
      } catch (err) {
        show(status, err.message, "error");
      } finally {
        older.disabled = false;
      }
    });

    load();
    return section;
  };

  function messageHead(h, m) {
    return h(
      "div",
      { class: "message-head" },
      h("strong", { class: "sender" }, m.sender_name || "Former member"),
      " ",
      h("time", { class: "muted", datetime: m.created_at || "" }, when(m.created_at)),
    );
  }

  // ------------------------------------------------- user profile + Message ----
  // UT_21: open another user's profile and click "Message".

  pages.users = async (ui, rest) => {
    const { h, render, alertBox, show, busy, go, me } = ui;
    let p;
    try {
      p = await API.get(`/profiles/${encodeURIComponent(rest[0] || "")}`);
    } catch (err) {
      return render(h("h1", {}, "Profile"), h("p", { class: "alert error" }, err.message));
    }
    const status = alertBox();
    const mine = me() && me().account.id === p.id;
    const message = h("button", { type: "button" }, "Message");
    message.addEventListener("click", () =>
      busy(message, async () => {
        try {
          const conversation = await API.post("/conversations", { user_id: p.id });
          go(`#/messages/${conversation.id}`);
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    const c = p.contributions || {};
    render(
      h(
        "section",
        { class: "card profile-view" },
        h("h1", {}, p.display_name),
        p.username ? h("p", { class: "muted" }, `@${p.username}`) : null,
        h("p", { class: p.bio ? "" : "muted" }, p.bio || "No bio yet."),
        h(
          "dl",
          { class: "facts" },
          [
            ["Role", p.role.replace("_", " ")],
            ["Institution", p.institution || "—"],
            ["Reputation", p.reputation],
            ["Contributions", c.total ?? 0],
          ].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]),
        ),
        p.badges.length ? h("p", {}, "Badges: ", p.badges.map((b) => h("span", { class: "badge" }, b.name))) : null,
        status,
        mine ? h("a", { href: "#/profile" }, "Edit my profile") : h("div", { class: "row-actions" }, message),
      ),
    );
  };

  // ------------------------------------------------- direct messages (S11.1) ----

  pages.messages = async (ui, rest, params) => {
    if (rest[0]) return conversationPage(ui, encodeURIComponent(rest[0]));
    const { h, render, go } = ui;
    const start = Number(params.get("user"));
    if (start) {
      try {
        const conversation = await API.post("/conversations", { user_id: start });
        return go(`#/messages/${conversation.id}`);
      } catch (err) {
        return render(h("h1", {}, "Messages"), h("p", { class: "alert error" }, err.message));
      }
    }
    const conversations = await API.get("/conversations");
    render(
      h("h1", {}, "Messages"),
      h(
        "section",
        { class: "card" },
        conversations.length
          ? h(
              "ul",
              { class: "conversations" },
              conversations.map((c) =>
                h(
                  "li",
                  { class: "conversation", "data-id": String(c.id) },
                  h("a", { href: `#/messages/${c.id}`, class: "channel-name" }, c.other_user.display_name || "Former member"),
                  c.blocked ? h("span", { class: "badge blocked" }, "Blocked") : null,
                  h("div", { class: "muted" }, c.last_message ? `${c.last_message.sender_name || ""}: ${c.last_message.body || "shared an item"}` : "No messages yet"),
                ),
              ),
            )
          : h("p", { class: "empty muted" }, "No conversations yet. Open someone's profile from a channel's member list and click Message."),
      ),
    );
  };

  async function conversationPage(ui, id) {
    const { h, render, alertBox, show, busy, me } = ui;
    let c;
    try {
      c = await API.get(`/conversations/${id}`);
    } catch (err) {
      return render(h("h1", {}, "Messages"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/messages" }, "Back to messages"));
    }
    const status = alertBox();
    const list = h("ol", { class: "chat-messages dm", "aria-live": "polite" });
    const older = h("button", { type: "button", class: "secondary older", hidden: true }, "Load older messages");
    const indicator = h("p", { class: "alert info blocked-indicator", hidden: true });
    const actions = h("div", { class: "row-actions" });
    const myId = me().account.id;
    let oldest = null;
    let newest = null;

    function item(m) {
      const report = m.sender_id !== myId ? reportControl(m) : null;
      return h(
        "li",
        { class: `message ${m.sender_id === myId ? "mine" : ""}`, "data-id": String(m.id) },
        messageHead(h, m),
        m.body ? h("p", { class: "body" }, m.body) : null,
        sharedCard(h, m.shared_resource),
        report,
      );
    }

    // IT_22: select a message, click Report, pick a reason and submit.
    function reportControl(m) {
      const open = h("button", { type: "button", class: "link report" }, "Report");
      const reason = h("select", { name: "reason" }, h("option", { value: "" }, "Select a reason"), REPORT_REASONS.map(([v, t]) => h("option", { value: v }, t)));
      const note = alertBox();
      const submit = h("button", { type: "submit" }, "Submit report");
      const form = h("form", { class: "report-form inline", hidden: true }, reason, submit, note);
      open.addEventListener("click", () => {
        form.hidden = !form.hidden;
      });
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        busy(submit, async () => {
          try {
            const result = await API.post(`/conversations/${c.id}/messages/${m.id}/reports`, { reason: reason.value || null });
            show(note, result.message, "success");
            submit.hidden = true;
            reason.disabled = true;
          } catch (err) {
            show(note, err.message, "error"); // "Select a reason for reporting this message"
          }
        });
      });
      return h("div", { class: "row-actions" }, open, form);
    }

    function add(rows, { prepend = false } = {}) {
      const nodes = rows.filter((m) => !list.querySelector(`[data-id="${m.id}"]`)).map(item);
      if (prepend) list.prepend(...nodes);
      else list.append(...nodes);
      for (const m of rows) {
        if (oldest === null || m.id < oldest) oldest = m.id;
        if (newest === null || m.id > newest) newest = m.id;
      }
      if (list.children.length) list.querySelector(".empty")?.remove();
    }

    const box = composer(h, `Message ${c.other_user.display_name || ""}`.trim(), async (body) => {
      try {
        add([await API.post(`/conversations/${c.id}/messages`, { body })]);
        show(status, "");
        list.lastElementChild?.scrollIntoView({ block: "nearest" });
        return true;
      } catch (err) {
        show(status, err.message, "error"); // e.g. "You can no longer send messages to this user"
        return false;
      }
    });

    function showBlockState() {
      indicator.hidden = !c.blocked;
      indicator.textContent = c.blocked_by_me
        ? `You have blocked ${c.other_user.display_name}. Unblock them to send messages.`
        : "This conversation is blocked. Messages can't be sent.";
      box.hidden = !c.can_send;
      const toggle = c.blocked_by_me
        ? h("button", { type: "button", class: "secondary" }, "Unblock User")
        : h("button", { type: "button", class: "secondary" }, "Block User");
      toggle.addEventListener("click", () => {
        if (!c.blocked_by_me && !window.confirm(`Block ${c.other_user.display_name}? They will not be able to message you.`)) return;
        busy(toggle, async () => {
          try {
            if (c.blocked_by_me) await API.del(`/blocks/${c.other_user.id}`);
            else await API.put(`/blocks/${c.other_user.id}`, { confirm: true });
            c = await API.get(`/conversations/${c.id}`);
            show(status, "");
            showBlockState();
          } catch (err) {
            show(status, err.message, "error");
          }
        });
      });
      actions.replaceChildren(h("a", { href: `#/users/${c.other_user.id}` }, "View profile"), toggle);
    }

    older.addEventListener("click", () =>
      busy(older, async () => {
        const page = await API.get(`/conversations/${c.id}/messages?limit=50&before=${oldest}`);
        add(page.items, { prepend: true });
        older.hidden = !page.has_more;
      }),
    );

    const section = h(
      "section",
      { class: "card chat" },
      h("div", { class: "chat-head" }, h("h1", {}, c.other_user.display_name || "Former member"), c.blocked ? h("span", { class: "badge blocked" }, "Blocked") : null, actions),
      indicator,
      status,
      older,
      list,
      box,
    );
    render(h("p", {}, h("a", { href: "#/messages" }, "← Messages")), section);
    showBlockState();
    const page = await API.get(`/conversations/${c.id}/messages?limit=50`);
    if (!page.items.length) list.append(h("li", { class: "empty muted" }, "No messages yet."));
    add(page.items);
    older.hidden = !page.has_more;
    list.lastElementChild?.scrollIntoView({ block: "nearest" });
    poll(section, async () => {
      const fresh = await API.get(`/conversations/${c.id}/messages${newest ? `?after=${newest}` : ""}`);
      add(fresh.items);
    });
  }

  // ----------------------------------------------------------- Ask AI (S09) ----
  // UT_18: a blank question shows "Please enter a question" and sends nothing.

  async function myChannels() {
    try {
      return await API.get("/channels/mine");
    } catch {
      return [];
    }
  }

  function channelPicker(h, channels, selected, generalLabel) {
    const select = h("select", { name: "channel_id" }, h("option", { value: "" }, generalLabel), channels.map((c) => h("option", { value: String(c.id) }, c.name)));
    select.value = selected && channels.some((c) => String(c.id) === String(selected)) ? String(selected) : "";
    return select;
  }

  pages.ask = async (ui, rest, params) => {
    if (rest[0] === "review" && rest[1]) return reviewPage(ui, encodeURIComponent(rest[1]));
    const { h, render, alertBox, show, field, busy, go } = ui;
    const channels = await myChannels();
    const status = alertBox();
    const result = h("div", { class: "ask-result" });
    const query = h("textarea", { name: "query", rows: 3, maxlength: 500, placeholder: 'e.g. "Explain B-Trees with an example" or "Give me a quiz on Binary Search Trees"' });
    const difficulty = h("select", { name: "difficulty" }, DIFFICULTIES.map(([v, t]) => h("option", { value: v }, t)));
    difficulty.value = "medium";
    const channel = channelPicker(h, channels, params.get("channel"), "No channel (general)");
    const form = h(
      "form",
      { class: "card ask", novalidate: true },
      h("h2", {}, "Ask a question"),
      field("Your question", query),
      field("Ask in channel", channel, "In a channel, answers and quizzes use that channel's subject and question bank"),
      field("Quiz difficulty", difficulty),
      status,
      h("button", { type: "submit" }, "Submit"),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!query.value.trim()) {
        show(status, "Please enter a question", "error");
        result.replaceChildren();
        return;
      }
      busy(form.querySelector("button[type=submit]"), async () => {
        show(status, "Thinking…", "info");
        try {
          const body = { query: query.value, difficulty: difficulty.value, channel_id: channel.value ? Number(channel.value) : null };
          const answer = await API.post("/ai/ask", body);
          show(status, "");
          result.replaceChildren(answerCard(h, answer, busy, go, alertBox, show));
        } catch (err) {
          show(status, err.message, "error");
        }
      });
    });

    render(
      h("h1", {}, "Ask AI"),
      h("div", { class: "grid" }, h("div", {}, form, result), await generationsCard(ui, channels, params)),
    );
  };

  function answerCard(h, a, busy, go, alertBox, show) {
    if (a.kind === "explanation" && a.explanation) {
      return h(
        "section",
        { class: "card answer explanation" },
        h("h2", {}, a.topic),
        h("p", { class: "explanation-text" }, a.explanation.text),
        a.explanation.examples.length ? h("div", {}, h("h3", {}, "Examples"), h("ul", { class: "examples" }, a.explanation.examples.map((e) => h("li", {}, e)))) : null,
        h("p", { class: "muted" }, a.message),
      );
    }
    const q = a.quiz;
    const status = alertBox();
    const start = h("button", { type: "button" }, "Start quiz");
    start.addEventListener("click", () =>
      busy(start, async () => {
        try {
          const started = await API.post(`/ai/ask/${a.id}/start`);
          go(`#/attempts/${started.attempt_id}`);
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    return h(
      "section",
      { class: "card answer quiz-offer" },
      h("h2", {}, q ? q.title : a.topic),
      h("p", {}, a.message),
      q
        ? h(
            "p",
            { class: "muted" },
            `${q.question_count} questions · ${q.duration_minutes} minutes · ${q.difficulty}`,
            q.peer_count ? ` · ${q.peer_count} from the channel's question bank, ${q.generated_count} newly generated` : "",
          )
        : null,
      status,
      h(
        "div",
        { class: "row-actions" },
        start,
        // Generated questions join the question bank only once approved here.
        q && q.review_required && q.generation_id
          ? h("a", { class: "button secondary", href: `#/ask/review/${q.generation_id}` }, "Review the generated questions")
          : null,
      ),
    );
  }

  // Generate questions for review (S07.1) and the list of earlier batches.
  async function generationsCard(ui, channels, params) {
    const { h, alertBox, show, field, formData, busy, go } = ui;
    const status = alertBox();
    const difficulty = h("select", { name: "difficulty" }, DIFFICULTIES.map(([v, t]) => h("option", { value: v }, t)));
    difficulty.value = "medium";
    const form = h(
      "form",
      { class: "card generate", novalidate: true },
      h("h2", {}, "Generate practice questions"),
      h("p", { class: "muted" }, "You review each generated question before it is added."),
      field("Subject", h("input", { name: "subject", maxlength: 100 })),
      field("Topic", h("input", { name: "topic", maxlength: 100 })),
      field("Difficulty", difficulty),
      field("How many", h("input", { name: "count", type: "number", min: 5, max: 10, value: 5 })),
      field("Add to channel bank", channelPicker(h, channels, params.get("channel"), "General question bank")),
      status,
      h("button", { type: "submit" }, "Generate"),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button"), async () => {
        const body = formData(form);
        body.count = Number(body.count || 5);
        body.channel_id = body.channel_id ? Number(body.channel_id) : null;
        try {
          const generation = await API.post("/ai/generations", body);
          go(`#/ask/review/${generation.id}`);
        } catch (err) {
          show(status, err.message, "error");
        }
      });
    });
    let earlier = [];
    try {
      earlier = await API.get("/ai/generations");
    } catch {
      earlier = [];
    }
    return h(
      "div",
      {},
      form,
      earlier.length
        ? h(
            "section",
            { class: "card" },
            h("h2", {}, "My generated batches"),
            h(
              "ul",
              { class: "generations" },
              earlier.map((g) => h("li", {}, h("a", { href: `#/ask/review/${g.id}` }, `${g.subject} › ${g.topic}`), h("span", { class: "muted" }, ` · ${g.questions.length} questions, ${g.pending_count} to review`))),
            ),
          )
        : null,
    );
  }

  // The review screen: approve, edit or reject each generated question.
  async function reviewPage(ui, id) {
    const { h, render, alertBox, show, field, busy } = ui;
    let g;
    try {
      g = await API.get(`/ai/generations/${id}`);
    } catch (err) {
      return render(h("h1", {}, "Review questions"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/ask" }, "Back to Ask AI"));
    }
    const status = alertBox();
    const list = h("ol", { class: "review-list" });
    const quizBox = h("div");

    function draw(updated) {
      g = updated;
      list.replaceChildren(...g.questions.map(questionCard));
      quizBox.replaceChildren(g.can_add_to_quiz ? addToQuizForm() : h("p", { class: "muted" }, `${g.pending_count} question${g.pending_count === 1 ? "" : "s"} still to review.`));
    }

    function act(button, work) {
      return busy(button, async () => {
        try {
          draw(await work());
          show(status, "");
        } catch (err) {
          show(status, err.message, "error");
        }
      });
    }

    function questionCard(q) {
      const approve = h("button", { type: "button" }, "Approve");
      const edit = h("button", { type: "button", class: "secondary" }, "Edit");
      const reject = h("button", { type: "button", class: "secondary" }, "Reject");
      approve.addEventListener("click", () => act(approve, () => API.post(`/ai/generations/${g.id}/questions/${q.id}/approve`)));
      reject.addEventListener("click", () => act(reject, () => API.post(`/ai/generations/${g.id}/questions/${q.id}/reject`)));
      const pending = q.review_status === "pending";
      const card = h(
        "li",
        { class: `review-item status-${q.review_status}`, "data-id": String(q.id) },
        h("p", { class: "question-text" }, q.text, " ", h("span", { class: `badge review-${q.review_status}` }, q.review_status), q.edited ? h("span", { class: "badge" }, "edited") : null),
        q.options.length ? h("ol", { type: "A", class: "options" }, q.options.map((o) => h("li", { class: o === q.correct_answer ? "correct" : "" }, o))) : null,
        q.correct_answer ? h("p", { class: "muted" }, `Answer: ${q.correct_answer}`) : null,
        q.explanation ? h("p", { class: "muted" }, q.explanation) : null,
        pending ? h("div", { class: "row-actions" }, approve, edit, reject) : null,
      );
      edit.addEventListener("click", () => {
        const options = h("textarea", { name: "options", rows: 4 }, q.options.join("\n"));
        const form = h(
          "form",
          { class: "edit-question", novalidate: true },
          field("Question", h("textarea", { name: "text", rows: 2 }, q.text)),
          field("Options", options, "One per line"),
          field("Correct answer", h("input", { name: "correct_answer", value: q.correct_answer || "" })),
          field("Explanation", h("textarea", { name: "explanation", rows: 2 }, q.explanation || "")),
          h("button", { type: "submit" }, "Save changes"),
        );
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          const data = Object.fromEntries(new FormData(form).entries());
          data.options = data.options.split("\n").map((o) => o.trim()).filter(Boolean);
          act(form.querySelector("button"), () => API.put(`/ai/generations/${g.id}/questions/${q.id}`, data));
        });
        card.append(form);
        edit.disabled = true;
      });
      return card;
    }

    function addToQuizForm() {
      const note = alertBox();
      const form = h(
        "form",
        { class: "card add-to-quiz", novalidate: true },
        h("h2", {}, "Add approved questions to a quiz"),
        field("Quiz title", h("input", { name: "title", maxlength: 200, placeholder: `${g.topic} practice` })),
        field("Duration (minutes)", h("input", { name: "duration_minutes", type: "number", min: 1, value: 15 })),
        note,
        h("button", { type: "submit" }, "Create quiz"),
      );
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        busy(form.querySelector("button"), async () => {
          const data = Object.fromEntries(new FormData(form).entries());
          try {
            const made = await API.post(`/ai/generations/${g.id}/quiz`, { title: blankToNull(data.title), duration_minutes: Number(data.duration_minutes || 15) });
            show(note, made.message, "success");
            note.append(" ", h("a", { href: "#/quizzes" }, "Go to my quizzes"));
          } catch (err) {
            show(note, err.message, "error");
          }
        });
      });
      return form;
    }

    render(
      h("p", {}, h("a", { href: "#/ask" }, "← Ask AI")),
      h("h1", {}, `Review questions: ${g.subject} › ${g.topic}`),
      h("p", { class: "muted" }, "Approve, edit or reject each generated question. Only approved questions are added."),
      status,
      h("section", { class: "card" }, list),
      quizBox,
    );
    draw(g);
  }

  // ------------------------------------------------ notifications (S14.1) ----

  window.StudyHubBell = async (signedIn) => {
    const count = document.getElementById("bell-count");
    if (!count) return;
    if (!signedIn) {
      count.hidden = true;
      return;
    }
    try {
      const { unread_count: n } = await API.get("/notifications/unread-count");
      count.textContent = n > 99 ? "99+" : String(n);
      count.hidden = !n;
      document.getElementById("bell").setAttribute("aria-label", n ? `Notifications, ${n} unread` : "Notifications");
    } catch {
      count.hidden = true;
    }
  };

  pages.notifications = async (ui) => {
    const { h, render, alertBox, show, busy, go } = ui;
    const [feed, prefs] = await Promise.all([API.get("/notifications?limit=50"), API.get("/notifications/preferences")]);
    const status = alertBox();
    const list = h(
      "ul",
      { class: "notifications" },
      feed.items.length
        ? feed.items.map((n) => {
            const href = uiLink(n.link);
            const row = h(
              "li",
              { class: `notification ${n.is_read ? "" : "unread"}`, "data-kind": n.kind },
              h("strong", {}, n.title),
              n.body ? h("p", {}, n.body) : null,
              h("span", { class: "muted" }, when(n.created_at)),
            );
            row.addEventListener("click", async () => {
              if (!n.is_read) {
                await API.post(`/notifications/${n.id}/read`).catch(() => {});
                n.is_read = true;
                row.classList.remove("unread");
                window.StudyHubBell(true);
              }
              if (href) go(href);
            });
            if (href) row.classList.add("clickable");
            return row;
          })
        : [h("li", { class: "empty muted" }, "You have no notifications.")],
    );
    const readAll = h("button", { type: "button", class: "secondary" }, "Mark all as read");
    readAll.addEventListener("click", () =>
      busy(readAll, async () => {
        await API.post("/notifications/read-all");
        list.querySelectorAll(".unread").forEach((el) => el.classList.remove("unread"));
        window.StudyHubBell(true);
      }),
    );

    // Notification Settings (UT_28): switching a category off stops it at once.
    const settings = h(
      "fieldset",
      { class: "card notification-settings" },
      h("legend", {}, "Notification settings"),
      prefs.map((p) => {
        const box = h("input", { type: "checkbox", name: p.kind, checked: p.enabled });
        box.addEventListener("change", async () => {
          box.disabled = true;
          try {
            await API.put("/notifications/preferences", { preferences: { [p.kind]: box.checked } });
            show(status, `${p.label} ${box.checked ? "on" : "off"}`, "success");
          } catch (err) {
            box.checked = !box.checked;
            show(status, err.message, "error");
          } finally {
            box.disabled = false;
          }
        });
        return h("label", { class: "inline" }, box, ` ${p.label}`);
      }),
      status,
    );
    render(
      h("h1", {}, "Notifications"),
      h("div", { class: "grid" }, h("section", { class: "card" }, h("div", { class: "row-actions" }, readAll), list), settings),
    );
  };

  // --------------------------------------------------- moderation (S13.2) ----
  // IT_25/IT_26: pending reports with content, reporter and reason; remove or dismiss.

  pages.moderation = async (ui) => {
    const { h, render, alertBox, show, busy } = ui;
    let queue;
    try {
      queue = await API.get("/moderation/queue");
    } catch (err) {
      return render(h("h1", {}, "Moderation"), h("p", { class: "alert error" }, err.message));
    }
    const status = alertBox();
    const items = queue.map((item) => {
      const note = alertBox();
      const reportId = item.reports[0].report_id;
      const remove = h("button", { type: "button" }, "Remove Content");
      const dismiss = h("button", { type: "button", class: "secondary" }, "Dismiss");
      const buttons = h("div", { class: "row-actions" }, item.content.available && (item.target_type === "material" || item.target_type === "question") ? remove : null, dismiss);
      const done = (result, text) => {
        show(note, `${text} · Status: ${result.status}`, "success");
        buttons.remove();
        row.classList.add("closed");
      };
      remove.addEventListener("click", () => {
        if (!window.confirm("Remove this content from the platform?")) return;
        busy(remove, async () => {
          try {
            done(await API.post(`/moderation/reports/${reportId}/remove`, { confirm: true }), "Content removed");
          } catch (err) {
            show(note, err.message, "error");
          }
        });
      });
      dismiss.addEventListener("click", () =>
        busy(dismiss, async () => {
          try {
            done(await API.post(`/moderation/reports/${reportId}/dismiss`, { note: null }), "Report dismissed");
          } catch (err) {
            show(note, err.message, "error");
          }
        }),
      );
      const href = uiLink(item.content.link);
      const title = item.content.title || `${TARGET_LABEL[item.target_type] || item.target_type} #${item.target_id}`;
      const row = h(
        "li",
        { class: "item report-item", "data-target": `${item.target_type}:${item.target_id}` },
        h("div", {},
          h("span", { class: "badge" }, TARGET_LABEL[item.target_type] || item.target_type), " ",
          href ? h("a", { href, class: "content-title" }, title) : h("strong", { class: "content-title" }, title),
          item.content.available ? null : h("span", { class: "badge pending" }, "removed"),
          h("p", { class: "muted" }, [item.channel_name, `${item.report_count} report${item.report_count === 1 ? "" : "s"}`, `first reported ${when(item.first_reported_at)}`].filter(Boolean).join(" · ")),
          h("ul", { class: "reporters" }, item.reports.map((r) => h("li", {}, h("strong", {}, r.reporter.name || "Unknown"), ` — ${label(REPORT_REASONS, r.reason)}`, r.details ? `: ${r.details}` : "", h("span", { class: "muted" }, ` · ${when(r.reported_at)}`)))),
          note),
        buttons,
      );
      return row;
    });
    render(
      h("h1", {}, "Moderation"),
      h("p", { class: "muted" }, "Pending reports, one entry per reported item. Removing or dismissing closes every report on it."),
      status,
      h("section", { class: "card" }, items.length ? h("ul", { class: "items reports" }, items) : h("p", { class: "empty muted" }, "No pending reports.")),
    );
  };

  // -------------------------------------------------------- admin (S14.2) ----
  // IT_27 suspend, ST_27 channel requests, ST_28 remove flagged content; anyone who
  // is not an admin sees the API's "Unauthorized" and nothing else (IT_28).

  pages.admin = async (ui) => {
    const { h, render, alertBox, show, busy } = ui;
    let dash;
    try {
      dash = await API.get("/admin/dashboard");
    } catch (err) {
      return render(h("h1", {}, "Admin"), h("p", { class: "alert error unauthorized" }, err.message));
    }
    const tiles = h(
      "div",
      { class: "tiles" },
      [["Users", dash.total_users], ["Suspended", dash.suspended_users], ["Open reports", dash.open_reports], ["Flagged items", dash.flagged_items], ["Channel requests", dash.pending_channel_requests]].map(([k, v]) => h("div", { class: "tile" }, h("span", { class: "muted" }, k), h("strong", {}, String(v)))),
    );

    // User Management
    const userStatus = alertBox();
    const users = h("ul", { class: "items admin-users" });
    const q = h("input", { name: "q", type: "search", placeholder: "Name, username or email", maxlength: 100 });
    const filter = h("select", { name: "status" }, h("option", { value: "" }, "Any status"), h("option", { value: "active" }, "Active"), h("option", { value: "suspended" }, "Suspended"));
    const search = h("form", { class: "search", role: "search" }, q, filter, h("button", { type: "submit" }, "Search"));
    async function loadUsers() {
      const query = new URLSearchParams();
      if (q.value.trim()) query.set("q", q.value.trim());
      if (filter.value) query.set("status", filter.value);
      try {
        const rows = await API.get(`/admin/users${query.toString() ? `?${query}` : ""}`);
        users.replaceChildren(...(rows.length ? rows.map(userRow) : [h("li", { class: "empty muted" }, "No users match.")]));
      } catch (err) {
        show(userStatus, err.message, "error");
      }
    }
    function userRow(u) {
      const action = u.status === "Suspended" ? h("button", { type: "button", class: "secondary" }, "Reinstate") : h("button", { type: "button" }, "Suspend");
      action.addEventListener("click", () => {
        const suspending = u.status !== "Suspended";
        if (suspending && !window.confirm(`Suspend ${u.display_name}? They will not be able to log in.`)) return;
        busy(action, async () => {
          try {
            const updated = suspending ? await API.post(`/admin/users/${u.id}/suspend`, { confirm: true, reason: null }) : await API.post(`/admin/users/${u.id}/reinstate`, {});
            row.replaceWith(userRow(updated));
            show(userStatus, `${updated.display_name} is now ${updated.status}`, "success");
          } catch (err) {
            show(userStatus, err.message, "error");
          }
        });
      });
      const row = h(
        "li",
        { class: "item", "data-user": u.username || String(u.id) },
        h("div", {}, h("strong", {}, u.display_name), u.username ? h("span", { class: "muted" }, ` @${u.username}`) : null, h("div", { class: "muted" }, `${u.email} · ${u.role.replace("_", " ")}`)),
        h("div", { class: "row-actions" }, h("span", { class: `badge status-${u.status.toLowerCase()}` }, u.status), u.role === "admin" ? null : action),
      );
      return row;
    }
    search.addEventListener("submit", (event) => {
      event.preventDefault();
      loadUsers();
    });

    // Channel Requests
    const requestStatus = alertBox();
    const requests = h("ul", { class: "items channel-requests" });
    async function loadRequests() {
      const rows = await API.get("/admin/channel-requests");
      requests.replaceChildren(...(rows.length ? rows.map(requestRow) : [h("li", { class: "empty muted" }, "No pending Authorized Channel requests.")]));
    }
    function requestRow(r) {
      const approve = h("button", { type: "button" }, "Approve");
      const reject = h("button", { type: "button", class: "secondary" }, "Reject");
      const decide = (button, verb) =>
        busy(button, async () => {
          try {
            const body = { note: null };
            const channel = verb === "approve" ? await API.post(`/channels/${r.id}/approve`, body) : await API.post(`/channels/${r.id}/reject`, body);
            show(requestStatus, `"${channel.name}" is now ${channel.approval_status}`, "success");
            await loadRequests();
          } catch (err) {
            show(requestStatus, err.message, "error");
          }
        });
      approve.addEventListener("click", () => decide(approve, "approve"));
      reject.addEventListener("click", () => decide(reject, "reject"));
      return h(
        "li",
        { class: "item", "data-channel": r.name },
        h("div", {}, h("strong", {}, r.name), h("div", { class: "muted" }, [r.subject, r.topic, r.requester ? `requested by ${r.requester.display_name}` : null, when(r.requested_at)].filter(Boolean).join(" · ")), r.description ? h("p", {}, r.description) : null),
        h("div", { class: "row-actions" }, approve, reject),
      );
    }

    // Flagged content (ST_28): removal needs a reason, which the poster is told.
    const flaggedStatus = alertBox();
    const flagged = h("ul", { class: "items flagged" });
    async function loadFlagged() {
      const rows = await API.get("/admin/reports");
      flagged.replaceChildren(...(rows.length ? rows.map(flaggedRow) : [h("li", { class: "empty muted" }, "No flagged content.")]));
    }
    function flaggedRow(f) {
      const reason = h("input", { name: "reason", value: "Rule violation", maxlength: 500, "aria-label": "Reason for removal" });
      const remove = h("button", { type: "button" }, "Remove Content");
      remove.addEventListener("click", () => {
        if (!window.confirm("Remove this content platform-wide? The poster will be notified.")) return;
        busy(remove, async () => {
          try {
            const result = await API.post(`/admin/content/${f.target_type}/${f.target_id}/remove`, { reason: reason.value });
            show(flaggedStatus, result.message, "success");
            await Promise.all([loadFlagged(), loadAudit()]);
          } catch (err) {
            show(flaggedStatus, err.message, "error");
          }
        });
      });
      return h(
        "li",
        { class: "item" },
        h("div", {}, h("span", { class: "badge" }, TARGET_LABEL[f.target_type] || f.target_type), " ", h("strong", {}, f.title || `#${f.target_id}`), h("div", { class: "muted" }, `${f.report_count} reports · ${f.reasons.map((r) => label(REPORT_REASONS, r)).join(", ")}${f.poster ? ` · posted by ${f.poster.display_name}` : ""}`)),
        f.removable ? h("div", { class: "row-actions wrap" }, reason, remove) : null,
      );
    }

    // Audit log
    const audit = h("ol", { class: "audit-log" });
    async function loadAudit() {
      const log = await API.get("/admin/audit-log?limit=20");
      audit.replaceChildren(...(log.items.length ? log.items.map((e) => h("li", {}, h("strong", {}, e.action.replace(/_/g, " ")), ` ${TARGET_LABEL[e.target_type] || e.target_type} #${e.target_id} by ${e.actor.display_name || "admin"}`, e.note ? ` — ${e.note}` : "", h("span", { class: "muted" }, ` · ${when(e.created_at)}`))) : [h("li", { class: "empty muted" }, "Nothing logged yet.")]));
    }

    render(
      h("h1", {}, "Admin"),
      tiles,
      h("section", { class: "card" }, h("h2", {}, "User management"), search, userStatus, users),
      h("section", { class: "card" }, h("h2", {}, "Channel requests"), requestStatus, requests),
      h("section", { class: "card" }, h("h2", {}, "Flagged content"), flaggedStatus, flagged),
      h("section", { class: "card" }, h("h2", {}, "Admin action log"), audit),
    );
    await Promise.all([loadUsers(), loadRequests(), loadFlagged(), loadAudit()]);
  };
})();
