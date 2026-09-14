"use strict";
const $ = id => document.getElementById(id);
let csrf = "", overview = null, pendingConfirm = null, page = "accounts", busy = false;
const labels = {
  accounts: ["账号管理", "ACCOUNT DIRECTORY", "集中管理登录凭据，为你的客户端选择当前使用的账号。"],
  keys: ["API 密钥", "CLIENT ACCESS", "为每个客户端分配独立密钥，让连接清晰可控。"],
  test: ["连接测试", "CONNECTION LAB", "从当前账号发起请求，确认模型能否正常响应。"],
  guide: ["接入指南", "GET CONNECTED", "从导入凭据到客户端接入，只需几步。"]
};
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const stamp = ts => ts ? new Date(ts).toLocaleString("zh-CN", {hour12:false}) : "未提供";
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $("toast").hidden = true, 4500); }
function showLogin() {
  csrf = ""; overview = null; $("workspace").hidden = true; $("login").hidden = false; $("boot").hidden = true;
  document.querySelectorAll("dialog[open]").forEach(d => d.close()); $("admin-key").value = "";
}
async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = {"Content-Type":"application/json", ...(method !== "GET" ? {"X-CSRF-Token":csrf} : {})};
  const response = await fetch("/admin/api/" + path, {method, headers, credentials:"same-origin", body:options.body === undefined ? undefined : JSON.stringify(options.body)});
  let data; try { data = await response.json(); } catch { throw new Error("服务器暂时不可用，请稍后重试"); }
  if (!response.ok) {
    if (response.status === 401 && path !== "login") showLogin();
    throw new Error(typeof data.detail === "string" ? data.detail : "操作失败，请检查输入后重试");
  }
  return data;
}
function goPage(next) {
  if (!(next in labels)) next = "accounts";
  page = next;
  document.querySelectorAll(".page-panel").forEach(el => el.hidden = el.id !== "page-" + next);
  document.querySelectorAll("[data-page]").forEach(el => { el.classList.toggle("selected", el.dataset.page === next); el.setAttribute("aria-current", el.dataset.page === next ? "page" : "false"); });
  const [title, kicker, desc] = labels[next];
  $("page-title").replaceChildren(document.createTextNode(title));
  const dot = document.createElement("span"); dot.className = "title-dot"; dot.textContent = "."; $("page-title").append(dot);
  $("breadcrumb").textContent = title; $("page-kicker").textContent = kicker; $("page-desc").textContent = desc;
  history.replaceState(null, "", "#" + next);
}
function render() {
  const {accounts, keys, models, uptime, events} = overview;
  const active = accounts.find(a => a.active && a.enabled);
  $("account-count").textContent = accounts.length; $("account-badge").textContent = accounts.length;
  $("active-name").textContent = active?.name || "未选择账号";
  $("active-state").textContent = active ? (active.expired ? "令牌已过期 · 调用时尝试刷新" : "新请求使用此账号") : "添加或启用一个账号后开始调用";
  $("test-account").textContent = active?.name || "尚未选择";
  $("uptime").textContent = uptime < 60 ? "已启动不到 1 分钟" : `持续运行 ${Math.floor(uptime / 3600)} 小时 ${Math.floor(uptime % 3600 / 60)} 分钟`;
  $("accounts-empty").hidden = accounts.length > 0;
  $("accounts-body").innerHTML = accounts.map(a => {
    const status = !a.enabled ? ["已停用", ""] : a.status === "invalid" ? ["凭据异常", "red"] : a.expired ? [a.refresh_available ? "到期 · 可尝试刷新" : "需要重新登录", "amber"] : ["令牌未到期", "green"];
    return `<tr><td><div class="account-cell"><span class="account-icon">${esc(a.name.slice(0,1))}</span><div><strong>${esc(a.name)}${a.active ? '<span class="mini-active">当前使用</span>' : ""}</strong><small>${esc(a.nickname || "WorkBuddy / CodeBuddy")}</small></div></div></td><td><span class="pill ${status[1]}">${status[0]}</span></td><td class="muted mono">${esc(stamp(a.expires_at))}</td><td><div class="actions">${a.enabled && !a.active ? `<button class="switch" data-action="activate" data-id="${a.id}">使用此账号</button>` : ""}<button data-action="rename" data-id="${a.id}">备注</button><button data-action="toggle" data-id="${a.id}">${a.enabled ? "停用" : "启用"}</button><button class="danger" data-action="delete" data-id="${a.id}">删除</button></div></td></tr>`;
  }).join("");
  $("keys-body").innerHTML = keys.map(k => `<tr><td><strong>${esc(k.name)}</strong></td><td><code>${esc(k.hint)}</code></td><td class="muted mono">${esc(stamp(k.created * 1000))}</td><td class="align-right"><div class="actions"><button class="danger" data-revoke="${k.id}">撤销</button></div></td></tr>`).join("");
  const selected = $("model").value || "deepseek-v4-flash";
  $("model").replaceChildren(...models.map(model => { const o = document.createElement("option"); o.value = o.textContent = model; return o; }));
  if (models.includes(selected)) $("model").value = selected;
  $("test-submit").disabled = !active || busy;
  $("test-history").innerHTML = events.length ? events.map(e => `<div class="history-row"><span class="mono muted">${esc(stamp(e.time * 1000))}</span><strong>${esc(e.model)}</strong><span class="pill ${e.ok ? "green" : "red"}">${e.ok ? "成功" : "失败"}</span><span class="mono">${e.seconds}s</span></div>`).join("") : '<p class="history-empty">暂无测试记录，发送第一条测试消息。</p>';
}
async function refresh() {
  $("refresh").disabled = true;
  try { overview = await api("overview"); render(); $("load-error").hidden = true; }
  catch (e) { $("load-error").textContent = e.message; $("load-error").hidden = false; throw e; }
  finally { $("refresh").disabled = false; }
}
async function enter() { $("boot").hidden = true; $("login").hidden = true; $("workspace").hidden = false; goPage(location.hash.slice(1)); await refresh(); }
$("login-form").addEventListener("submit", async e => {
  e.preventDefault(); const button = e.submitter; button.disabled = true; $("login-error").textContent = "";
  try { const data = await api("login", {method:"POST",body:{key:$("admin-key").value.trim()}}); csrf = data.csrf; $("admin-key").value = ""; await enter(); }
  catch (error) { $("login-error").textContent = error.message; }
  finally { button.disabled = false; }
});
$("logout").addEventListener("click", async () => { try { await api("logout", {method:"POST"}); showLogin(); } catch (e) { toast(e.message); } });
$("refresh").addEventListener("click", () => refresh().then(() => toast("已刷新"), e => toast(e.message)));
document.querySelectorAll("[data-page]").forEach(b => b.addEventListener("click", () => goPage(b.dataset.page)));
$("go-guide").addEventListener("click", () => goPage("guide"));
document.querySelectorAll(".close-dialog").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
$("account-dialog").addEventListener("close", () => { $("account-form").reset(); $("file-label").textContent = "选择或拖入 .info / .json 文件"; $("account-error").textContent = ""; });
function openAccount() { $("account-dialog").showModal(); }
$("add-account").addEventListener("click", openAccount); $("empty-add").addEventListener("click", openAccount);
async function readFile(file) {
  if (!file) return;
  if (file.size > 1024 * 1024) throw new Error("文件超过 1 MB，请选择登录凭据文件");
  $("credential-json").value = (await file.text()).replace(/^\uFEFF/, "");
  $("file-label").textContent = file.name;
}
$("credential-file").addEventListener("change", e => { readFile(e.target.files[0]).catch(err => $("account-error").textContent = err.message); });
$("file-drop").addEventListener("dragover", e => { e.preventDefault(); $("file-drop").classList.add("drag-over"); });
$("file-drop").addEventListener("dragleave", () => $("file-drop").classList.remove("drag-over"));
$("file-drop").addEventListener("drop", e => { e.preventDefault(); $("file-drop").classList.remove("drag-over"); readFile(e.dataTransfer.files[0]).catch(err => $("account-error").textContent = err.message); });
$("account-form").addEventListener("submit", async e => {
  e.preventDefault(); e.submitter.disabled = true; $("account-error").textContent = "";
  try {
    let credential; try { credential = JSON.parse($("credential-json").value); } catch { throw new Error("请选择文件或粘贴有效的 JSON 内容"); }
    await api("accounts", {method:"POST", body:{name:$("account-name").value.trim() || undefined, credential}});
    $("account-dialog").close(); await refresh(); toast("账号已导入");
  } catch (err) { $("account-error").textContent = err.message; } finally { e.submitter.disabled = false; }
});
function confirmAction(title, desc, callback, rename = null) {
  $("confirm-title").textContent = title; $("confirm-desc").textContent = desc; $("confirm-error").textContent = "";
  $("rename-label").hidden = $("rename-value").hidden = rename === null; $("rename-value").value = rename || "";
  pendingConfirm = callback; $("confirm-dialog").showModal();
}
$("confirm-form").addEventListener("submit", async e => { e.preventDefault(); e.submitter.disabled = true; try { await pendingConfirm(); $("confirm-dialog").close(); await refresh(); toast("操作已保存"); } catch (err) { $("confirm-error").textContent = err.message; } finally { e.submitter.disabled = false; } });
$("accounts-body").addEventListener("click", async e => {
  const b = e.target.closest("[data-action]"); if (!b) return;
  const a = overview.accounts.find(x => x.id === b.dataset.id); if (!a) return;
  const update = body => api("accounts/" + a.id, {method:"PATCH", body});
  if (b.dataset.action === "activate") confirmAction("切换当前账号？", `新请求将使用「${a.name}」。已开始的请求继续使用原账号。`, () => update({active:true}));
  if (b.dataset.action === "rename") confirmAction("编辑账号备注", "备注仅用于在管理后台识别账号。", () => update({name:$("rename-value").value}), a.name);
  if (b.dataset.action === "toggle") confirmAction(a.enabled ? "停用这个账号？" : "启用这个账号？", a.active ? "这是当前账号。停用后需要手动选择另一个账号，才能继续调用。" : "启用后可将它设为当前使用账号。", () => update({enabled:!a.enabled}));
  if (b.dataset.action === "delete") confirmAction("删除这个账号？", `「${a.name}」将从账号列表移除。服务器保留恢复副本。${a.active ? "当前调用账号将被清空。" : ""}`, () => api("accounts/" + a.id, {method:"DELETE"}));
});
$("keys-body").addEventListener("click", e => { const b = e.target.closest("[data-revoke]"); if (!b) return; const k = overview.keys.find(x => x.id === b.dataset.revoke); confirmAction("撤销客户端密钥？", `使用「${k.name}」的客户端将无法发起新请求。此操作不可撤销。`, () => api("keys/" + k.id, {method:"DELETE"})); });
$("add-key").addEventListener("click", () => { $("key-form").hidden = false; $("created-key").hidden = true; $("key-dialog").showModal(); });
$("key-dialog").addEventListener("close", () => { $("key-form").reset(); $("new-key-value").textContent = ""; $("key-error").textContent = ""; });
$("key-form").addEventListener("submit", async e => { e.preventDefault(); e.submitter.disabled = true; try { const d = await api("keys", {method:"POST",body:{name:$("key-name").value}}); $("key-form").hidden = true; $("created-key").hidden = false; $("new-key-value").textContent = d.key; await refresh(); } catch (err) { $("key-error").textContent = err.message; } finally { e.submitter.disabled = false; } });
async function copy(value) { try { await navigator.clipboard.writeText(value); toast("已复制"); } catch { toast("无法自动复制，请手动选择文本复制"); } }
$("copy-key").addEventListener("click", () => copy($("new-key-value").textContent));
document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", () => copy(b.dataset.copy)));
$("base-url").textContent = location.origin + "/v1"; $("anthropic-url").textContent = location.origin;
$("copy-base").addEventListener("click", () => copy(location.origin + "/v1")); $("copy-anthropic").addEventListener("click", () => copy(location.origin));
$("host-label").textContent = location.host;
$("test-form").addEventListener("submit", async e => {
  e.preventDefault(); busy = true; $("test-submit").disabled = true; $("test-submit").textContent = "正在调用…"; $("test-status").className = "pill amber"; $("test-status").textContent = "请求中"; $("test-output").textContent = "正在等待上游响应，最长约 90 秒…"; $("test-meta").textContent = "";
  try { const r = await api("test", {method:"POST",body:{model:$("model").value,prompt:$("test-prompt").value}}); $("test-status").className = "pill " + (r.ok ? "green" : "red"); $("test-status").textContent = r.ok ? "连接成功" : "调用失败"; $("test-output").textContent = r.ok ? r.answer : r.error; $("test-meta").textContent = `${r.seconds}s${r.status ? " · HTTP " + r.status : ""}${r.usage?.total_tokens !== undefined ? " · " + r.usage.total_tokens + " tokens" : ""}`; }
  catch (err) { $("test-status").className = "pill red"; $("test-status").textContent = "请求失败"; $("test-output").textContent = err.message; }
  finally { busy = false; $("test-submit").textContent = "发送测试 ↗"; await refresh().catch(() => {}); }
});
(async () => { try { const s = await api("session"); csrf = s.csrf; await enter(); } catch (e) { if (!csrf) showLogin(); } })();
