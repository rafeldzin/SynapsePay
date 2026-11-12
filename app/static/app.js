/* SynapsePay operator console (PRD 13.1). Plain JavaScript, no framework, no build step.
 *
 * Every value on screen comes from the state snapshot or from a gate response
 * (INV-11). All text is set with textContent, never parsed as HTML: reports,
 * messages, reply drafts and model output are untrusted (INV-8).
 * Every pay or repair control sits in a strip of AI recommends, Gate checks and
 * Human approves. A click only asks the server, whose gate decides.
 */
"use strict";

const PRIMARY_SCENARIO = "B"; // the on-camera scenario, chosen by the team from Gate 0
const POLL_MS = 2000;
const CONTRACTOR = "dewi"; // the seeded contractor, used as submitted_by for uploads

const API = {
  health: () => ["GET", "/api/health"],
  state: () => ["GET", "/api/state"],
  reset: () => ["POST", "/api/demo/reset"],
  deliverable: (id) => ["POST", `/api/milestones/${enc(id)}/deliverable`],
  pay: (id) => ["POST", `/api/milestones/${enc(id)}/pay`],
  retry: (id) => ["POST", `/api/payouts/${enc(id)}/retry`],
  messages: () => ["POST", "/api/messages"],
  investigate: (id) => ["POST", `/api/cases/${enc(id)}/investigate`],
  cancel: (id) => ["POST", `/api/cases/${enc(id)}/actions/cancel`],
  reissue: (id) => ["POST", `/api/cases/${enc(id)}/actions/reissue`],
  reconcile: (id) => ["POST", `/api/cases/${enc(id)}/actions/reconcile`],
  recheck: (id) => ["POST", `/api/cases/${enc(id)}/actions/recheck`],
};

const FIXTURES = {m1_pass: "m1", m2_fail_injected: "m2", m2_fail_clean: "m2", m2_good_injected: "m2"};

const SCENARIO_HINT = {
  A: "Scenario A: m1 is paid to a mistyped email and sits UNCLAIMED. The contractor writes in, the investigator recommends cancel and reissue after the new email is confirmed.",
  B: "Scenario B: the first PayPal response is discarded on purpose (simulated fault), so the ledger says UNKNOWN. Retry with the same key finds the original, the investigator recommends reconcile, and nothing is paid twice.",
};

const ui = {
  state: null,
  lastJson: "",
  busy: false,
  gates: {},    // key -> {title, gate, status, at}
  notices: {},  // key -> {kind, text}
  files: {},    // milestone id -> {csv: File, report: File}
  reissue: {},  // case id -> {email, confirmed}
};

// ---- helpers ----------------------------------------------------------------

function enc(value) {
  return encodeURIComponent(String(value));
}

function byId(id) {
  return document.getElementById(id);
}

const PROPS = new Set(["disabled", "value", "checked", "type", "hidden", "title", "placeholder",
                       "name", "accept", "htmlFor", "rows", "size"]);

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = String(value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (PROPS.has(key)) el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(typeof child === "object" ? child : document.createTextNode(String(child)));
  }
  return el;
}

function money(cents, currency) {
  const value = `$${(Number(cents) / 100).toFixed(2)}`;
  return currency && currency !== "USD" ? `${value} ${currency}` : value;
}

function badge(state, extra) {
  return h("span", {class: `badge state-${state || "none"}${extra ? " " + extra : ""}`, text: state || "-"});
}

function time(ts) {
  return ts ? String(ts).slice(11, 19) : "-";
}

function operator() {
  const input = byId("operator");
  return (input && input.value ? input.value : "").trim();
}

function approval() {
  return {approved_by: operator(), confirm: true};
}

function routeLabel(route) {
  return `${route[0]} ${route[1]}`;
}

function gateOf(data) {
  if (!data || typeof data !== "object") return null;
  if (Array.isArray(data.checks)) return data;
  if (data.gate && Array.isArray(data.gate.checks)) return data.gate;
  if (data.detail && Array.isArray(data.detail.checks)) return data.detail;
  return null;
}

function detailText(data) {
  if (!data) return "";
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return "";
}

async function call(route, body) {
  const [method, path] = route;
  const init = {method, headers: {}};
  if (typeof FormData !== "undefined" && body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    return {status: 0, data: null, label: routeLabel(route), error: String(error)};
  }
  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }
  return {status: response.status, data, label: routeLabel(route)};
}

function failure(result) {
  if (result.status === 0) return `${result.label}: network error`;
  if (result.status === 404 || result.status === 405) {
    const detail = detailText(result.data);
    return detail && detail !== "Not Found" && detail !== "Method Not Allowed"
      ? `${result.label}: ${detail}`
      : `${result.label} is not available on the server yet (HTTP ${result.status}).`;
  }
  const detail = detailText(result.data);
  return `${result.label} failed (HTTP ${result.status})${detail ? ": " + detail : ""}`;
}

function notice(key, kind, text) {
  ui.notices[key] = {kind, text};
  rerender();
}

function noticeView(key) {
  const n = ui.notices[key];
  return n ? h("div", {class: `notice ${n.kind}`, text: n.text}) : null;
}

function recordGate(key, title, result) {
  const gate = gateOf(result.data);
  if (gate) ui.gates[key] = {title, gate, status: result.status, at: new Date().toISOString()};
  return gate;
}

function refusedChecks(gate) {
  return (gate.checks || []).filter((c) => c.ok === false);
}

// ---- gate checklist and the two-key strip -----------------------------------

function checklist(gate, label) {
  if (!gate) return h("p", {class: "muted", text: "No gate result yet."});
  const verdict = gate.preview
    ? (refusedChecks(gate).length ? "Preview: blocked" : "Preview: ready for human approval")
    : (gate.allowed ? "Allowed" : "Refused");
  const cls = gate.preview ? (refusedChecks(gate).length ? "fail" : "pending") : (gate.allowed ? "ok" : "fail");
  return h("div", {class: "gate"},
    h("div", {class: `gate-verdict ${cls}`, text: `${label ? label + ": " : ""}${gate.action || ""} ${verdict}`}),
    h("ul", {class: "checks"}, (gate.checks || []).map((check) => {
      const state = check.ok === true ? "ok" : check.ok === false ? "fail" : "pending";
      const mark = check.ok === true ? "✓" : check.ok === false ? "✗" : "…";
      return h("li", {class: `check ${state}`},
        h("span", {class: "mark", text: mark}),
        h("code", {class: "check-id", text: check.id}),
        h("span", {class: "check-name", text: check.name}),
        h("span", {class: "check-detail", text: check.detail || ""}));
    })));
}

function strip(ai, gate, human) {
  return h("div", {class: "strip"},
    h("div", {class: "col ai"}, h("div", {class: "col-title", text: "AI recommends"}), ai),
    h("div", {class: "col gatecol"}, h("div", {class: "col-title", text: "Gate checks"}), gate),
    h("div", {class: "col human"}, h("div", {class: "col-title", text: "Human approves"}), human));
}

// ---- header and banners -----------------------------------------------------

function setHealth(health) {
  byId("run-id").textContent = health.run_id || "-";
  byId("scenario").textContent = health.scenario || "-";
  byId("paypal-mode").textContent = health.paypal_mode || "-";
  byId("llm-mode").textContent = health.llm_mode || "-";
  renderBanners(health);
}

function renderBanners(info, extra) {
  const banners = [];
  if (info.paypal_mode === "fake") {
    banners.push(h("div", {class: "banner warn", text: "PAYPAL_MODE=fake: PayPal is simulated in-process for testing. No sandbox call is made and the IDs are not real."}));
  }
  if (info.llm_mode === "fake") {
    banners.push(h("div", {class: "banner warn", text: "LLM_MODE=fake: the verifier and investigator use canned test answers."}));
  }
  const scenario = info.scenario || PRIMARY_SCENARIO;
  banners.push(h("div", {class: `banner info${scenario === PRIMARY_SCENARIO ? " primary-scenario" : ""}`},
    h("strong", {text: scenario === PRIMARY_SCENARIO ? "Primary demo. " : "Alternate demo. "}),
    SCENARIO_HINT[scenario] || ""));
  if (extra) banners.push(extra);
  byId("banners").replaceChildren(...banners);
}

// ---- contract ---------------------------------------------------------------

function renderContract(state) {
  const c = state.contract;
  if (!c) {
    byId("contract-body").replaceChildren(h("p", {class: "muted", text: "No contract seeded."}));
    return;
  }
  const verified = c.payee_email_verified_at
    ? h("span", {class: "tag ok", text: `verified ${c.payee_email_verified_at}`})
    : h("span", {class: "tag", text: "not verified"});
  byId("contract-body").replaceChildren(h("dl", {class: "facts"},
    h("dt", {text: "Contract"}), h("dd", {text: `${c.id} · ${c.currency || "USD"}`}),
    h("dt", {text: "Buyer"}), h("dd", {text: c.buyer_name}),
    h("dt", {text: "Contractor"}), h("dd", {text: c.contractor_name}),
    h("dt", {text: "Payee email"}), h("dd", {}, h("code", {text: c.payee_email}), " ", verified)));
}

// ---- milestones -------------------------------------------------------------

function factValues(verification) {
  const values = {};
  for (const fact of (verification && verification.facts) || []) values[fact.id] = fact.value;
  return values;
}

function factChip(id, values) {
  if (!(id in values)) return h("span", {class: "chip bad", text: `${id} (unknown fact)`});
  const value = Array.isArray(values[id]) ? `[${values[id].join(", ")}]` : String(values[id]);
  return h("span", {class: "chip", text: `${id} = ${id === "sha256" ? value.slice(0, 12) + "…" : value}`});
}

function criteriaTable(milestone) {
  const v = milestone.verification;
  const values = factValues(v);
  const verdicts = {};
  for (const cv of (v && v.criteria) || []) verdicts[cv.criterion_id] = cv;
  return h("table", {class: "criteria"},
    h("thead", {}, h("tr", {}, h("th", {text: "Criterion"}), h("th", {text: "Hard checks"}), h("th", {text: "Verdict and cited facts"}))),
    h("tbody", {}, (milestone.criteria || []).map((criterion) => {
      const cv = verdicts[criterion.id];
      return h("tr", {},
        h("td", {}, h("code", {text: criterion.id}), " ", criterion.text),
        h("td", {}, (criterion.hard_checks || []).length
          ? criterion.hard_checks.map((hc) => h("span", {class: "chip rule", text: `${hc.fact_id} ${hc.op} ${hc.value}`}))
          : h("span", {class: "muted", text: "none (AI)"})),
        h("td", {}, cv
          ? [badge(cv.verdict),
             cv.downgraded_by ? h("span", {class: "tag warn", text: `downgraded: ${cv.downgraded_by}`}) : null,
             h("div", {class: "rationale", text: cv.rationale}),
             h("div", {class: "chips"}, (cv.cited_fact_ids || []).map((id) => factChip(id, values)))]
          : h("span", {class: "muted", text: "not verified"})));
    })));
}

function verificationView(milestone) {
  const v = milestone.verification;
  if (!v) return h("p", {class: "muted", text: "No deliverable submitted yet."});
  const parts = [];
  if (v.injection_detected) {
    parts.push(h("div", {class: "banner danger"},
      h("strong", {text: "Injection attempt flagged. "}),
      "Text in the deliverable tried to instruct the AI. It was treated as data and can only make the verdict stricter.",
      h("ul", {}, (v.injection_findings || []).map((f) =>
        h("li", {}, h("code", {text: `${f.source} · ${f.pattern_id}`}), " ", h("span", {class: "excerpt", text: f.excerpt}))))));
  }
  if (v.overall_capped_by) {
    parts.push(h("div", {class: "notice warn", text: `Overall capped at NEEDS_REVIEW by ${v.overall_capped_by}.`}));
  }
  if (!v.llm_used) {
    parts.push(h("div", {class: "notice warn", text: "The model was unavailable, so the verifier abstained: criteria without a failed hard check need a person (NEEDS_REVIEW)."}));
  }
  if ((v.contradicted_claims || []).length) {
    parts.push(h("div", {class: "claims"}, h("strong", {text: "Report claims contradicted by the data"}),
      h("ul", {}, v.contradicted_claims.map((claim) =>
        h("li", {}, h("q", {text: claim.claim}), " contradicts ", factChip(claim.fact_id, {[claim.fact_id]: claim.fact_value}))))));
  }
  if (v.note_to_contractor) {
    parts.push(h("div", {class: "note"}, h("strong", {text: "Note to contractor (draft)"}), h("blockquote", {text: v.note_to_contractor})));
  }
  return h("div", {class: "verification"},
    h("div", {class: "overall"}, "Verifier overall ", badge(v.overall),
      h("span", {class: "muted", text: ` · deliverable sha256 ${String(v.deliverable_sha256 || "").slice(0, 12)}…`})),
    parts);
}

function deliverableControls(milestone) {
  const files = ui.files[milestone.id] || {};
  const fixtureButtons = Object.entries(FIXTURES).filter(([, mid]) => mid === milestone.id).map(([name]) =>
    h("button", {type: "button", class: "fixture", title: "Seeded fixture", onclick: () => submitFixture(milestone, name)},
      h("span", {class: "tag seeded", text: "Seeded fixture"}), " ", name));
  const pick = (kind) => (event) => {
    ui.files[milestone.id] = Object.assign({}, ui.files[milestone.id], {[kind]: event.target.files[0]});
    rerender();
  };
  return h("div", {class: "deliverable"},
    h("div", {class: "fixtures"}, fixtureButtons),
    h("div", {class: "upload"},
      h("label", {}, "CSV ", h("input", {type: "file", accept: ".csv,text/csv", onchange: pick("csv")})),
      files.csv ? h("span", {class: "muted", text: files.csv.name}) : null,
      h("label", {}, "Report ", h("input", {type: "file", accept: ".md,.txt,text/plain", onchange: pick("report")})),
      files.report ? h("span", {class: "muted", text: files.report.name}) : null,
      h("button", {type: "button", disabled: !(files.csv && files.report), onclick: () => uploadFiles(milestone)}, "Upload deliverable")),
    noticeView(`m:${milestone.id}`));
}

function payStrip(milestone) {
  const preview = milestone.gate_preview || null;
  const ready = Boolean(preview) && refusedChecks(preview).length === 0;
  const last = ui.gates[`pay:${milestone.id}`];
  const v = milestone.verification;
  const ai = v
    ? h("div", {}, "Verifier says ", badge(v.overall), v.injection_detected ? h("div", {class: "tag danger", text: "injection flagged"}) : null)
    : h("p", {class: "muted", text: "Waiting for a deliverable."});
  const gate = h("div", {}, checklist(preview, "Preview"),
    last ? h("div", {class: "last-gate"}, checklist(last.gate, `Last decision ${time(last.at)}`)) : null);
  const human = h("div", {},
    h("button", {type: "button", class: "approve", dataset: {role: "pay", milestone: milestone.id},
                 disabled: !ready, onclick: () => pay(milestone, `pay:${milestone.id}`)},
      `Approve and pay ${money(milestone.amount_cents)}`),
    h("div", {class: "muted small", text: ready ? `as ${operator() || "?"}` : "Enabled when every non-human gate check passes."}),
    noticeView(`pay:${milestone.id}`));
  return strip(ai, gate, human);
}

function renderMilestones(state) {
  const body = (state.milestones || []).map((milestone) => h("article", {class: "card milestone"},
    h("header", {class: "card-head"},
      h("h3", {}, h("code", {text: milestone.id}), ` ${milestone.title}`),
      h("span", {class: "amount", text: money(milestone.amount_cents)}),
      badge(milestone.state)),
    criteriaTable(milestone),
    deliverableControls(milestone),
    verificationView(milestone),
    payStrip(milestone)));
  byId("milestones-body").replaceChildren(...(body.length ? body : [h("p", {class: "muted", text: "No milestones."})]));
}

// ---- payout ledger ----------------------------------------------------------

function renderLedger(state) {
  const payouts = state.payouts || [];
  const last = ui.gates.ledger;
  const table = payouts.length ? h("table", {class: "ledger"},
    h("thead", {}, h("tr", {}, ["Payout", "Milestone", "Attempt", "Local state", "Batch status", "Item status",
      "Batch ID", "Item ID", "Recipient", "Amount", "Last sync", ""].map((t) => h("th", {text: t})))),
    h("tbody", {}, payouts.map((p) => h("tr", {},
      h("td", {}, h("code", {text: p.id})),
      h("td", {text: p.milestone_id}),
      h("td", {text: p.attempt}),
      h("td", {}, badge(p.state)),
      h("td", {}, h("code", {text: p.batch_status || "-"})),
      h("td", {}, h("code", {text: p.item_status || "-"})),
      h("td", {}, h("code", {text: p.payout_batch_id || "-"})),
      h("td", {}, h("code", {text: p.payout_item_id || "-"})),
      h("td", {}, h("code", {text: p.recipient_email})),
      h("td", {text: money(p.amount_cents, p.currency)}),
      h("td", {text: time(p.last_synced_at)}),
      h("td", {}, p.state === "UNKNOWN"
        ? h("button", {type: "button", onclick: () => retry(p.id, "ledger")}, "Retry (same key)")
        : h("button", {type: "button", class: "ghost",
                       onclick: () => pay({id: p.milestone_id, amount_cents: milestoneAmount(p.milestone_id)}, "ledger")},
            "Resend"))))))
    : h("p", {class: "muted", text: "No payouts yet."});
  byId("ledger-body").replaceChildren(...[table,
    last ? h("div", {class: "last-gate"}, h("strong", {text: last.title}), checklist(last.gate)) : null,
    noticeView("ledger")].filter(Boolean));
}

function milestoneAmount(milestoneId) {
  const m = ((ui.state && ui.state.milestones) || []).find((x) => x.id === milestoneId);
  return m ? m.amount_cents : null;
}

// ---- inbox ------------------------------------------------------------------

function renderInbox(state) {
  const messages = state.messages || [];
  byId("inbox-body").replaceChildren(messages.length
    ? h("ul", {class: "messages"}, messages.map((m) => h("li", {},
        h("div", {class: "msg-head"}, h("span", {class: "tag simulated", text: "Simulated"}), " ",
          h("code", {text: m.from_email}), h("span", {class: "muted", text: ` ${time(m.created_at)}`})),
        h("div", {class: "msg-body", text: m.body}))))
    : h("p", {class: "muted", text: "No messages."}));
}

// ---- cases ------------------------------------------------------------------

const ACTION_BUTTON = {
  CANCEL_UNCLAIMED_AND_REISSUE: "cancel",
  RECONCILE_MARK_PAID: "reconcile",
  RETRY_SAME_BATCH_ID: "retry",
  WAIT_AND_RECHECK: "recheck",
};

function evidenceChip(id, evidence) {
  const item = evidence.find((e) => e.id === id);
  return h("span", {class: `chip ${item && item.source === "paypal" ? "paypal" : ""}`,
                    title: item ? `${item.tool}: ${item.summary}` : "", text: id});
}

function reportView(report) {
  if (!report) return h("p", {class: "muted", text: "Not investigated yet."});
  const evidence = report.evidence || [];
  return h("div", {class: "report"},
    h("ol", {class: "hypotheses"}, (report.hypotheses || []).slice().sort((a, b) => a.rank - b.rank).map((hyp) =>
      h("li", {class: hyp.supported ? "supported" : "rejected"},
        h("code", {text: hyp.code}), " ",
        h("span", {class: `tag ${hyp.supported ? "ok" : ""}`, text: hyp.supported ? "supported" : "rejected"}),
        h("div", {class: "rationale", text: hyp.rationale}),
        h("div", {class: "chips"}, (hyp.evidence_ids || []).map((id) => evidenceChip(id, evidence)))))),
    (report.hypotheses || []).length ? null : h("p", {class: "muted", text: "No hypotheses: the investigation did not complete, so it escalated to a person."}),
    h("dl", {class: "facts"},
      h("dt", {text: "Do not resend"}), h("dd", {text: report.do_not_resend ? "yes" : "no"}),
      h("dt", {text: "Resend risk"}), h("dd", {text: report.resend_risk}),
      report.required_verification ? [h("dt", {text: "Required verification"}), h("dd", {text: report.required_verification})] : null,
      h("dt", {text: "Steps"}), h("dd", {text: `${report.steps_used} tool calls · mode ${report.mode}`})),
    h("div", {class: "note"}, h("strong", {text: "Reply draft (not sent)"}), h("blockquote", {text: report.reply_draft})),
    h("details", {class: "evidence"}, h("summary", {text: `Evidence (${evidence.length})`}),
      h("ul", {}, evidence.map((e) => h("li", {},
        h("code", {text: e.id}), " ", h("span", {class: `tag ${e.source === "paypal" ? "real" : ""}`, text: e.source}), " ",
        h("code", {text: e.tool}), h("div", {text: e.summary}))))));
}

function caseStrip(c, state) {
  const report = c.report;
  const recommended = report ? ACTION_BUTTON[report.recommended_action] : null;
  const closed = c.state === "RESOLVED";
  const milestone = (state.milestones || []).find((m) => m.id === c.milestone_id) || {};
  const form = ui.reissue[c.id] || {email: c.requested_email || "", confirmed: false};
  ui.reissue[c.id] = form;
  const actionButton = (key, label, handler) => h("button", {
    type: "button", class: key === recommended ? "approve recommended" : "ghost",
    disabled: closed, onclick: handler}, label);

  const ai = h("div", {},
    report
      ? [h("div", {class: "recommended-action"}, h("code", {text: report.recommended_action})),
         report.downgraded_by ? h("span", {class: "tag warn", text: `downgraded: ${report.downgraded_by}`}) : null]
      : h("p", {class: "muted", text: "No recommendation yet."}),
    h("button", {type: "button", disabled: closed, onclick: () => investigate(c.id)}, "Run AI investigator"),
    h("div", {class: "muted small", text: "Read-only: the investigator cannot move money."}));
  const last = ui.gates[`case:${c.id}`];
  const gate = last ? checklist(last.gate, last.title)
    : h("p", {class: "muted", text: "On approval the gate re-checks the action against a fresh PayPal read."});
  const human = h("div", {class: "actions"},
    actionButton("cancel", "Approve cancel of unclaimed item", () => caseAction(c, "cancel", approval())),
    h("div", {class: "reissue"},
      h("label", {}, "New email ", h("input", {type: "email", value: form.email, dataset: {keep: "1"},
        oninput: (event) => { form.email = event.target.value; }})),
      h("label", {class: "check-label"},
        h("input", {type: "checkbox", checked: form.confirmed, onchange: (event) => { form.confirmed = event.target.checked; }}),
        " New email confirmed by phone (simulated)"),
      h("button", {type: "button", class: c.state === "AWAITING_REISSUE" ? "approve recommended" : "ghost", disabled: closed,
        onclick: () => caseAction(c, "reissue", Object.assign(approval(), {
          expected_amount_cents: milestone.amount_cents,
          new_email: form.email.trim(),
          email_verified: form.confirmed,
          verified_by: form.confirmed ? operator() : "",
          verification_channel: "phone",
        }))}, `Approve reissue ${money(milestone.amount_cents)}`)),
    actionButton("reconcile", "Approve reconcile (mark paid)", () => caseAction(c, "reconcile", approval())),
    actionButton("retry", "Approve retry (same batch ID)", () => retry(c.payout_id, `case:${c.id}`)),
    actionButton("recheck", "Recheck PayPal (read-only)", () => caseAction(c, "recheck")),
    noticeView(`case:${c.id}`));
  return strip(ai, gate, human);
}

function renderCases(state) {
  const cases = (state.cases || []).slice().reverse();
  const contract = state.contract || {};
  byId("cases-body").replaceChildren(...(cases.length ? cases.map((c) => h("article", {class: "card case"},
    h("header", {class: "card-head"},
      h("h3", {}, h("code", {text: c.id})),
      h("span", {class: "tag", text: `trigger ${c.trigger_rule}`}),
      h("span", {class: "muted", text: `payout ${c.payout_id} · milestone ${c.milestone_id}`}),
      badge(c.state),
      c.resolution ? h("span", {class: "tag ok", text: c.resolution}) : null),
    c.diversion_risk ? h("div", {class: "banner danger"},
      h("strong", {text: "Diversion risk. "}),
      `A message asks to pay ${c.requested_email || "another address"} instead of the payee on file (${contract.payee_email || "-"}). ` +
      "Confirm through another channel before any reissue.") : null,
    reportView(c.report),
    caseStrip(c, state)))
    : [h("p", {class: "muted", text: "No open cases. Cases open from detection rules, never from the AI."})]));
}

// ---- timeline ---------------------------------------------------------------

function actorClass(actor) {
  const kind = String(actor || "").split(":")[0];
  return ["ai", "human", "paypal", "system"].includes(kind) ? `actor-${kind}` : "actor-system";
}

function renderTimeline(state) {
  const box = byId("timeline-body");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const events = state.events || [];
  box.replaceChildren(events.length ? h("ol", {class: "events"}, events.map((e) => {
    const ids = [["m", e.milestone_id], ["payout", e.payout_id], ["case", e.case_id],
                 ["batch", e.paypal_batch_id], ["item", e.paypal_item_id]].filter(([, v]) => v);
    const payload = e.payload !== undefined ? e.payload : e.payload_json;
    const payloadText = typeof payload === "string" ? payload : JSON.stringify(payload || {});
    return h("li", {class: actorClass(e.actor)},
      h("span", {class: "ts", text: time(e.ts)}),
      h("code", {class: "type", text: e.type}),
      h("span", {class: "actor", text: e.actor}),
      ids.map(([k, v]) => h("span", {class: `chip ${k === "batch" || k === "item" ? "paypal" : ""}`, text: `${k} ${v}`})),
      payloadText && payloadText !== "{}" ? h("span", {class: "payload", text: payloadText.length > 300 ? payloadText.slice(0, 300) + "…" : payloadText}) : null);
  })) : h("p", {class: "muted", text: "No events."}));
  if (atBottom) box.scrollTop = box.scrollHeight;
}

// ---- render -----------------------------------------------------------------

function render(state) {
  ui.state = state;
  ui.lastJson = JSON.stringify(state);
  setHealth(state);
  renderContract(state);
  renderMilestones(state);
  renderLedger(state);
  renderInbox(state);
  renderCases(state);
  renderTimeline(state);
}

function editing() {
  const el = document.activeElement;
  return Boolean(el && el.dataset && el.dataset.keep === "1");
}

function rerender() {
  if (ui.state && !editing()) render(ui.state);
}

async function refresh() {
  if (ui.busy) return;
  ui.busy = true;
  try {
    const result = await call(API.state());
    if (result.status === 200 && result.data) {
      if (JSON.stringify(result.data) !== ui.lastJson && !editing()) render(result.data);
      return;
    }
    const health = await call(API.health());
    if (health.status === 200 && health.data) {
      renderBanners(health.data, h("div", {class: "banner error", text: failure(result)}));
      byId("run-id").textContent = health.data.run_id || "-";
      byId("scenario").textContent = health.data.scenario || "-";
      byId("paypal-mode").textContent = health.data.paypal_mode || "-";
      byId("llm-mode").textContent = health.data.llm_mode || "-";
    } else {
      byId("banners").replaceChildren(h("div", {class: "banner error", text: failure(result)}));
    }
  } finally {
    ui.busy = false;
  }
}

// ---- actions ----------------------------------------------------------------

async function submitFixture(milestone, fixture) {
  notice(`m:${milestone.id}`, "info", `Verifying ${fixture}…`);
  const result = await call(API.deliverable(milestone.id), {fixture, submitted_by: CONTRACTOR});
  notice(`m:${milestone.id}`, result.status === 200 ? "ok" : "error",
    result.status === 200 ? `${fixture}: ${result.data.overall}` : failure(result));
  refresh();
}

async function uploadFiles(milestone) {
  const files = ui.files[milestone.id] || {};
  if (!files.csv || !files.report) return;
  const form = new FormData();
  form.append("csv_file", files.csv);
  form.append("report_file", files.report);
  form.append("submitted_by", CONTRACTOR);
  notice(`m:${milestone.id}`, "info", `Verifying ${files.csv.name}…`);
  const result = await call(API.deliverable(milestone.id), form);
  if (result.status === 200) delete ui.files[milestone.id];
  notice(`m:${milestone.id}`, result.status === 200 ? "ok" : "error",
    result.status === 200 ? `${files.csv.name}: ${result.data.overall}` : failure(result));
  refresh();
}

async function pay(milestone, key) {
  const result = await call(API.pay(milestone.id), Object.assign(approval(), {
    expected_amount_cents: milestone.amount_cents,
  }));
  const title = key === "ledger" ? `Resend ${milestone.id}` : `Pay ${milestone.id}`;
  const gate = recordGate(key === "ledger" ? "ledger" : `pay:${milestone.id}`, title, result);
  if (result.status === 200) {
    const p = result.data.payout || {};
    notice(key, "ok", `Payout ${p.id || ""} is ${p.state || "created"}.`);
  } else if (result.status === 409 && gate) {
    notice(key, "error", `Refused by the gate: ${refusedChecks(gate).map((c) => c.id).join(", ")}. No PayPal call was made.`);
  } else {
    notice(key, "error", failure(result));
  }
  refresh();
}

async function retry(payoutId, key) {
  const result = await call(API.retry(payoutId), approval());
  const gate = recordGate(key, `Retry ${payoutId}`, result);
  if (result.status === 200) {
    const p = result.data.payout || {};
    notice(key, "ok", `Retried with the same key. Payout ${p.id || payoutId} is ${p.state || "-"}` +
      (result.data.case_id ? `; case ${result.data.case_id} opened.` : "; PayPal's outcome is still unknown."));
  } else if (result.status === 409 && gate) {
    notice(key, "error", `Refused by the gate: ${refusedChecks(gate).map((c) => c.id).join(", ")}.`);
  } else {
    notice(key, "error", failure(result));
  }
  refresh();
}

async function investigate(caseId) {
  notice(`case:${caseId}`, "info", "Investigating with read-only tools (up to 45 s)…");
  const result = await call(API.investigate(caseId));
  notice(`case:${caseId}`, result.status === 200 ? "ok" : "error",
    result.status === 200 ? `Recommendation: ${result.data.recommended_action}` : failure(result));
  refresh();
}

async function caseAction(c, action, body) {
  const key = `case:${c.id}`;
  const result = await call(API[action](c.id), body);
  const gate = recordGate(key, `${action} ${time(new Date().toISOString())}`, result);
  if (result.status === 200) {
    const p = result.data.payout || {};
    notice(key, "ok", `${action}: payout ${p.id || c.payout_id} is ${p.state || "-"}, case ${(result.data.case || {}).state || "-"}.`);
  } else if (result.status === 409 && gate) {
    notice(key, "error", `Refused by the gate: ${refusedChecks(gate).map((x) => x.id).join(", ")}. No PayPal write was made.`);
  } else {
    notice(key, "error", failure(result));
  }
  refresh();
}

async function reset(scenario) {
  ui.gates = {};
  ui.notices = {};
  ui.files = {};
  ui.reissue = {};
  const result = await call(API.reset(), {scenario});
  if (result.status !== 200) {
    byId("banners").replaceChildren(h("div", {class: "banner error", text: failure(result)}));
  }
  ui.lastJson = "";
  refresh();
}

async function sendMessage(event) {
  event.preventDefault();
  const from = byId("inbox-from").value.trim();
  const text = byId("inbox-text").value.trim();
  if (!from || !text) return;
  const contractId = (ui.state && ui.state.contract && ui.state.contract.id) || "c1";
  const result = await call(API.messages(), {contract_id: contractId, from_email: from, body: text});
  const box = byId("inbox-notice");
  if (result.status === 200) {
    byId("inbox-text").value = "";
    const d = result.data || {};
    box.replaceChildren(h("div", {class: "notice ok",
      text: d.case_id ? `Message stored. Case ${d.case_id} opened${d.diversion_risk ? " with diversion risk flagged" : ""}.`
                      : "Message stored. No detection rule fired."}));
  } else {
    box.replaceChildren(h("div", {class: "notice error", text: failure(result)}));
  }
  refresh();
}

function boot() {
  byId("reset-a").addEventListener("click", () => reset("A"));
  byId("reset-b").addEventListener("click", () => reset("B"));
  byId("inbox-form").addEventListener("submit", sendMessage);
  document.addEventListener("focusout", () => setTimeout(rerender, 0));
  refresh();
  setInterval(refresh, POLL_MS);
}

// ---- node export (render smoke test only; the browser boots instead) ---------
if (typeof module !== "undefined" && module.exports) {
  module.exports = {render, ui};
} else {
  boot();
}
