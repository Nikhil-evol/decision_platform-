const PRESETS = {
  msme_credit_underwriting: {
    approve: {
      applicant_name: "Aarav Sharma",
      age: 32,
      monthly_net_inflow: 145000,
      existing_monthly_emi: 15000,
      proposed_emi: 28000,
      loan_amount: 800000,
      kyc_verified: true,
      borrower_segment: "msme_proprietor",
      inward_bounces_6m: 0
    },
    review: {
      applicant_name: "Meera Nair",
      age: 29,
      monthly_net_inflow: 65000,
      existing_monthly_emi: 18000,
      proposed_emi: 22000,
      loan_amount: 550000,
      kyc_verified: true,
      borrower_segment: "salaried",
      inward_bounces_6m: 1
    },
    reject: {
      applicant_name: "Rohan Verma",
      age: 35,
      monthly_net_inflow: 40000,
      existing_monthly_emi: 10000,
      proposed_emi: 12000,
      loan_amount: 300000,
      kyc_verified: false,
      borrower_segment: "salaried"
    },
    invalid: {
      applicant_name: "Invalid Type Payload",
      age: "thirty-two",
      monthly_net_inflow: -500
    }
  },
  gst_vendor_onboarding: {
    approve: {
      entity_name: "Kaveri Precision Components Pvt Ltd",
      gstin: "29AAACR5055K1Z5",
      annual_turnover: 12000000,
      contract_value: 1800000,
      msme_udyam_verified: true
    },
    review: {
      entity_name: "Vindhya Logistics & Infra Ltd",
      gstin: "09AABCV4120N1Z2",
      annual_turnover: 9000000,
      contract_value: 3200000,
      msme_udyam_verified: false
    },
    reject: {
      entity_name: "Apex Cancelled Traders",
      gstin: "00INVALID123456",
      annual_turnover: 6000000,
      contract_value: 500000,
      msme_udyam_verified: false
    },
    invalid: {
      entity_name: "Missing GSTIN Corp"
    }
  },
  kyc_document_verification: {
    approve: {
      full_name: "Ananya Joshi",
      pan_number: "ABCDE1234F",
      document_tamper_score: 4.5
    },
    review: {
      full_name: "Karthik Menon",
      pan_number: "PQRST6789K",
      document_tamper_score: 12.0
    },
    reject: {
      full_name: "Tampered Statement Upload",
      pan_number: "ABCDE1234F",
      document_tamper_score: 68.0
    },
    invalid: {
      full_name: "Bad Score Type",
      pan_number: "ABCDE1234F",
      document_tamper_score: "high"
    }
  }
};

let workflowsCache = [];
let lastExecutionId = null;

function generateIdempotencyKey() {
  const rand = Math.random().toString(36).substring(2, 8);
  return `scoreme-${Date.now()}-${rand}`;
}

function showAlert(el, message, type = "error") {
  el.textContent = message;
  el.className = `alert alert-${type}`;
}

function hideAlert(el) {
  el.className = "alert hidden";
}

function statusPillClass(status) {
  if (status === "APPROVED") return "pill-ok";
  if (status === "MANUAL_REVIEW" || status === "RETRY_PENDING") return "pill-warn";
  return "pill-bad";
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

async function init() {
  document.getElementById("idempotencyKeyInput").value = generateIdempotencyKey();

  try {
    await fetchJson("/api/v1/health");
    const hp = document.getElementById("healthPill");
    hp.textContent = "Engine Online";
    hp.className = "pill pill-ok";
  } catch {
    const hp = document.getElementById("healthPill");
    hp.textContent = "Engine Offline";
    hp.className = "pill pill-bad";
  }

  await loadWorkflows();
  await refreshTelemetry();
  await refreshLedger();
}

async function loadWorkflows() {
  const data = await fetchJson("/api/v1/workflows");
  workflowsCache = data.workflows;

  const sel = document.getElementById("workflowSelect");
  const cfgSel = document.getElementById("configWorkflowSelect");
  sel.innerHTML = "";
  cfgSel.innerHTML = "";

  workflowsCache.forEach((wf) => {
    const opt1 = new Option(`${wf.name} (${wf.workflow_id} v${wf.version})`, wf.workflow_id);
    const opt2 = new Option(`${wf.workflow_id} (v${wf.version})`, wf.workflow_id);
    sel.add(opt1);
    cfgSel.add(opt2);
  });

  onWorkflowChange();
  await loadRawYaml(cfgSel.value);
}

function onWorkflowChange() {
  const wfId = document.getElementById("workflowSelect").value;
  const wf = workflowsCache.find((w) => w.workflow_id === wfId);
  if (!wf) return;
  document.getElementById("workflowDesc").textContent = wf.description;
  document.getElementById("activeConfigHash").textContent = `v${wf.version} | hash: ${wf.config_hash}`;
  applyPreset("approve");
}

function applyPreset(kind) {
  const wfId = document.getElementById("workflowSelect").value;
  const wfPresets = PRESETS[wfId] || PRESETS.msme_credit_underwriting;
  const sample = wfPresets[kind] || wfPresets.approve;
  document.getElementById("payloadEditor").value = JSON.stringify(sample, null, 2);
}

async function submitDecision(reuseKey = false) {
  const alertEl = document.getElementById("requestAlert");
  hideAlert(alertEl);

  if (!reuseKey) {
    // Keep user-entered key as is
  }

  let parsedPayload;
  try {
    parsedPayload = JSON.parse(document.getElementById("payloadEditor").value);
  } catch (err) {
    showAlert(alertEl, `Invalid JSON in payload editor: ${err.message}`);
    return;
  }

  const wfId = document.getElementById("workflowSelect").value;
  const idemKey = document.getElementById("idempotencyKeyInput").value.trim() || generateIdempotencyKey();
  document.getElementById("idempotencyKeyInput").value = idemKey;

  try {
    const res = await fetchJson("/api/v1/decisions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idemKey
      },
      body: JSON.stringify({
        workflow_id: wfId,
        payload: parsedPayload,
        simulate_storage_failure: document.getElementById("simulateStorageFail").checked
      })
    });
    renderDecisionResult(res);
    await refreshTelemetry();
    await refreshLedger();
  } catch (err) {
    showAlert(alertEl, err.message, "error");
    await refreshLedger();
  }
}

function renderDecisionResult(data) {
  lastExecutionId = data.execution_id;
  document.getElementById("emptyResult").classList.add("hidden");
  document.getElementById("resultContainer").classList.remove("hidden");

  const replayBadge = document.getElementById("replayBadge");
  if (data.idempotent_replay) {
    replayBadge.classList.remove("hidden");
  } else {
    replayBadge.classList.add("hidden");
  }

  const banner = document.getElementById("decisionBanner");
  banner.className = `decision-banner ${data.status}`;
  document.getElementById("resultStatus").textContent = data.status;
  document.getElementById("resultExecId").textContent = data.execution_id;
  document.getElementById("resultWfVersion").textContent = `${data.workflow_id} v${data.workflow_version} (${data.config_hash})`;
  document.getElementById("resultRetries").textContent = String(data.retry_count);
  document.getElementById("resultSummary").textContent = data.decision_summary;

  const retryBox = document.getElementById("retryActionBox");
  if (data.status === "RETRY_PENDING") {
    retryBox.classList.remove("hidden");
  } else {
    retryBox.classList.add("hidden");
  }

  const timeline = document.getElementById("stateTimeline");
  timeline.innerHTML = "";
  data.state_history.forEach((tr) => {
    const item = document.createElement("div");
    item.className = "timeline-item";
    const fromTxt = tr.from_state ? `${tr.from_state} → ` : "";
    item.innerHTML = `<span class="pill ${statusPillClass(tr.to_state)}">${fromTxt}${tr.to_state}</span><span>${tr.reason}</span>`;
    timeline.appendChild(item);
  });

  document.getElementById("enrichedBox").textContent = JSON.stringify(data.enriched_data, null, 2);

  const auditList = document.getElementById("auditList");
  auditList.innerHTML = "";
  data.audit_trail.forEach((entry) => {
    const card = document.createElement("div");
    card.className = `audit-card ${entry.passed ? "pass" : "fail"}`;
    card.innerHTML = `
      <div class="audit-top">
        <span class="audit-title">#${entry.sequence} [${entry.step_id}] ${entry.rule_name}</span>
        <span class="pill ${entry.passed ? "pill-ok" : "pill-bad"}">${entry.passed ? "PASS" : "FAIL"} → ${entry.action_taken}</span>
      </div>
      <div class="audit-expr">${entry.expression}</div>
      <p class="audit-reason">${entry.reasoning}</p>
      <div class="audit-data">Referenced Data: ${JSON.stringify(entry.data_referenced)}</div>
    `;
    auditList.appendChild(card);
  });
}

async function retryCurrentExecution() {
  if (!lastExecutionId) return;
  const alertEl = document.getElementById("requestAlert");
  hideAlert(alertEl);
  try {
    await fetchJson("/api/v1/simulator/reset", { method: "POST" });
    const res = await fetchJson(`/api/v1/executions/${lastExecutionId}/retry`, { method: "POST" });
    renderDecisionResult(res);
    await refreshTelemetry();
    await refreshLedger();
  } catch (err) {
    showAlert(alertEl, err.message, "error");
  }
}

async function loadRawYaml(wfId) {
  const data = await fetchJson(`/api/v1/workflows/${wfId}/raw`);
  document.getElementById("yamlEditor").value = data.yaml_content;
}

async function saveRawYaml() {
  const wfId = document.getElementById("configWorkflowSelect").value;
  const alertEl = document.getElementById("configAlert");
  hideAlert(alertEl);
  try {
    const res = await fetchJson(`/api/v1/workflows/${wfId}/raw`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ yaml_content: document.getElementById("yamlEditor").value })
    });
    showAlert(
      alertEl,
      `Saved & hot-reloaded '${res.workflow_id}' (version ${res.version}, new config_hash: ${res.config_hash}).`,
      "success"
    );
    await loadWorkflows();
  } catch (err) {
    showAlert(alertEl, err.message, "error");
  }
}

async function refreshTelemetry() {
  const data = await fetchJson("/api/v1/simulator/status");
  const grid = document.getElementById("telemetryGrid");
  grid.innerHTML = "";
  Object.entries(data.services).forEach(([svc, info]) => {
    const div = document.createElement("div");
    div.className = "telemetry-item";
    const statusPill = info.failure_configured
      ? `<span class="pill pill-bad">FAULT ACTIVE (${info.permanent ? "PERMANENT" : `fail ${info.fail_times}x`})</span>`
      : `<span class="pill pill-ok">HEALTHY</span>`;
    div.innerHTML = `
      <div>
        <strong class="mono">${svc}</strong>
        <div class="muted-text">Total Calls Recorded: <strong>${info.total_calls}</strong></div>
      </div>
      <div>${statusPill}</div>
    `;
    grid.appendChild(div);
  });
}

async function applyFaultInjection() {
  const alertEl = document.getElementById("faultAlert");
  hideAlert(alertEl);
  try {
    await fetchJson("/api/v1/simulator/failures", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        service: document.getElementById("faultServiceSelect").value,
        fail_times: Number(document.getElementById("failTimesInput").value),
        permanent: document.getElementById("permanentFailCheck").checked
      })
    });
    showAlert(alertEl, "Fault mode injected. Submit a request in Tab 1 to observe retry handling.", "success");
    await refreshTelemetry();
  } catch (err) {
    showAlert(alertEl, err.message, "error");
  }
}

async function resetFaults() {
  const alertEl = document.getElementById("faultAlert");
  await fetchJson("/api/v1/simulator/reset", { method: "POST" });
  showAlert(alertEl, "All simulated external service faults cleared.", "success");
  await refreshTelemetry();
}

async function refreshLedger() {
  const rows = await fetchJson("/api/v1/executions?limit=30");
  const body = document.getElementById("ledgerBody");
  body.innerHTML = "";
  rows.forEach((exec) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${exec.execution_id}</td>
      <td>${exec.workflow_id} <span class="muted-text">v${exec.workflow_version}</span></td>
      <td class="mono">${exec.config_hash}</td>
      <td class="mono">${exec.idempotency_key}</td>
      <td><span class="pill ${statusPillClass(exec.status)}">${exec.status}</span></td>
      <td>${exec.retry_count}</td>
      <td>${new Date(exec.updated_at).toLocaleTimeString()}</td>
      <td><button type="button" class="btn btn-chip">Inspect</button></td>
    `;
    tr.addEventListener("click", () => {
      renderDecisionResult(exec);
      switchTab("runner");
    });
    body.appendChild(tr);
  });
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tabName));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tabName}`));
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});
document.querySelectorAll("[data-preset]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("idempotencyKeyInput").value = generateIdempotencyKey();
    applyPreset(btn.dataset.preset);
  });
});
document.getElementById("workflowSelect").addEventListener("change", () => {
  document.getElementById("idempotencyKeyInput").value = generateIdempotencyKey();
  onWorkflowChange();
});
document.getElementById("regenKeyBtn").addEventListener("click", () => {
  document.getElementById("idempotencyKeyInput").value = generateIdempotencyKey();
});
document.getElementById("submitDecisionBtn").addEventListener("click", () => submitDecision(false));
document.getElementById("replayDuplicateBtn").addEventListener("click", () => submitDecision(true));
document.getElementById("retryPendingBtn").addEventListener("click", retryCurrentExecution);
document.getElementById("configWorkflowSelect").addEventListener("change", (e) => loadRawYaml(e.target.value));
document.getElementById("saveYamlBtn").addEventListener("click", saveRawYaml);
document.getElementById("applyFaultBtn").addEventListener("click", applyFaultInjection);
document.getElementById("resetFaultsBtn").addEventListener("click", resetFaults);
document.getElementById("refreshTelemetryBtn").addEventListener("click", refreshTelemetry);
document.getElementById("refreshLedgerBtn").addEventListener("click", refreshLedger);

init();
