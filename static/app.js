"use strict";
(() => {
  const themeButton = document.getElementById("theme-toggle");
  let theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  try { theme = localStorage.getItem("sentinel-theme") || theme; } catch (_) { /* Private browsing may block storage. */ }
  const applyTheme = () => {
    document.documentElement.dataset.theme = theme;
    themeButton.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    themeButton.setAttribute("aria-label", "Switch to " + (theme === "dark" ? "light" : "dark") + " mode");
  };
  applyTheme();
  themeButton.addEventListener("click", () => {
    theme = theme === "dark" ? "light" : "dark"; applyTheme();
    try { localStorage.setItem("sentinel-theme", theme); } catch (_) { /* Theme still works in memory. */ }
  });
  document.querySelectorAll("[data-demo-account]").forEach(button => button.addEventListener("click", () => {
    const admin = button.dataset.demoAccount === "admin";
    document.getElementById("username").value = admin ? "admin" : "operator";
    document.getElementById("password").value = admin ? "DemoAdmin!2026" : "DemoUser!2026";
    document.getElementById("password").focus();
  }));

  const root = document.getElementById("monitor");
  if (!root) return;
  const q = selector => root.querySelector(selector);
  let state = JSON.parse(q("#initial-state").textContent), busy = false, reachable = true;
  let page = 1, pages = 1, eventsRequest = 0, debounce;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const errorBox = q("#request-error"), message = q("#action-message");
  const formatTime = value => new Date(value).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  const showError = text => {errorBox.textContent = text; errorBox.hidden = false;};

  async function api(url, payload) {
    let response;
    try {
      response = await fetch(url, {credentials: "same-origin", signal: AbortSignal.timeout(10000),
        ...(payload ? {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify(payload)} : {})});
    } catch (_) {
      reachable = false; renderState();
      throw new Error("Cannot reach the app. Any submitted command may have been saved; reconnect to check its state.");
    }
    if (response.status === 401) {window.location.assign("/login"); throw new Error("Please sign in again.");}
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (data.state) {state = data.state; renderState();}
      throw new Error(data.error || "The request failed. Refresh the page and try again.");
    }
    reachable = true;
    return data;
  }

  function renderState() {
    const status = !reachable ? "Unavailable" : busy ? "Pending" : state.state;
    const label = q("#alarm-state");
    if (label) {
      label.textContent = status; label.dataset.state = status;
      q("#state-note").textContent = !reachable ? "The app cannot be reached. Last confirmed: " + state.state.toLowerCase() + "." : busy ? "Waiting for the simulator to confirm the request…" : !state.online ? "Device offline. Last confirmed: " + (state.armed ? "armed." : "disarmed.") : state.triggered ? "Motion detected. The alarm remains armed." : state.armed ? "Monitoring entrance. The simulator has confirmed the command." : "Alarm off. Motion events are still recorded.";
      q("#control-alarm").textContent = state.armed ? "Disarm system" : "Arm system";
      q("#control-alarm").disabled = busy || !reachable || !state.online;
      q("#simulate-motion").disabled = busy || !reachable || !state.online;
      q("#simulate-connection").disabled = busy || !reachable;
      q("#simulate-connection").textContent = state.online ? "Simulate disconnect" : "Simulate reconnect";
      q("#connection").textContent = !reachable ? "Unavailable" : state.online ? "Online · Simulated" : "Offline";
      q("#device-updated").textContent = formatTime(state.updated_at);
      q("#device-updated").dateTime = state.updated_at;
    }
    q("#motion-alert").hidden = !state.open_alerts;
    q("#alert-detail").textContent = state.open_alerts + " event(s) awaiting review. Acknowledging keeps their history.";
    q("#acknowledge").disabled = busy || !reachable;
    q("#sync-status").textContent = !reachable ? "Connection lost" : busy ? "Saving…" : "Synced · Updates every 4 seconds";
  }

  function filters() {
    return new URLSearchParams({q: q("#event-search").value, kind: q("#event-kind").value, period: q("#event-period").value});
  }

  async function loadEvents() {
    const requestId = ++eventsRequest;
    const params = filters(); params.set("page", page);
    q("#export-events").href = "/events/export?" + filters().toString();
    const data = await api("/api/events?" + params.toString());
    if (requestId !== eventsRequest) return;
    page = data.page; pages = data.pages;
    const body = q("#event-rows"); body.replaceChildren();
    data.events.forEach(event => {
      const row = document.createElement("tr");
      const time = document.createElement("td"), date = document.createElement("small");
      time.textContent = formatTime(event.created_at); date.textContent = new Date(event.created_at).toLocaleDateString(); time.append(date);
      const detail = document.createElement("td"), label = document.createElement("div"), source = document.createElement("div");
      label.className = "event-label"; label.textContent = event.label;
      source.className = "event-source"; source.textContent = event.source;
      const mobileActor = document.createElement("span"); mobileActor.className = "event-actor-mobile"; mobileActor.textContent = "By " + event.actor_name;
      detail.append(label, source, mobileActor);
      const actor = document.createElement("td"); actor.textContent = event.actor_name;
      const status = document.createElement("td");
      const review = event.needs_review && !event.acknowledged_at;
      status.className = "event-status" + (review ? " warning" : "");
      status.textContent = event.acknowledged_at ? "Reviewed" : review ? "Needs review" : "Recorded";
      if (event.acknowledged_by) {const by = document.createElement("small"); by.textContent = "By " + event.acknowledged_by; status.append(by);}
      row.append(time, detail, actor, status); body.append(row);
    });
    if (!data.events.length) {const row = document.createElement("tr"), cell = document.createElement("td");cell.colSpan = 4;cell.textContent = "No events match these filters.";row.append(cell);body.append(row);}
    q("#event-count").textContent = data.total + " matching event(s)";
    q("#page-label").textContent = "Page " + page + " of " + pages;
    q("#previous-page").disabled = page <= 1;
    q("#next-page").disabled = page >= pages;
  }

  async function refresh() {
    if (busy) return;
    try {
      const recovering = !reachable;
      const latest = await api("/api/state");
      if (recovering) {errorBox.hidden = true; message.textContent = "Connection restored. Current state refreshed.";}
      // A response from a poll started before a command must not overwrite it.
      if (!busy && latest.revision >= state.revision) {state = latest; renderState();}
      if (!busy) await loadEvents();
    } catch (error) {showError(error.message);}
  }

  async function action(url, values) {
    if (busy || !reachable) return;
    const revision = state.revision;
    busy = true; errorBox.hidden = true; message.textContent = ""; renderState();
    try {
      const result = await api(url, {...values, revision});
      state = result.state; message.textContent = result.message;
      page = 1; await loadEvents();
    } catch (error) {showError(error.message);}
    finally {busy = false; renderState();}
  }

  if (q("#control-alarm")) {
    const dialog = q("#disarm-dialog");
    q("#control-alarm").addEventListener("click", () => state.armed ? dialog.showModal() : action("/api/control", {action: "arm"}));
    q("#cancel-disarm").addEventListener("click", () => dialog.close());
    q("#confirm-disarm").addEventListener("click", () => {dialog.close(); action("/api/control", {action: "disarm"});});
    q("#simulate-motion").addEventListener("click", () => action("/api/simulate", {event: "motion"}));
    q("#simulate-connection").addEventListener("click", () => action("/api/simulate", {event: state.online ? "disconnect" : "reconnect"}));
  }
  q("#acknowledge").addEventListener("click", () => action("/api/events/acknowledge", {}));
  q("#event-filters").addEventListener("submit", event => {event.preventDefault(); page = 1; loadEvents().catch(error => showError(error.message));});
  q("#event-search").addEventListener("input", () => {clearTimeout(debounce); debounce = setTimeout(() => {page = 1; loadEvents().catch(error => showError(error.message));}, 250);});
  [q("#event-kind"), q("#event-period")].forEach(input => input.addEventListener("change", () => {page = 1; loadEvents().catch(error => showError(error.message));}));
  q("#previous-page").addEventListener("click", () => {page = Math.max(1, page - 1); loadEvents().catch(error => showError(error.message));});
  q("#next-page").addEventListener("click", () => {page = Math.min(pages, page + 1); loadEvents().catch(error => showError(error.message));});
  renderState(); refresh();
  setInterval(() => {if (!document.hidden) refresh();}, 4000);
  document.addEventListener("visibilitychange", () => {if (!document.hidden) refresh();});
})();
