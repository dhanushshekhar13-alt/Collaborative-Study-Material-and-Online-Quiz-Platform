// StudyHub web UI (UI-1): register, log in, profile, channels; S12.1: performance; S12.2: recommended;
// NFR-03: the dashboard every signed-in user lands on.
// UI-2's materials, question bank and quiz screens live in study.js, and UI-3's chat,
// messages, Ask AI, notifications, moderation and admin screens in community.js; both
// register in window.StudyHubPages and the router below hands them the shared helpers.
// Hash routes keep this a set of static files with no build step. All text from the
// server is inserted with textContent, never as HTML.
"use strict";

(() => {
  const view = document.getElementById("view");
  const nav = document.getElementById("nav");
  const whoami = document.getElementById("whoami");
  let me = null; // { account, capabilities }

  // ------------------------------------------------------------------ helpers --

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "class") el.className = value;
      else if (value === true) el.setAttribute(key, "");
      else el.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  function render(...nodes) {
    // A page may pass null for a section it leaves out; replaceChildren would print it.
    view.replaceChildren(...nodes.filter((node) => node !== null && node !== undefined && node !== false));
    view.focus();
  }

  function alertBox(kind = "error") {
    return h("p", { class: `alert ${kind}`, role: kind === "error" ? "alert" : "status", hidden: true });
  }

  function show(box, message, kind) {
    if (kind) box.className = `alert ${kind}`;
    box.textContent = message;
    box.hidden = !message;
  }

  function field(label, input, hint) {
    return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", { class: "muted" }, hint) : null);
  }

  function formData(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  async function busy(button, work) {
    button.disabled = true;
    try {
      return await work();
    } finally {
      button.disabled = false;
    }
  }

  function typeLabel(channel) {
    // type_label / type_style come from the API so every screen labels the same way.
    return h(
      "span",
      { class: `badge ${channel.type_style}`, title: channel.is_verified ? "Verified Authorized Channel" : null },
      channel.type_label,
      channel.is_verified ? " ✓" : null,
    );
  }

  function can(capability) {
    return !!me && me.capabilities.includes(capability);
  }

  function go(hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  }

  // -------------------------------------------------------------- session ----

  async function loadMe() {
    if (!API.token()) {
      me = null;
      return null;
    }
    try {
      const [account, permissions] = await Promise.all([
        API.get("/accounts/me"),
        API.get("/accounts/me/permissions"),
      ]);
      me = { account, capabilities: permissions.capabilities };
    } catch {
      API.setToken(null);
      me = null;
    }
    return me;
  }

  function updateNav() {
    nav.hidden = !me;
    whoami.textContent = me ? `${me.account.display_name} (${me.account.role.replace("_", " ")})` : "";
    const current = (location.hash.split("/")[1] || "").split("?")[0];
    for (const link of nav.querySelectorAll("[data-nav]")) {
      link.classList.toggle("active", link.dataset.nav === current);
    }
    // Links to role pages show only to roles that can use them; the API still refuses
    // anyone else (a direct URL shows its "Unauthorized").
    for (const link of nav.querySelectorAll("[data-cap]")) {
      link.hidden = !link.dataset.cap.split(" ").some(can);
    }
    if (window.StudyHubBell) window.StudyHubBell(!!me);
  }

  document.getElementById("logout").addEventListener("click", () => {
    API.setToken(null);
    me = null;
    go("#/login");
  });

  window.addEventListener("studyhub:signed-out", () => {
    me = null;
    if (!location.hash.startsWith("#/login")) go("#/login?expired=1");
  });

  // ---------------------------------------------------------------- pages ----

  function registerPage() {
    const status = alertBox();
    const form = h(
      "form",
      { class: "card narrow", novalidate: true },
      h("h1", {}, "Create an account"),
      status,
      field("Full name", h("input", { name: "display_name", required: true, maxlength: 100, autocomplete: "name" })),
      field("Username", h("input", { name: "username", required: true, minlength: 3, maxlength: 50, autocomplete: "username" }), "Letters, digits, dot, dash or underscore"),
      field("Email", h("input", { name: "email", type: "text", inputmode: "email", required: true, autocomplete: "email" })),
      field("Password", h("input", { name: "password", type: "password", required: true, minlength: 8, autocomplete: "new-password" }), "At least 8 characters"),
      h("button", { type: "submit" }, "Register"),
      h("p", { class: "muted" }, "Already registered? ", h("a", { href: "#/login" }, "Log in")),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button"), async () => {
        try {
          const result = await API.post("/accounts/register", formData(form));
          form.reset();
          show(status, result.message, "success"); // "Registration successful"
          status.append(" ", h("a", { href: "#/login" }, "Log in now"));
        } catch (err) {
          show(status, err.message, "error"); // e.g. "Enter a valid email address"
        }
      });
    });
    render(form);
  }

  function loginPage(params) {
    const status = alertBox();
    const form = h(
      "form",
      { class: "card narrow", novalidate: true },
      h("h1", {}, "Log in"),
      status,
      field("Username or email", h("input", { name: "username", required: true, autocomplete: "username" })),
      field("Password", h("input", { name: "password", type: "password", required: true, autocomplete: "current-password" })),
      h("button", { type: "submit" }, "Log in"),
      h("p", { class: "muted" }, "New here? ", h("a", { href: "#/register" }, "Create an account")),
    );
    if (params.get("expired")) show(status, "Your session has ended. Please log in again.", "info");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button"), async () => {
        const body = formData(form);
        if (!body.username.trim() || !body.password) {
          show(status, "Enter your username and password", "error");
          return;
        }
        try {
          const result = await API.post("/accounts/login", body);
          API.setToken(result.access_token);
          await loadMe();
          go("#/dashboard");
        } catch (err) {
          show(status, err.message, "error"); // "Invalid username or password"
        }
      });
    });
    render(form);
  }

  async function profilePage() {
    const profile = await API.get("/profiles/me");
    const status = alertBox();
    const form = h(
      "form",
      { class: "card", novalidate: true },
      h("h2", {}, "Edit profile"),
      status,
      field("Display name", h("input", { name: "display_name", required: true, maxlength: 100, value: profile.display_name })),
      field("Institution", h("input", { name: "institution", maxlength: 150, value: profile.institution || "" })),
      field("Bio", h("textarea", { name: "bio", maxlength: 500, rows: 4 }, profile.bio || ""), "Up to 500 characters"),
      h("button", { type: "submit" }, "Save profile"),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button"), async () => {
        try {
          const updated = await API.patch("/profiles/me", formData(form));
          show(status, "Profile saved", "success");
          details.replaceWith((details = profileDetails(updated)));
          if (me) me.account.display_name = updated.display_name;
          updateNav();
        } catch (err) {
          show(status, err.message, "error");
        }
      });
    });
    let details = profileDetails(profile);
    render(h("h1", {}, "My profile"), h("div", { class: "grid" }, details, form));
  }

  function profileDetails(p) {
    const c = p.contributions || {};
    const rows = [
      ["Username", p.username || "—"],
      ["Role", p.role.replace("_", " ")],
      ["Institution", p.institution || "—"],
      ["Member since", new Date(p.member_since).toLocaleDateString()],
      ["Reputation", p.reputation],
      ["Materials", c.materials ?? 0],
      ["Questions", c.questions ?? 0],
      ["Quizzes", c.quizzes ?? 0],
    ];
    return h(
      "section",
      { class: "card" },
      p.picture_url ? h("img", { class: "avatar", src: p.picture_url, alt: "" }) : null,
      h("h2", {}, p.display_name),
      h("p", { class: p.bio ? "" : "muted" }, p.bio || "No bio yet."),
      h("dl", { class: "facts" }, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))])),
      p.badges && p.badges.length
        ? h("p", {}, "Badges: ", p.badges.map((b) => h("span", { class: "badge" }, b.name || b)))
        : null,
    );
  }

  // ---------------------------------------------------------- leaderboard ----
  // S13.1: contributors ranked by reputation, with the caller's own standing.

  async function leaderboardPage() {
    const board = await API.get("/leaderboard?limit=50");
    const mine = board.me.rank
      ? `You are ranked #${board.me.rank} with ${board.me.reputation} reputation.`
      : "Earn reputation when peers rate your materials and questions 4 stars or more.";
    const rows = board.entries.map((e) =>
      h(
        "tr",
        { class: me && e.user_id === me.account.id ? "mine" : "" },
        h("td", {}, `#${e.rank}`),
        h("td", {}, e.display_name),
        h("td", {}, String(e.reputation)),
        h("td", {}, e.badges.map((code) => h("span", { class: "badge" }, code.replace(/-/g, " ")))),
      ),
    );
    render(
      h("h1", {}, "Leaderboard"),
      h("p", { class: "muted" }, mine),
      rows.length
        ? h(
            "table",
            { class: "leaderboard" },
            h("thead", {}, h("tr", {}, ["Rank", "Contributor", "Reputation", "Badges"].map((t) => h("th", {}, t)))),
            h("tbody", {}, rows),
          )
        : h("p", { class: "empty muted" }, "No one has earned reputation yet."),
    );
  }

  function channelRow(channel, { onChange } = {}) {
    const actions = h("div", { class: "row-actions" });
    const status = alertBox();
    if (channel.my_role) {
      actions.append(h("span", { class: "muted" }, channel.my_role === "owner" ? "Owner" : "Joined"));
      if (channel.my_role !== "owner") actions.append(leaveButton(channel, status, onChange));
    } else {
      actions.append(joinButton(channel, status, onChange));
    }
    return h(
      "li",
      { class: "channel" },
      h(
        "div",
        {},
        h("a", { href: `#/channels/${channel.id}`, class: "channel-name" }, channel.name),
        " ",
        typeLabel(channel),
        channel.approval_status !== "approved" ? h("span", { class: "badge pending" }, channel.approval_status) : null,
        h("div", { class: "muted" }, [channel.subject, channel.topic].filter(Boolean).join(" · "), ` · ${channel.member_count} member${channel.member_count === 1 ? "" : "s"}`),
        status,
      ),
      actions,
    );
  }

  function joinButton(channel, status, onChange) {
    const button = h("button", { type: "button" }, "Join");
    button.addEventListener("click", () =>
      busy(button, async () => {
        try {
          await API.post(`/channels/${channel.id}/join`);
          if (onChange) onChange();
        } catch (err) {
          show(status, err.message, "error");
        }
      }),
    );
    return button;
  }

  function leaveButton(channel, status, onChange) {
    const button = h("button", { type: "button", class: "secondary" }, "Leave");
    button.addEventListener("click", () => {
      if (!window.confirm(`Leave "${channel.name}"?`)) return;
      busy(button, async () => {
        try {
          await API.post(`/channels/${channel.id}/leave`);
          if (onChange) onChange();
        } catch (err) {
          show(status, err.message, "error");
        }
      });
    });
    return button;
  }

  function createChannelForm(onCreated) {
    const status = alertBox();
    const canAuthorize = can("channel:create_authorized");
    const typeSelect = h(
      "select",
      { name: "channel_type" },
      h("option", { value: "normal" }, "Normal"),
      h("option", { value: "authorized", disabled: !canAuthorize }, canAuthorize ? "Authorized" : "Authorized (authorized users only)"),
    );
    const form = h(
      "form",
      { class: "card", novalidate: true },
      h("h2", {}, "Create a channel"),
      status,
      field("Name", h("input", { name: "name", required: true, maxlength: 120 })),
      field("Subject", h("input", { name: "subject", maxlength: 100 })),
      field("Topic", h("input", { name: "topic", maxlength: 100 })),
      field("Description", h("textarea", { name: "description", maxlength: 2000, rows: 3 })),
      field("Type", typeSelect, canAuthorize ? "Authorized Channels need admin approval" : "Only authorized users and admins can create Authorized Channels"),
      h("button", { type: "submit" }, "Create channel"),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      busy(form.querySelector("button"), async () => {
        const body = formData(form);
        for (const key of ["subject", "topic", "description"]) if (!body[key].trim()) body[key] = null;
        try {
          const channel = await API.post("/channels", body);
          form.reset();
          const pending = channel.approval_status === "pending";
          show(status, pending ? `"${channel.name}" was sent to an admin for approval` : `Created "${channel.name}"`, "success");
          if (onCreated) onCreated(channel);
        } catch (err) {
          show(status, err.message, "error");
        }
      });
    });
    return form;
  }

  async function channelsPage(params) {
    const list = h("ul", { class: "channels" });
    const status = alertBox();
    const q = h("input", { name: "q", type: "search", placeholder: "Search by name, subject or topic", value: params.get("q") || "", maxlength: 100 });
    const type = h(
      "select",
      { name: "type" },
      h("option", { value: "" }, "All types"),
      h("option", { value: "normal" }, "Normal"),
      h("option", { value: "authorized" }, "Authorized"),
    );
    type.value = params.get("type") || "";
    const search = h("form", { class: "search", role: "search" }, q, type, h("button", { type: "submit" }, "Search"));

    async function load() {
      const query = new URLSearchParams();
      if (q.value.trim()) query.set("q", q.value.trim());
      if (type.value) query.set("type", type.value);
      try {
        const channels = await API.get(`/channels${query.toString() ? `?${query}` : ""}`);
        show(status, "");
        list.replaceChildren(
          ...(channels.length
            ? channels.map((c) => channelRow(c, { onChange: load }))
            : [h("li", { class: "empty muted" }, "No channels match your search.")]),
        );
      } catch (err) {
        show(status, err.message, "error");
      }
    }

    search.addEventListener("submit", (event) => {
      event.preventDefault();
      load();
    });
    render(
      h("h1", {}, "Channels"),
      h("div", { class: "grid" }, h("section", { class: "card" }, search, status, list), createChannelForm(load)),
    );
    await load();
  }

  async function myChannelsPage() {
    const list = h("ul", { class: "channels" });
    async function load() {
      const channels = await API.get("/channels/mine");
      list.replaceChildren(
        ...(channels.length
          ? channels.map((c) => channelRow(c, { onChange: load }))
          : [h("li", { class: "empty muted" }, "You have not joined any channels yet. ", h("a", { href: "#/channels" }, "Find a channel"))]),
      );
    }
    render(h("h1", {}, "My Channels"), h("section", { class: "card" }, list));
    await load();
  }

  async function channelPage(id, params) {
    const status = alertBox();
    let channel;
    try {
      channel = await API.get(`/channels/${id}`);
    } catch (err) {
      render(h("h1", {}, "Channel"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/channels" }, "Back to channels"));
      return;
    }
    const reload = () => channelPage(id, params);
    const actions = h("div", { class: "row-actions" });
    if (!channel.my_role) actions.append(joinButton(channel, status, reload));
    else if (channel.my_role !== "owner") actions.append(leaveButton(channel, status, reload));
    else actions.append(h("span", { class: "muted" }, "You own this channel"));

    const members = h("ul", { class: "members" });
    if (channel.my_role || can("channel:approve")) {
      try {
        const rows = await API.get(`/channels/${id}/members`);
        members.replaceChildren(
          ...rows.map((m) => h("li", {}, h("a", { href: `#/users/${m.user_id}` }, m.display_name), m.username ? h("span", { class: "muted" }, ` @${m.username}`) : null, " ", h("span", { class: `badge role-${m.role}` }, m.role))),
        );
      } catch (err) {
        members.replaceChildren(h("li", { class: "muted" }, err.message));
      }
    } else {
      members.replaceChildren(h("li", { class: "muted" }, "Join the channel to see its members."));
    }

    render(
      h("p", {}, h("a", { href: "#/channels" }, "← Channels")),
      h(
        "section",
        { class: "card" },
        h("h1", {}, channel.name, " ", typeLabel(channel)),
        channel.approval_status !== "approved" ? h("p", { class: "alert info" }, `Approval status: ${channel.approval_status}`) : null,
        h("p", { class: "muted" }, [channel.subject, channel.topic].filter(Boolean).join(" · ")),
        channel.description ? h("p", {}, channel.description) : null,
        h("p", {}, `${channel.member_count} member${channel.member_count === 1 ? "" : "s"}`),
        h("p", { class: "channel-links" },
          h("a", { href: `#/materials?channel=${channel.id}` }, "Materials"), " · ",
          h("a", { href: `#/questions?channel=${channel.id}` }, "Question bank"), " · ",
          h("a", { href: `#/quizzes?channel=${channel.id}` }, "Quizzes"), " · ",
          h("a", { href: `#/ask?channel=${channel.id}` }, "Ask AI in this channel")),
        status,
        actions,
      ),
      // UI-3: the channel chat; the API refuses non-members (IT_20).
      window.StudyHubChat ? window.StudyHubChat(ui, channel, params) : null,
      h("section", { class: "card" }, h("h2", {}, "Members"), members),
    );
  }

  // ------------------------------------------------- performance (S12.1) ----
  // Charts are inline SVG built with createElementNS; text goes in via textContent.

  const SVG_NS = "http://www.w3.org/2000/svg";

  function s(tag, attrs = {}, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value !== undefined && value !== null) el.setAttribute(key, String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  const STANDING_LABEL = {
    strong: "Strong",
    developing: "Developing",
    weak: "Weak",
    not_enough_data: "Not enough data",
  };
  const DIRECTION_LABEL = {
    improving: "Improving",
    steady: "Steady",
    declining: "Declining",
    not_enough_data: "Not enough attempts for a trend",
  };

  function pct(value) {
    return value === null || value === undefined ? "—" : `${Number(value).toFixed(1).replace(/\.0$/, "")}%`;
  }

  function chart(title, description, svg) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", description);
    svg.prepend(s("title", {}, description));
    return h("figure", { class: "chart" }, h("figcaption", {}, title), svg);
  }

  // Horizontal bars, 0-100%, with optional dashed threshold lines.
  function barChart(rows, { thresholds = [] } = {}) {
    const label = 150, width = 560, bar = 22, gap = 12, top = 8;
    const plot = width - label - 60;
    const height = top + rows.length * (bar + gap) + 18;
    const x = (v) => label + (plot * Math.max(0, Math.min(100, v))) / 100;
    const svg = s("svg", { class: "bars", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "xMinYMin meet" });
    for (const t of thresholds) {
      svg.append(
        s("line", { class: `threshold ${t.cls}`, x1: x(t.value), x2: x(t.value), y1: top - 4, y2: height - 16 }),
        s("text", { class: "axis", x: x(t.value), y: height - 4, "text-anchor": "middle" }, `${t.value}%`),
      );
    }
    rows.forEach((row, i) => {
      const y = top + i * (bar + gap);
      const g = s("g", { class: `bar ${row.cls || ""}`, "data-label": row.label });
      g.append(
        s("text", { class: "label", x: label - 8, y: y + bar / 2 + 5, "text-anchor": "end" }, row.label),
        s("rect", { class: "track", x: label, y, width: plot, height: bar, rx: 4 }),
        s("rect", { class: "fill", x: label, y, width: Math.max(0, x(row.value ?? 0) - label), height: bar, rx: 4 }),
        s("text", { class: "value", x: x(row.value ?? 0) + 6, y: y + bar / 2 + 5 }, pct(row.value)),
        s("title", {}, `${row.label}: ${pct(row.value)}${row.note ? ` — ${row.note}` : ""}`),
      );
      svg.append(g);
    });
    return svg;
  }

  // Each attempt's percentage in order, with the fitted trend line dashed.
  function trendChart(trend) {
    const width = 560, height = 220, left = 40, right = 16, top = 12, bottom = 28;
    const pts = trend.points;
    const n = pts.length;
    const x = (i) => left + (n > 1 ? ((width - left - right) * i) / (n - 1) : (width - left - right) / 2);
    const y = (v) => top + ((height - top - bottom) * (100 - v)) / 100;
    const svg = s("svg", { class: "trend", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "xMinYMin meet" });
    for (const v of [0, 25, 50, 75, 100]) {
      svg.append(
        s("line", { class: "grid-line", x1: left, x2: width - right, y1: y(v), y2: y(v) }),
        s("text", { class: "axis", x: left - 6, y: y(v) + 4, "text-anchor": "end" }, `${v}%`),
      );
    }
    if (trend.line_start !== null && n > 1) {
      svg.append(s("line", { class: "fit", x1: x(0), y1: y(trend.line_start), x2: x(n - 1), y2: y(trend.line_end) }));
    }
    if (n > 1) svg.append(s("polyline", { class: "series", points: pts.map((p, i) => `${x(i)},${y(p.percentage)}`).join(" ") }));
    pts.forEach((p, i) => {
      const when = new Date(p.submitted_at).toLocaleString();
      svg.append(
        s("circle", { class: "point", cx: x(i), cy: y(p.percentage), r: 4.5, "data-attempt": p.attempt_id },
          s("title", {}, `${p.quiz_title} (${p.topic}) — ${pct(p.percentage)} on ${when}`)),
      );
    });
    svg.append(s("text", { class: "axis", x: (left + width - right) / 2, y: height - 6, "text-anchor": "middle" }, "Attempts, oldest to newest"));
    return svg;
  }

  function topicList(title, items, cls, emptyText) {
    return h(
      "section",
      { class: `card standing-list ${cls}` },
      h("h2", {}, title),
      items.length
        ? h("ul", {}, items.map((t) => h("li", {}, h("strong", {}, t.topic), ` (${t.subject}) — ${pct(t.accuracy)} over ${t.attempts} attempts`)))
        : h("p", { class: "muted" }, emptyText),
    );
  }

  // UI-3: the top of Recommended for You, shown on the Performance page.
  async function recommendedPreview() {
    let r;
    try {
      r = await API.get("/analytics/recommendations?limit=5");
    } catch {
      return null;
    }
    return h("section", { class: "card recommended-preview" },
      h("h2", {}, "Recommended for You"),
      h("p", { class: "muted" }, r.message),
      r.items.length
        ? h("ul", {}, r.items.map((item) => h("li", {}, h("span", { class: `badge kind-${item.item_type}` }, KIND_LABEL[item.item_type]), " ", h("strong", {}, item.title), h("span", { class: "muted" }, ` — ${item.reason}`))))
        : h("p", { class: "muted" }, "Nothing to recommend yet."),
      h("a", { href: "#/recommended" }, "See all recommendations"));
  }

  async function performancePage() {
    const [d, recommended] = await Promise.all([API.get("/analytics/dashboard"), recommendedPreview()]);
    if (d.empty) {
      return render(
        h("h1", {}, "My performance"),
        h("section", { class: "card empty-state" },
          h("h2", {}, "Nothing to show yet"),
          h("p", {}, d.empty_message),
          h("a", { href: "#/quizzes" }, "Attempt a quiz"), " · ",
          h("a", { href: "#/channels" }, "Find a channel with quizzes")),
        recommended,
      );
    }
    const t = d.thresholds;
    const tiles = h(
      "div", { class: "tiles" },
      [["Attempts", d.attempts], ["Accuracy", pct(d.accuracy)], ["Points", `${d.total_score} / ${d.total_possible}`],
        ["Trend", DIRECTION_LABEL[d.trend.direction]]].map(([k, v]) => h("div", { class: "tile" }, h("span", { class: "muted" }, k), h("strong", {}, String(v)))),
    );
    const subjectChart = chart(
      "Accuracy by subject",
      "Accuracy by subject: " + d.subjects.map((x) => `${x.subject} ${pct(x.accuracy)}`).join(", "),
      barChart(d.subjects.map((x) => ({ label: x.subject, value: x.accuracy, note: `${x.attempts} attempts` }))),
    );
    const topicChart = chart(
      "Accuracy by topic",
      "Accuracy by topic: " + d.topics.map((x) => `${x.topic} ${pct(x.accuracy)} (${STANDING_LABEL[x.standing]})`).join(", "),
      barChart(
        d.topics.map((x) => ({ label: x.topic, value: x.accuracy, cls: x.standing, note: `${x.subject}, ${STANDING_LABEL[x.standing]}` })),
        { thresholds: [{ value: t.weak_below, cls: "weak" }, { value: t.strong_at, cls: "strong" }] },
      ),
    );
    const legend = h("p", { class: "legend muted" },
      ["strong", "developing", "weak", "not_enough_data"].map((k) => h("span", { class: `key ${k}` }, STANDING_LABEL[k])),
      ` Topics need ${t.min_attempts} attempts before they are labelled.`);
    const overallTrend = chart(
      "Score trend over time",
      `Score trend: ${d.trend.points.map((p) => pct(p.percentage)).join(", ")}. ${DIRECTION_LABEL[d.trend.direction]}.`,
      trendChart(d.trend),
    );
    const subjectTrends = d.subjects.map((x) =>
      h("section", { class: "card" },
        chart(`${x.subject} — ${DIRECTION_LABEL[x.trend.direction]}`,
          `${x.subject} trend: ${x.trend.points.map((p) => pct(p.percentage)).join(", ")}`, trendChart(x.trend))));
    render(
      h("h1", {}, "My performance"),
      tiles,
      h("div", { class: "grid even" },
        topicList("Strengths", d.strengths, "strong", "No strong topics yet."),
        topicList("Weak areas", d.weak_areas, "weak", "No weak topics. Nice work!")),
      h("p", { class: "muted not-enough", hidden: !d.not_enough_data.length },
        "Not enough data yet: ", d.not_enough_data.map((x) => x.topic).join(", ")),
      h("section", { class: "card" }, subjectChart),
      h("section", { class: "card" }, topicChart, legend),
      h("section", { class: "card" }, overallTrend),
      h("h2", {}, "Trend by subject"),
      h("div", { class: "grid even" }, subjectTrends),
      recommended,
    );
  }

  // ----------------------------------------------- recommended (S12.2) ------
  // Recommended for You: items for weak topics; opening one de-prioritises it.

  const KIND_LABEL = { quiz: "Quiz", material: "Material", question: "Question" };

  function recommendationDetail(item, data) {
    if (item.item_type === "material") {
      return h("div", { class: "rec-detail" },
        h("p", {}, data.description || "No description."),
        data.url ? h("a", { href: data.url, target: "_blank", rel: "noopener noreferrer" }, "Open link") : null);
    }
    if (item.item_type === "question") {
      const q = data.question || data;
      return h("div", { class: "rec-detail" },
        h("p", {}, q.text),
        q.options && q.options.length ? h("ol", { type: "A" }, q.options.map((o) => h("li", {}, o))) : null);
    }
    const count = (data.questions || []).length;
    return h("div", { class: "rec-detail" },
      h("p", {}, `${count} questions · ${data.duration_minutes} minutes · ${data.difficulty}`));
  }

  function recommendationRow(item) {
    const status = alertBox();
    const meta = [
      `${item.subject}${item.topic ? ` › ${item.topic}` : ""}`,
      item.rating_average ? `★ ${item.rating_average} (${item.rating_count})` : null,
      item.attempt_count ? `${item.attempt_count} attempt${item.attempt_count === 1 ? "" : "s"}` : null,
      item.difficulty,
    ].filter(Boolean).join(" · ");
    const flag = h("span", { class: "badge consumed", hidden: !item.consumed }, item.item_type === "quiz" ? "Done before" : "Seen");
    const open = h("button", { type: "button", class: "secondary" }, "Open");
    const row = h("li", { class: `rec ${item.consumed ? "is-consumed" : ""}`, "data-type": item.item_type, "data-id": String(item.item_id) },
      h("div", {},
        h("span", { class: `badge kind-${item.item_type}` }, KIND_LABEL[item.item_type]), flag,
        h("strong", { class: "rec-title" }, item.title),
        h("p", { class: "muted rec-meta" }, meta),
        h("p", { class: "rec-reason" }, item.reason),
        status),
      h("div", { class: "row-actions" }, open));
    open.addEventListener("click", () => busy(open, async () => {
      try {
        await API.post("/analytics/recommendations/opened", { item_type: item.item_type, item_id: item.item_id });
        flag.hidden = false;
        row.classList.add("is-consumed");
        const data = await API.get(item.api_path.replace(/^\/api\/v1/, ""));
        row.querySelector(".rec-detail")?.remove();
        row.firstChild.append(recommendationDetail(item, data));
        open.hidden = true;
      } catch (err) {
        show(status, err.message, "error");
      }
    }));
    return row;
  }

  async function recommendedPage() {
    const r = await API.get("/analytics/recommendations");
    const focus = r.focus_topics.length
      ? h("section", { class: "card focus" },
          h("h2", {}, "Your weak topics"),
          h("ul", {}, r.focus_topics.map((f) => h("li", {}, h("strong", {}, f.topic), ` (${f.subject}) — ${f.reason}`))))
      : null;
    render(
      h("h1", {}, "Recommended for You"),
      h("p", { class: `alert ${r.mode === "targeted" ? "info" : "success"}` }, r.message),
      focus,
      r.items.length
        ? h("ul", { class: "recs card" }, r.items.map(recommendationRow))
        : h("p", { class: "empty muted" }, "Nothing to recommend yet. Check back once peers share materials and quizzes."),
    );
  }

  // ------------------------------------------------- dashboard (NFR-03) -----
  // The page a signed-in user lands on (IT_01). Every core flow starts from one link
  // here, so each is at most three clicks away; a new user gets a three-step
  // checklist, and progress is drawn as charts rather than bare numbers.

  // [label, hint, href]; the first link of each core flow. tests/test_usability.py
  // checks each is reachable from here and walks them in the browser.
  const CORE_FLOWS = [
    ["Find a channel", "Search by subject or topic, then join", "#/channels"],
    ["Upload material", "Share notes, slides or a link", "#/materials?upload=1"],
    ["Take a quiz", "Pick a published quiz and start", "#/quizzes"],
    ["Question bank", "Browse, rate or add questions", "#/questions"],
    ["My performance", "Charts by subject, topic and over time", "#/performance"],
    ["Recommended for you", "Materials and quizzes for weak topics", "#/recommended"],
  ];

  function checklist(steps) {
    const done = steps.filter((x) => x.done).length;
    return h(
      "section",
      { class: "card get-started", "aria-labelledby": "get-started-title" },
      h("h2", { id: "get-started-title" }, "Get started"),
      h("p", { class: "muted" }, `${done} of ${steps.length} done. Each step is one click from here.`),
      h("progress", { max: steps.length, value: done, "aria-label": `${done} of ${steps.length} steps done` }),
      h(
        "ol",
        { class: "steps" },
        steps.map((x) =>
          h(
            "li",
            { class: x.done ? "step done" : "step", "data-step": x.key },
            h("span", { class: "step-mark", "aria-hidden": "true" }, x.done ? "✓" : ""),
            h("div", {},
              x.done ? h("strong", {}, x.title) : h("a", { href: x.href }, h("strong", {}, x.title)),
              x.done ? h("span", { class: "visually-hidden" }, " (done)") : null,
              h("div", { class: "muted" }, x.hint)),
          ),
        ),
      ),
    );
  }

  function progressCard(d) {
    if (d.empty) {
      return h(
        "section",
        { class: "card progress-card empty-state" },
        h("h2", {}, "Your progress"),
        h("p", {}, d.empty_message),
        h("a", { href: "#/quizzes", class: "button" }, "Take a quiz"),
      );
    }
    const tiles = h(
      "div", { class: "tiles" },
      [["Attempts", d.attempts], ["Accuracy", pct(d.accuracy)], ["Subjects", d.subjects.length]]
        .map(([k, v]) => h("div", { class: "tile" }, h("span", { class: "muted" }, k), h("strong", {}, String(v)))),
    );
    return h(
      "section",
      { class: "card progress-card" },
      h("h2", {}, "Your progress"),
      tiles,
      chart(
        "Accuracy by subject",
        "Accuracy by subject: " + d.subjects.map((x) => `${x.subject} ${pct(x.accuracy)}`).join(", "),
        barChart(d.subjects.map((x) => ({ label: x.subject, value: x.accuracy, note: `${x.attempts} attempts` }))),
      ),
      chart(
        `Score trend — ${DIRECTION_LABEL[d.trend.direction]}`,
        `Score trend: ${d.trend.points.map((p) => pct(p.percentage)).join(", ")}. ${DIRECTION_LABEL[d.trend.direction]}.`,
        trendChart(d.trend),
      ),
      h("p", {}, h("a", { href: "#/performance" }, "See full performance →")),
    );
  }

  async function dashboardPage() {
    const [profile, channels, performance] = await Promise.all([
      API.get("/profiles/me"),
      API.get("/channels/mine"),
      API.get("/analytics/dashboard"),
    ]);
    const steps = [
      { key: "channel", title: "Join a channel", hint: "Find one for your subject and click Join.", href: "#/channels", done: channels.length > 0 },
      { key: "upload", title: "Upload a study material", hint: "Notes, slides or a link, for everyone or one channel.", href: "#/materials?upload=1", done: profile.contributions.materials > 0 },
      { key: "quiz", title: "Attempt a quiz", hint: "Your score and charts appear here afterwards.", href: "#/quizzes", done: !performance.empty },
    ];
    const actions = h(
      "nav",
      { class: "quick-actions", "aria-label": "Quick actions" },
      CORE_FLOWS.map(([label, hint, href]) => h("a", { href, class: "action" }, h("strong", {}, label), h("span", { class: "muted" }, hint))),
    );
    const mine = h(
      "section",
      { class: "card my-channels-card" },
      h("h2", {}, "My channels"),
      channels.length
        ? h("ul", { class: "links" }, channels.slice(0, 6).map((c) => h("li", {}, h("a", { href: `#/channels/${c.id}` }, c.name), " ", typeLabel(c))))
        : h("p", { class: "muted" }, "You have not joined any channels yet. ", h("a", { href: "#/channels" }, "Find a channel")),
      channels.length > 6 ? h("p", {}, h("a", { href: "#/my-channels" }, `All ${channels.length} channels →`)) : null,
    );
    render(
      h("h1", {}, "Dashboard"),
      h("p", { class: "muted" }, `Welcome, ${profile.display_name}.`),
      steps.every((x) => x.done) ? null : checklist(steps),
      actions,
      h("div", { class: "grid even" }, progressCard(performance), mine),
    );
  }

  // --------------------------------------------------------------- router ----

  // Helpers shared with the pages other scripts register (UI-2, study.js).
  const ui = { h, render, alertBox, show, field, formData, busy, can, go, typeLabel, me: () => me };

  const PUBLIC = new Set(["login", "register"]);

  async function route() {
    const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/").filter(Boolean);
    const page = parts[0] || (API.token() ? "dashboard" : "login");
    const params = new URLSearchParams(query);

    if (!me && API.token()) await loadMe();
    if (!PUBLIC.has(page) && !me) {
      updateNav();
      return loginPage(params);
    }
    if (PUBLIC.has(page) && me) {
      go("#/dashboard");
      return;
    }
    updateNav();
    try {
      if (page === "login") return loginPage(params);
      if (page === "register") return registerPage();
      if (page === "dashboard") return await dashboardPage();
      if (page === "profile") return await profilePage();
      if (page === "my-channels") return await myChannelsPage();
      if (page === "leaderboard") return await leaderboardPage();
      if (page === "performance") return await performancePage();
      if (page === "recommended") return await recommendedPage();
      const extra = Object.prototype.hasOwnProperty.call(window.StudyHubPages || {}, page) ? window.StudyHubPages[page] : null;
      if (extra) return await extra(ui, parts.slice(1), params);
      if (page === "channels" && parts[1]) return await channelPage(encodeURIComponent(parts[1]), params);
      if (page === "channels") return await channelsPage(params);
      render(h("h1", {}, "Page not found"), h("a", { href: "#/dashboard" }, "Go to the dashboard"));
    } catch (err) {
      if (err instanceof API.ApiError && err.status === 401) return;
      render(h("p", { class: "alert error" }, err.message || "Something went wrong"));
    }
  }

  window.addEventListener("hashchange", route);
  route();
})();
