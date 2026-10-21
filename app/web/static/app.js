// StudyHub web UI (UI-1): register, log in, profile, channels.
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
    view.replaceChildren(...nodes);
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
          go("#/channels");
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

  async function channelPage(id) {
    const status = alertBox();
    let channel;
    try {
      channel = await API.get(`/channels/${id}`);
    } catch (err) {
      render(h("h1", {}, "Channel"), h("p", { class: "alert error" }, err.message), h("a", { href: "#/channels" }, "Back to channels"));
      return;
    }
    const reload = () => channelPage(id);
    const actions = h("div", { class: "row-actions" });
    if (!channel.my_role) actions.append(joinButton(channel, status, reload));
    else if (channel.my_role !== "owner") actions.append(leaveButton(channel, status, reload));
    else actions.append(h("span", { class: "muted" }, "You own this channel"));

    const members = h("ul", { class: "members" });
    if (channel.my_role || can("channel:approve")) {
      try {
        const rows = await API.get(`/channels/${id}/members`);
        members.replaceChildren(
          ...rows.map((m) => h("li", {}, m.display_name, m.username ? h("span", { class: "muted" }, ` @${m.username}`) : null, " ", h("span", { class: `badge role-${m.role}` }, m.role))),
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
        status,
        actions,
      ),
      h("section", { class: "card" }, h("h2", {}, "Members"), members),
    );
  }

  // --------------------------------------------------------------- router ----

  const PUBLIC = new Set(["login", "register"]);

  async function route() {
    const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/").filter(Boolean);
    const page = parts[0] || (API.token() ? "channels" : "login");
    const params = new URLSearchParams(query);

    if (!me && API.token()) await loadMe();
    if (!PUBLIC.has(page) && !me) {
      updateNav();
      return loginPage(params);
    }
    if (PUBLIC.has(page) && me) {
      go("#/channels");
      return;
    }
    updateNav();
    try {
      if (page === "login") return loginPage(params);
      if (page === "register") return registerPage();
      if (page === "profile") return await profilePage();
      if (page === "my-channels") return await myChannelsPage();
      if (page === "channels" && parts[1]) return await channelPage(encodeURIComponent(parts[1]));
      if (page === "channels") return await channelsPage(params);
      render(h("h1", {}, "Page not found"), h("a", { href: "#/channels" }, "Go to channels"));
    } catch (err) {
      if (err instanceof API.ApiError && err.status === 401) return;
      render(h("p", { class: "alert error" }, err.message || "Something went wrong"));
    }
  }

  window.addEventListener("hashchange", route);
  route();
})();
