// Thin client for the /api/v1 endpoints. Every message shown to the user comes from
// the API's "detail" field, so the UI repeats the Test Plan wording exactly.
"use strict";

const API = (() => {
  const BASE = "/api/v1";
  const TOKEN_KEY = "studyhub.token";

  function storage() {
    try {
      return window.sessionStorage;
    } catch {
      return null;
    }
  }

  function token() {
    const s = storage();
    return s ? s.getItem(TOKEN_KEY) : null;
  }

  function setToken(value) {
    const s = storage();
    if (!s) return;
    if (value) s.setItem(TOKEN_KEY, value);
    else s.removeItem(TOKEN_KEY);
  }

  class ApiError extends Error {
    constructor(status, message, body) {
      super(message);
      this.status = status;
      this.body = body;
    }
  }

  function messageFrom(body, status) {
    if (body && typeof body.detail === "string") return body.detail;
    if (body && Array.isArray(body.detail) && body.detail[0] && body.detail[0].msg) {
      return body.detail[0].msg;
    }
    return status >= 500 ? "Something went wrong. Please try again." : "Request failed";
  }

  async function request(method, path, body) {
    const headers = { Accept: "application/json" };
    const t = token();
    if (t) headers.Authorization = `Bearer ${t}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(BASE + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, "Cannot reach the server. Check your connection.", null);
    }
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
    if (!response.ok) {
      if (response.status === 401 && t) {
        setToken(null); // expired or revoked: send the user back to sign in
        window.dispatchEvent(new Event("studyhub:signed-out"));
      }
      throw new ApiError(response.status, messageFrom(data, response.status), data);
    }
    return data;
  }

  return {
    ApiError,
    token,
    setToken,
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    patch: (path, body) => request("PATCH", path, body),
    del: (path) => request("DELETE", path),
  };
})();
