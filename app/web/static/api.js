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

  async function send(method, path, body, accept = "application/json") {
    const headers = { Accept: accept };
    const t = token();
    if (t) headers.Authorization = `Bearer ${t}`;
    const multipart = body instanceof FormData; // the browser sets the boundary itself
    if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(BASE + path, {
        method,
        headers,
        body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
      });
    } catch {
      throw new ApiError(0, "Cannot reach the server. Check your connection.", null);
    }
    return response;
  }

  async function fail(response) {
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (response.status === 401 && token()) {
      setToken(null);
      window.dispatchEvent(new Event("studyhub:signed-out"));
    }
    throw new ApiError(response.status, messageFrom(data, response.status), data);
  }

  // A file endpoint (preview, download) needs the bearer token, so it is fetched as a
  // Blob instead of being opened as a plain link.
  async function blob(path) {
    const response = await send("GET", path, undefined, "*/*");
    if (!response.ok) return fail(response);
    return response.blob();
  }

  async function request(method, path, body) {
    const t = token();
    const response = await send(method, path, body);
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
    put: (path, body) => request("PUT", path, body),
    patch: (path, body) => request("PATCH", path, body),
    del: (path) => request("DELETE", path),
    // UI-2: multipart upload (a FormData body) and authenticated file downloads.
    upload: (path, form) => request("POST", path, form),
    blob,
  };
})();
