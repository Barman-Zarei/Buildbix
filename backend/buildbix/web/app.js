"use strict";
// All DOM is built from text nodes (never HTML strings) so server data cannot inject markup.
const $ = (id) => document.getElementById(id);
const S = { token: sessionStorage.getItem("bbx_token"), email: sessionStorage.getItem("bbx_email"),
  ws: null, project: null, build: null, timer: null };

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) e.append(k instanceof Node ? k : document.createTextNode(String(k ?? "")));
  return e;
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch("/api" + path, { ...opts, headers });
  if (r.status === 401 && S.token) { logout(); throw new Error("Session expired"); }
  if (!r.ok) {
    let d = r.statusText; try { const j = await r.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(d);
  }
  return r;
}
const jget = async (p) => (await api(p)).json();
const jpost = async (p, json) => (await api(p, { method: "POST", json })).json();
function logout() { sessionStorage.clear(); S.token = null; clearInterval(S.timer); render(); }
function toast(parent, msg, cls = "error") { const old = parent.querySelector(".toast"); if (old) old.remove(); parent.append(h("div", { class: "toast " + cls }, msg)); }
const guard = (box, fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(box, e.message); } };

function render() {
  const app = $("app"); app.replaceChildren(); $("who").replaceChildren();
  if (!S.token) return renderAuth(app);
  $("who").append(S.email + " ", h("button", { class: "sec", onclick: logout }, "Log out"));
  renderWorkspaces(app);
}

function renderAuth(app) {
  const email = h("input", { type: "email", placeholder: "Email", autocomplete: "username" });
  const pw = h("input", { type: "password", placeholder: "Password (10+ characters)", autocomplete: "current-password" });
  const box = h("div", { class: "card" }, h("h2", {}, "What do you want to build?"), h("div", { class: "muted" }, "Sign in to your workspace."));
  const go = (path) => guard(box, async () => {
    const emailValue = email.value.trim();
    const passwordValue = pw.value;

    if (!emailValue) throw new Error("Please enter your email.");
    if (!passwordValue) throw new Error("Please enter your password.");
    if (passwordValue.length < 10) throw new Error("Password must be at least 10 characters.");

    const response = await api(path, { method: "POST", json: { email: emailValue, password: passwordValue } });
    const user = await response.json();
    S.token = user.access_token; S.email = user.user.email;
    sessionStorage.setItem("bbx_token", S.token); sessionStorage.setItem("bbx_email", S.email); render();
  });
  box.append(h("div", { class: "row" }, email, pw), h("div", { class: "row" },
    h("button", { onclick: go("/auth/login") }, "Log in"), h("button", { class: "sec", onclick: go("/auth/register") }, "Create account")));
  app.append(box);
}

async function renderWorkspaces(app) {
  const box = h("div", { class: "card" }, h("h2", {}, "Workspace"));
  app.append(box);
  const list = await jget("/workspaces").catch(() => []);
  const sel = h("select", { onchange: () => { S.ws = Number(sel.value); S.project = null; renderBody(); } },
    list.map((w) => h("option", { value: w.id }, `${w.name} (${w.role})`)));
  if (!S.ws || !list.find((w) => w.id === S.ws)) S.ws = list[0]?.id ?? null;
  if (S.ws) sel.value = S.ws;
  const name = h("input", { placeholder: "New workspace name" });
  box.append(h("div", { class: "row" }, list.length ? sel : h("span", { class: "muted" }, "No workspace yet."), name,
    h("button", { class: "sec", onclick: guard(box, async () => { const w = await jpost("/workspaces", { name: name.value }); S.ws = w.id; render(); }) }, "Create")));
  const body = h("div", { id: "body", class: "card", style: "display:grid;gap:14px;background:none;border:0;padding:0" });
  app.append(body);
  if (S.ws) renderBody();
}

async function renderBody() {
  const body = $("body"); body.replaceChildren(); clearInterval(S.timer);
  const projects = await jget(`/workspaces/${S.ws}/projects`);
  const box = h("div", { class: "card" }, h("h2", {}, "Projects"));
  const file = h("input", { type: "file", accept: ".zip" }), pname = h("input", { placeholder: "Project name" });
  const gitName = h("input", { placeholder: "Project name" });
  const gitUrl = h("input", { placeholder: "https://github.com/owner/repo", style: "min-width:280px" });
  const gitBranch = h("input", { placeholder: "Branch", value: "main", style: "width:100px" });
  box.append(h("table", {}, projects.map((p) => h("tr", {}, h("td", {}, p.name),
    h("td", { class: "muted" }, (p.analysis.frameworks || []).join(", ") || "-"),
    h("td", {}, h("button", { class: "sec", onclick: () => { S.project = p.id; renderProject(); } }, "Open"))))),
        h("div", { class: "row" }, pname, file, h("button", { onclick: guard(box, async () => {
      const fd = new FormData(); fd.append("name", pname.value); fd.append("file", file.files[0]);
      const p = await (await api(`/workspaces/${S.ws}/projects`, { method: "POST", body: fd })).json();
      S.project = p.id; renderBody(); }) }, "Import zip")),
    h("div", { class: "row" }, gitName, gitUrl, gitBranch, h("button", { onclick: guard(box, async () => {
      const projectName = gitName.value.trim();
      const repoUrl = gitUrl.value.trim();
      const branch = gitBranch.value.trim() || "main";

      if (!projectName) throw new Error("Please enter a project name.");
      if (!repoUrl) throw new Error("Please enter a GitHub repository URL.");
      if (!repoUrl.startsWith("https://github.com/")) throw new Error("URL must start with https://github.com/");

      const project = await jpost(`/workspaces/${S.ws}/projects/git`, { name: projectName, repo_url: repoUrl, branch: branch });
      S.project = project.id;
      renderBody();
    }) }, "Import from GitHub")));
  body.append(box, h("div", { id: "project" }), h("div", { id: "activity", class: "card" }));
  if (S.project) renderProject();
  const act = await jget(`/workspaces/${S.ws}/activity?limit=8`);
  $("activity").append(h("h2", {}, "Recent activity"), act.map((a) => h("div", { class: "muted" }, `${a.created_at.slice(0, 19)}  ${a.event}`)));
}

async function renderProject() {
  const box = $("project"); box.replaceChildren(); clearInterval(S.timer);
  const p = await jget(`/projects/${S.project}`), an = p.analysis;
  const card = h("div", { class: "card" }, h("h2", {}, p.name));
  card.append(h("div", {}, [...(an.languages || []), ...(an.frameworks || []), ...(an.build_systems || [])].map((c) => h("span", { class: "chip" }, c))));
  for (const f of an.findings || []) card.append(h("div", { class: f.severity }, `[${f.severity}] ${f.message}`));
  const lic = h("input", { type: "checkbox", id: "lic" });
  const runner = h("select", {}, h("option", { value: "docker" }, "Run on my worker (Docker)"),
    p.source_kind === "git" ? h("option", { value: "github_actions" }, "Run on GitHub Actions") : null);
  const row = h("div", { class: "row" });
  for (const t of an.targets || []) {
    row.append(h("button", { disabled: !t.supported, title: t.reason || "", onclick: guard(card, async () => {
      const b = await jpost(`/projects/${p.id}/builds`, { target: t.id, accept_sdk_license: lic.checked, runner: runner.value });
      S.build = b.id; renderProject(); }) }, "Build " + t.label));
  }
  const fixBox = h("div", {});
  const suggest = guard(card, async () => {
    const list = await jpost(`/projects/${p.id}/fixes/suggest`, { build_id: S.build });
    fixBox.replaceChildren(...list.map((f) => h("div", { class: "msg" }, h("strong", {}, f.title), h("div", { class: "muted" }, f.explanation),
      f.applicable ? h("button", { class: "sec", onclick: guard(fixBox, async () => {
        if (!confirm("Apply this change to your project source?")) return;
        await jpost(`/fixes/${f.id}/apply`); renderProject(); }) }, "Apply (I approve)") : null)));
    if (!list.length) fixBox.append(h("div", { class: "muted" }, "No suggestions."));
  });
  card.append(h("h2", {}, "What do you want to do?"), row, h("div", { class: "row" }, runner, h("button", { class: "sec", onclick: suggest }, "Suggest fixes")), fixBox,
    h("label", { class: "muted" }, lic, " I accept the Android SDK license (needed once per project unless set in buildozer.spec)"));
  const builds = await jget(`/projects/${p.id}/builds`);
  card.append(h("h2", {}, "Builds"), h("table", {}, builds.map((b) => h("tr", {}, h("td", {}, "#" + b.number), h("td", { class: b.status }, b.status),
    h("td", { class: "muted" }, b.target), h("td", {}, h("button", { class: "sec", onclick: () => { S.build = b.id; renderProject(); } }, "Logs"))))));
  box.append(card);
  if (S.build) renderBuild(box);
  renderAutomation(box, p); renderChat(box, p);
}

async function renderBuild(box) {
  const pre = h("pre", {}), meta = h("div", {}), card = h("div", { class: "card" }, h("h2", {}, "Build log"), meta, pre);
  box.append(card);
  let offset = 0;
  async function poll() {
    try {
      const r = await api(`/builds/${S.build}/logs?offset=${offset}`);
      const text = await r.text(); offset = Number(r.headers.get("x-log-next-offset"));
      if (text) { pre.append(text); pre.scrollTop = pre.scrollHeight; }
      const st = r.headers.get("x-build-status");
      if (["success", "failed", "cancelled"].includes(st) && !text) {
        clearInterval(S.timer);
        const b = await jget(`/builds/${S.build}`);
        meta.replaceChildren(h("span", { class: b.status }, b.status), b.error ? " - " + b.error : "");
        for (const a of b.artifacts) meta.append(h("div", {}, a.filename + " ", h("span", { class: "muted" }, "sha256 " + a.sha256.slice(0, 16) + "… "),
          h("button", { onclick: async () => { const blob = await (await api(`/artifacts/${a.id}/download`)).blob();
            const u = URL.createObjectURL(blob), l = h("a", { href: u, download: a.filename }); l.click(); URL.revokeObjectURL(u); } }, "Download")));
      } else if (st === "queued" || st === "running") {
        meta.replaceChildren(h("span", { class: st }, st));
        if (st === "queued") meta.append(" ", h("button", { class: "sec", onclick: () => api(`/builds/${S.build}/cancel`, { method: "POST" }) }, "Cancel"));
      }
    } catch (e) { clearInterval(S.timer); toast(card, e.message); }
  }
  await poll(); S.timer = setInterval(poll, 2000);
}

function renderAutomation(box, p) {
  const card = h("div", { class: "card" }, h("h2", {}, "Automate"));
  const name = h("input", { placeholder: "Name" }), sched = h("input", { placeholder: "every day at 03:00 (UTC)" });
  const lic = h("input", { type: "checkbox" });
  card.append(h("div", { class: "row" }, name, sched, h("label", { class: "muted" }, lic, " accept SDK license"),
    h("button", { onclick: guard(card, async () => { await jpost(`/projects/${p.id}/automations`, { name: name.value, schedule: sched.value, accept_sdk_license: lic.checked }); renderProject(); }) }, "Add")));
  jget(`/projects/${p.id}/automations`).then((l) => l.forEach((a) => card.append(h("div", { class: "muted" }, `${a.name}: ${a.schedule_text} — next ${a.next_run_at?.slice(0, 16) || "-"} UTC`))));
  box.append(card);
}

function renderChat(box, p) {
  const log = h("div", {}), input = h("input", { placeholder: "Ask about this project…", style: "flex:1;min-width:200px" });
  const card = h("div", { class: "card" }, h("h2", {}, "Ask AI (read-only)"), log);
  const send = guard(card, async () => {
    const m = input.value; if (!m) return; input.value = "";
    log.append(h("div", { class: "msg" }, "You: " + m));
    const r = await jpost(`/projects/${p.id}/chat`, { message: m });
    log.append(h("div", { class: "msg" }, "AI: " + r.reply));
  });
  card.append(h("div", { class: "row" }, input, h("button", { onclick: send }, "Send")));
  jget(`/projects/${p.id}/chat`).then((l) => l.forEach((m) => log.append(h("div", { class: "msg" }, (m.role === "user" ? "You: " : "AI: ") + m.content))));
  box.append(card);
}

render();
