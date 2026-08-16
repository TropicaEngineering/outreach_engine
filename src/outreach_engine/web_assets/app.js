const list = document.querySelector("#opportunities");
const dialog = document.querySelector("#detail-dialog");
const detail = document.querySelector("#detail");
const search = document.querySelector("#search");
const profileDialog = document.querySelector("#profile-dialog");
const profileForm = document.querySelector("#profile-form");
let items = [];
let bidProfile = {};
let activeOpportunity = null;

const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
})[char]);

const safeUrl = value => {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch { return ""; }
};

const formatDate = value => {
  if (!value) return "Not stated";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/London",
    timeZoneName: "short",
  }).format(parsed);
};

const fileSize = bytes => {
  const value = Number(bytes || 0);
  if (!value) return "";
  return value < 1000000 ? `${Math.ceil(value / 1000)} KB` : `${(value / 1000000).toFixed(1)} MB`;
};

function accessLabel(item) {
  if (item.bid_pack_status === "found") return "Pack ready";
  if (item.pack_access_status === "resolved") return displayAccessLabel(item.pack_access_label) || "Route found";
  if (item.pack_access_status === "advert_only") return "Advert available";
  if (item.pack_access_status === "unavailable") return "Advert available";
  return "Route not checked";
}

function displayAccessLabel(value = "") {
  const label = String(value);
  if (label.includes("etenderwales.bravosolution")) return "eTenderWales";
  return label;
}

function visibleItems() {
  const term = search.value.trim().toLowerCase();
  return items.filter(item => {
    if (item.action !== "review") return false;
    return !term || `${item.title} ${item.organization}`.toLowerCase().includes(term);
  });
}

function inlineMarkdown(value = "") {
  return escapeHtml(value).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderBidMarkdown(markdown = "") {
  const lines = String(markdown).split("\n");
  const output = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) continue;
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 3, 6);
      output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    if (line.startsWith("|") && lines[index + 1]?.match(/^\|[\s:|-]+\|$/)) {
      const rows = [line];
      index += 2;
      while (index < lines.length && lines[index].startsWith("|")) {
        rows.push(lines[index]);
        index += 1;
      }
      index -= 1;
      output.push(`<div class="table-scroll"><table>${rows.map((row, rowIndex) => {
        const cells = row.slice(1, -1).split("|").map(cell => cell.trim());
        const tag = rowIndex === 0 ? "th" : "td";
        return `<tr>${cells.map(cell => `<${tag}>${inlineMarkdown(cell)}</${tag}>`).join("")}</tr>`;
      }).join("")}</table></div>`);
      continue;
    }
    const checklist = line.match(/^- \[([ xX])\]\s+(.+)$/);
    if (checklist) {
      output.push(`<p class="draft-list"><span class="mini-check ${checklist[1].trim() ? "checked" : ""}">${checklist[1].trim() ? "✓" : ""}</span>${inlineMarkdown(checklist[2])}</p>`);
      continue;
    }
    const bullet = line.match(/^-\s+(.+)$/);
    if (bullet) {
      output.push(`<p class="draft-list"><span>•</span>${inlineMarkdown(bullet[1])}</p>`);
      continue;
    }
    output.push(`<p>${inlineMarkdown(line)}</p>`);
  }
  return output.join("");
}

function render() {
  const rows = visibleItems();
  document.querySelector("#opportunity-count").textContent = rows.length ? `(${rows.length})` : "";
  if (!rows.length) {
    list.innerHTML = '<div class="empty">No live tenders match this view.</div>';
    return;
  }
  list.innerHTML = rows.map(item => {
    const responseReady = Boolean(item.draft_id && item.bid_pack_status === "found");
    return `<article class="opportunity-row">
    <div class="opportunity-copy">
      <p class="row-kicker">${escapeHtml(item.organization || "Buyer not stated")}</p>
      <h3>${escapeHtml(item.title)}</h3>
      <p class="row-meta">${item.deadline ? `Closes ${escapeHtml(formatDate(item.deadline))}` : "Deadline not stated"}</p>
    </div>
    <div class="row-state">
      <span class="state-dot ${item.bid_pack_status === "found" ? "ready" : ""}"></span>
      <span>${escapeHtml(accessLabel(item))}</span>
    </div>
    <div class="row-state">
      <span class="state-dot ${responseReady ? "ready" : ""}"></span>
      <span>${responseReady ? "Response ready" : "Not generated"}</span>
    </div>
    <button class="open-button" data-id="${escapeHtml(item.id)}">Open</button>
  </article>`;
  }).join("");
  document.querySelectorAll(".open-button").forEach(button => {
    button.addEventListener("click", () => openDetail(button.dataset.id));
  });
}

async function load() {
  const response = await fetch("/api/opportunities");
  if (!response.ok) throw new Error("Could not load tenders");
  items = (await response.json()).items;
  render();
}

async function loadBidProfile() {
  const response = await fetch("/api/bid-profile");
  if (!response.ok) throw new Error("Could not load bid profile");
  bidProfile = await response.json();
  for (const [key, value] of Object.entries(bidProfile)) {
    if (profileForm.elements[key]) profileForm.elements[key].value = value;
  }
}

async function openBidProfile() {
  await loadBidProfile();
  document.querySelector("#profile-status").textContent = "Stored locally";
  if (!profileDialog.open) profileDialog.showModal();
}

function storedDocument(document) {
  const filename = document.filename || document.title || "Tender document";
  const url = safeUrl(document.final_url || document.source_url);
  const name = url
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(filename)}</a>`
    : `<strong>${escapeHtml(filename)}</strong>`;
  const role = document.document_role.replaceAll("_", " ");
  const usage = document.is_core ? "used for response" : "stored · not used for response";
  return `<li><span class="check">${document.is_core ? "✓" : "·"}</span><div>${name}<small>${escapeHtml(role)} · ${usage}${fileSize(document.size_bytes) ? ` · ${escapeHtml(fileSize(document.size_bytes))}` : ""}</small></div><button class="document-remove" data-remove-document="${escapeHtml(document.id)}" type="button">Remove</button></li>`;
}

function routeAction(item) {
  const attributes = item.attributes || {};
  const accessType = attributes.pack_access_type || "advert";
  const accessUrl = safeUrl(attributes.pack_access_url || item.url || "");
  const accessEmail = String(attributes.pack_access_email || "").trim();
  const label = displayAccessLabel(attributes.pack_access_label) || "Tender advert";
  if (accessType === "email_request" && accessEmail) {
    const subject = encodeURIComponent(`Tender documents: ${item.title}`);
    return `<a class="primary button-link" href="mailto:${escapeHtml(accessEmail)}?subject=${subject}">Email ${escapeHtml(accessEmail)}</a>`;
  }
  if (accessUrl) {
    const actionLabel = accessType === "direct_download" ? label : `Open ${label}`;
    return `<a class="primary button-link" href="${escapeHtml(accessUrl)}" target="_blank" rel="noreferrer">${escapeHtml(actionLabel)} ↗</a>`;
  }
  return "";
}

function officialDocumentLinks(item) {
  const urls = item.attributes?.pack_document_urls || [];
  if (!urls.length) return "";
  return `<div class="official-documents"><p class="micro-label">Documents listed in the official notice</p>${urls.map((url, index) => {
    const safe = safeUrl(url);
    return safe ? `<a href="${escapeHtml(safe)}" target="_blank" rel="noreferrer">Document ${index + 1} ↗</a>` : "";
  }).join("")}</div>`;
}

function submissionLink(item) {
  const submissionUrl = safeUrl(item.attributes?.submission_url || "");
  const accessUrl = safeUrl(item.attributes?.pack_access_url || "");
  if (!submissionUrl || submissionUrl === accessUrl) return "";
  return `<div class="submission-route"><span>Submission route identified</span><a href="${escapeHtml(submissionUrl)}" target="_blank" rel="noreferrer">Open submission page ↗</a></div>`;
}

function packStep(item, storedDocuments) {
  const attributes = item.attributes || {};
  const packReady = attributes.bid_pack_status === "found";
  const coreCount = storedDocuments.filter(document => document.is_core).length;
  const routeStatus = attributes.pack_access_status || "not_checked";
  const routeEvidence = attributes.pack_access_evidence || "";
  const submission = attributes.submission_method || "";
  const evidenceCopy = routeEvidence === attributes.pack_access_url
    ? "The official notice directs suppliers to this site."
    : routeEvidence;
  if (packReady) {
    return `<div class="step-content">
      <div class="step-result ready-result"><span class="result-mark">✓</span><div><strong>Bid pack ready</strong><p>${coreCount} governing document${coreCount === 1 ? "" : "s"} will be used. Remove anything attached by mistake before regenerating.</p></div></div>
      <ul class="document-list">${storedDocuments.map(storedDocument).join("")}</ul>
      <div class="inline-actions">${routeAction(item)}<button class="secondary upload-trigger" data-id="${escapeHtml(item.id)}">Add documents</button></div>
    </div>`;
  }
  const routeCopy = routeStatus === "resolved"
    ? `<strong>${escapeHtml(displayAccessLabel(attributes.pack_access_label) || "Official route found")}</strong><p>${submission ? `${escapeHtml(submission)}. ` : ""}${escapeHtml(evidenceCopy || "The official notice provides the tender route.")}</p>`
    : routeStatus === "not_checked"
      ? '<strong>Check the official notice</strong><p>Resolve the exact download, portal or email route from the notice ID.</p>'
      : `<strong>${escapeHtml(attributes.pack_access_label || "Tender advert available")}</strong><p>${escapeHtml(routeEvidence || "Open the advert to obtain the documents.")}</p>`;
  const checkButton = routeStatus === "not_checked"
    ? `<button class="secondary stage-button" data-stage="route" data-id="${escapeHtml(item.id)}">Check official notice</button>`
    : "";
  return `<div class="step-content">
    <div class="step-result"><span class="result-mark">↗</span><div>${routeCopy}</div></div>
    ${officialDocumentLinks(item)}
    ${submissionLink(item)}
    <div class="inline-actions">${routeAction(item)}${checkButton}</div>
    ${storedDocuments.length ? `<ul class="document-list pending-documents">${storedDocuments.map(storedDocument).join("")}</ul>` : ""}
    <div class="drop-zone" data-id="${escapeHtml(item.id)}" tabindex="0" role="button" aria-label="Upload tender documents">
      <strong>Drop the bid pack here</strong>
      <span>PDF, Word, Excel, PowerPoint or ZIP · up to 20 MB each</span>
      <button class="secondary upload-trigger" data-id="${escapeHtml(item.id)}">Choose files</button>
    </div>
  </div>`;
}

function responseControlPanel(item) {
  const metadata = item.draft_metadata || {};
  const brief = metadata.brief || {};
  const checklist = metadata.submission_checklist || [];
  const missing = metadata.missing_inputs || [];
  const commercialPattern = /\b(price|pricing|fee|fees|rate|rates|cost|commercial|vat)\b/i;
  const contentInputs = missing.filter(entry => !commercialPattern.test(`${entry.item} ${entry.why}`));
  const commercialInputs = missing.filter(entry => commercialPattern.test(`${entry.item} ${entry.why}`));
  const savedInputs = item.attributes?.bid_inputs || {};
  const deliverables = metadata.deliverables || [];
  const pricing = metadata.pricing || {};
  const packComplete = brief.pack_status === "complete";
  const portalAction = routeAction(item);
  const pricingRow = pricing.required
    ? `<li><span class="control-state ${pricing.status === "drafted" ? "ready" : "attention"}"></span><div><strong>Pricing schedule</strong><small>${escapeHtml(pricing.target_total || "Commercial input required")}</small></div></li>`
    : "";
  const deliverableRows = deliverables.map(deliverable => `<li><span class="control-state ${deliverable.status === "drafted" ? "ready" : "attention"}"></span><div><strong>${escapeHtml(deliverable.title)}</strong><small>${escapeHtml(deliverable.status.replaceAll("_", " "))}</small></div></li>`).join("");
  const handlingLabels = {
    generated_in_pack: "Generated in pack",
    manual_form: "Complete manually",
    separate_attachment: "Attach separately",
    portal_entry: "Enter in portal",
    commercial_check: "Commercial check",
  };
  const checklistRows = checklist.map(entry => {
    const handling = entry.handling || "generated_in_pack";
    return `<li><span class="mini-check ${entry.status === "ready" ? "checked" : ""}">${entry.status === "ready" ? "✓" : ""}</span><div>${escapeHtml(entry.item)}<small>${escapeHtml(handlingLabels[handling])}${entry.output ? ` · ${escapeHtml(entry.output)}` : ""}</small></div></li>`;
  }).join("");
  const missingFields = contentInputs.map(entry => `<label class="bid-input-field"><span>${escapeHtml(entry.item)}</span><textarea rows="3" data-bid-input="${escapeHtml(entry.item)}" placeholder="${escapeHtml(entry.action)}">${escapeHtml(savedInputs[entry.item] || "")}</textarea><small>${escapeHtml(entry.why)}</small></label>`).join("");
  const readyCount = deliverables.filter(deliverable => deliverable.status === "drafted").length + (pricing.required && pricing.status === "drafted" ? 1 : 0);
  const handlingCounts = checklist.reduce((counts, entry) => {
    const handling = entry.handling || "generated_in_pack";
    counts[handling] = (counts[handling] || 0) + 1;
    return counts;
  }, {});
  const handlingSummary = Object.entries(handlingCounts).map(([handling, count]) => `<span><b>${count}</b> ${escapeHtml(handlingLabels[handling] || handling.replaceAll("_", " "))}</span>`).join("");
  const winThemes = (brief.tailored_win_themes || []).map(theme => `<span>${escapeHtml(theme)}</span>`).join("");
  return `<aside class="response-controls">
    <div class="control-summary">
      <div><p class="micro-label">Bid brief</p><strong>${escapeHtml(brief.objective || "Response documents prepared from the supplied pack.")}</strong><p>${escapeHtml(brief.recommended_approach || "Review the generated documents and complete any highlighted company inputs.")}</p></div>
      <div class="control-badges"><span class="pack-confidence ${packComplete ? "complete" : "check"}">${packComplete ? "Requirements mapped" : "Portal check identified"}</span><span>${readyCount} drafted · ${contentInputs.length} content inputs</span></div>
    </div>
    <div class="assurance-grid"><div><p class="micro-label">Requirements coverage</p><p>${escapeHtml(brief.requirements_assurance || "The generated documents are mapped to the governing source pack.")}</p></div><div><p class="micro-label">Tailored win themes</p><div class="theme-list">${winThemes || "<span>Buyer priorities carried into the response</span>"}</div></div></div>
    ${handlingSummary ? `<div class="submission-map"><strong>Submission pack built</strong>${handlingSummary}</div>` : ""}
    ${portalAction ? `<div class="portal-strip"><div><strong>Official tender route</strong><p>${packComplete ? "Open the confirmed portal for submission." : escapeHtml(brief.pack_note || "Use the confirmed portal for the remaining tender material.")}</p></div>${portalAction}</div>${submissionLink(item)}` : ""}
    <div class="control-columns">
      <details><summary>Prepared documents <span>${deliverables.length + (pricing.required ? 1 : 0)}</span></summary><ul class="control-list">${deliverableRows}${pricingRow}</ul></details>
      <details><summary>Submission instructions <span>${checklist.length}</span></summary><ul class="control-list checklist-list">${checklistRows || "<li>No separate submission items identified.</li>"}</ul></details>
    </div>
    <section class="missing-panel"><h4>${contentInputs.length ? `Add the remaining bid evidence <span>${contentInputs.length}</span>` : "Bid content ready for review"}</h4>${contentInputs.length ? `<div class="bid-input-grid">${missingFields}</div><div class="input-submit"><p>Add what you know; blank fields remain clearly marked in the bid.</p><button class="primary" data-save-inputs="${escapeHtml(item.id)}" type="button">Save evidence & regenerate</button></div>` : "<p>No obvious content input is missing from the current first pass.</p>"}${commercialInputs.length || checklist.some(entry => entry.handling === "commercial_check") ? `<div class="commercial-note"><strong>Commercial check</strong><span>Review generated prices, later-phase fees and day rates before submission. This does not block the bid draft.</span></div>` : ""}</section>
  </aside>`;
}

function draftSection(item, canDraft) {
  if (!item.draft_id || !canDraft) {
    return `<div class="step-content generate-row"><div><strong>${canDraft ? "Ready to build the response pack" : "Waiting for the bid pack"}</strong><p>${canDraft ? "Draft the requested answers, forms, checklist and pricing schedule from the documents—not another summary." : "Upload the governing tender documents first."}</p><button class="profile-inline" data-open-profile type="button">Edit tone, pricing and experience</button></div><button class="primary stage-button" data-stage="draft" data-id="${escapeHtml(item.id)}" ${canDraft ? "" : "disabled"}>Build response pack</button></div>`;
  }
  return `<div class="step-content response-workspace"><div class="step-result ready-result"><span class="result-mark">✓</span><div><strong>Submission pack built</strong><p>Requirements, generated documents and separate submission actions are mapped below.</p></div></div>${responseControlPanel(item)}<section class="artifact-downloads" data-artifacts-for="${escapeHtml(item.id)}"><div><p class="micro-label">Editable submission files</p><strong>Preparing Word and spreadsheet files…</strong><p>The file set follows the submission map above.</p></div><span class="spinner" aria-hidden="true"></span></section><article class="draft-document"><p class="micro-label">Bid documents</p><h3>${escapeHtml(item.draft_subject)}</h3><div class="draft-body">${renderBidMarkdown(item.draft_body)}</div></article><div class="inline-actions review-actions"><button class="profile-inline" data-open-profile type="button">Edit bid profile</button><button class="quiet stage-button" data-stage="draft" data-id="${escapeHtml(item.id)}">Regenerate</button><button class="primary" data-review="approve" data-draft="${escapeHtml(item.draft_id)}">Approve first pass</button></div></div>`;
}

function renderDetail(item) {
  activeOpportunity = item;
  const attributes = item.attributes || {};
  const advertUrl = safeUrl(item.url || "");
  const documents = item.documents || [];
  const relevantDocuments = documents.filter(document => document.status === "retrieved" && (document.is_core || document.source_url.startsWith("user-upload:///")));
  const documentsByContent = new Map();
  relevantDocuments.forEach(document => {
    const key = document.content_hash || document.source_url;
    const existing = documentsByContent.get(key);
    if (!existing || document.source_url.startsWith("user-upload:///")) {
      documentsByContent.set(key, document);
    }
  });
  const storedDocuments = [...documentsByContent.values()];
  const canDraft = attributes.bid_pack_status === "found";
  detail.innerHTML = `<header class="detail-header">
    <div><p class="eyebrow">${escapeHtml(item.organization || "Tender opportunity")}</p><h2>${escapeHtml(item.title)}</h2></div>
    <button class="close" aria-label="Close">×</button>
  </header>
  <div class="document-sheet">
    <div class="tender-meta">
      <div><span>Deadline</span><strong>${escapeHtml(formatDate(item.deadline))}</strong></div>
      <div><span>Value</span><strong>${escapeHtml(item.value_text || "Not stated")}</strong></div>
      ${advertUrl ? `<a class="secondary button-link" href="${escapeHtml(advertUrl)}" target="_blank" rel="noreferrer">Open tender advert ↗</a>` : ""}
    </div>
    <section class="workflow-step done">
      <header><span class="step-number">1</span><div><p class="micro-label">Tender received</p><h3>Email parsed and deduplicated</h3></div><span class="status-word">Done</span></header>
      <div class="step-content compact"><p>${escapeHtml(item.summary || "The original tender alert has been stored with its official advert link.")}</p></div>
    </section>
    <section class="workflow-step ${canDraft ? "done" : "active"}">
      <header><span class="step-number">2</span><div><p class="micro-label">Bid pack</p><h3>Get the governing documents</h3></div><span class="status-word">${canDraft ? "Ready" : "Next"}</span></header>
      ${packStep(item, storedDocuments)}
    </section>
    <section class="workflow-step ${item.draft_id && canDraft ? "done" : ""}">
      <header><span class="step-number">3</span><div><p class="micro-label">Response</p><h3>Generate the working bid</h3></div><span class="status-word">${item.draft_id && canDraft ? "Ready" : ""}</span></header>
      ${draftSection(item, canDraft)}
    </section>
    <details class="source-details"><summary>Original tender email</summary><pre>${escapeHtml(item.signal_body)}</pre></details>
    <input id="bid-pack-upload" type="file" multiple hidden accept=".pdf,.doc,.docx,.odt,.rtf,.xls,.xlsx,.csv,.txt,.ppt,.pptx,.zip">
  </div>`;

  detail.querySelector(".close").addEventListener("click", () => dialog.close());
  detail.querySelectorAll("[data-stage]").forEach(button => button.addEventListener("click", () => runStage(button.dataset.id, button.dataset.stage, button)));
  detail.querySelectorAll("[data-review]").forEach(button => button.addEventListener("click", () => review(button.dataset.draft, button.dataset.review)));
  detail.querySelectorAll("[data-open-profile]").forEach(button => button.addEventListener("click", openBidProfile));
  detail.querySelectorAll("[data-remove-document]").forEach(button => button.addEventListener("click", () => removeDocument(item.id, button.dataset.removeDocument, button)));
  detail.querySelectorAll("[data-save-inputs]").forEach(button => button.addEventListener("click", () => saveBidInputs(item.id, button)));
  const input = detail.querySelector("#bid-pack-upload");
  detail.querySelectorAll(".upload-trigger").forEach(button => button.addEventListener("click", event => {
    event.stopPropagation();
    input.dataset.id = button.dataset.id;
    input.click();
  }));
  detail.querySelectorAll(".drop-zone").forEach(zone => {
    zone.addEventListener("click", () => { input.dataset.id = zone.dataset.id; input.click(); });
    zone.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) zone.click(); });
    zone.addEventListener("dragover", event => { event.preventDefault(); zone.classList.add("dragging"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", event => {
      event.preventDefault();
      zone.classList.remove("dragging");
      uploadBidPack(zone.dataset.id, [...event.dataTransfer.files]);
    });
  });
  input.addEventListener("change", () => uploadBidPack(input.dataset.id, [...input.files]));
  if (item.draft_id && canDraft) loadArtifacts(item.id);
}

async function loadArtifacts(opportunityId) {
  const panel = detail.querySelector(`[data-artifacts-for="${CSS.escape(opportunityId)}"]`);
  if (!panel) return;
  try {
    const response = await fetch(`/api/opportunities/${encodeURIComponent(opportunityId)}/artifacts`);
    const manifest = await response.json();
    if (!response.ok) throw new Error(manifest.message || "Could not prepare editable files");
    const files = manifest.files || [];
    const pack = files.find(file => file.id === "editable-pack");
    const individual = files.filter(file => file.id !== "editable-pack");
    const fileLinks = individual.map(file => {
      const type = file.kind === "docx" ? "Word" : file.kind === "xlsx" ? "Excel" : file.kind.toUpperCase();
      return `<a class="artifact-file" href="${escapeHtml(file.download_url)}"><span><strong>${escapeHtml(file.label)}</strong><small>${escapeHtml(type)} · ${escapeHtml(file.reason)}</small></span><b>Download</b></a>`;
    }).join("");
    panel.innerHTML = `<div class="artifact-heading"><div><p class="micro-label">Editable submission files</p><strong>${individual.length} file${individual.length === 1 ? "" : "s"} created from the buyer's instructions</strong><p>Narrative is editable in Word; pricing stays in its own working spreadsheet.</p></div>${pack ? `<a class="primary button-link" href="${escapeHtml(pack.download_url)}">Download editable pack</a>` : ""}</div><div class="artifact-file-list">${fileLinks}</div>`;
  } catch (error) {
    panel.innerHTML = `<div><p class="micro-label">Editable submission files</p><strong>Files not prepared</strong><p>${escapeHtml(error.message || "The draft is safe; retry the download preparation.")}</p></div><button class="secondary" data-retry-artifacts type="button">Retry</button>`;
    panel.querySelector("[data-retry-artifacts]")?.addEventListener("click", () => loadArtifacts(opportunityId));
  }
}

async function saveBidInputs(opportunityId, button) {
  const fields = [...detail.querySelectorAll("[data-bid-input]")];
  const inputs = Object.fromEntries(fields.map(field => [field.dataset.bidInput, field.value.trim()]));
  if (!Object.values(inputs).some(Boolean)) return toast("Add at least one bid input first");
  button.disabled = true;
  button.innerHTML = '<span class="spinner" aria-hidden="true"></span>Saving and regenerating…';
  try {
    const response = await fetch(`/api/opportunities/${encodeURIComponent(opportunityId)}/inputs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs, regenerate: true }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Could not regenerate the response");
    renderDetail(payload);
    await load();
    toast("Evidence added · response regenerated");
  } catch (error) {
    toast(error.message || "Inputs saved; use Regenerate to retry");
    await openDetail(opportunityId);
  }
}

async function removeDocument(opportunityId, documentId, button) {
  if (button.dataset.confirm !== "true") {
    button.dataset.confirm = "true";
    button.textContent = "Remove and clear response?";
    setTimeout(() => {
      if (button.isConnected) {
        button.dataset.confirm = "false";
        button.textContent = "Remove";
      }
    }, 4500);
    return;
  }
  button.disabled = true;
  button.innerHTML = '<span class="spinner" aria-hidden="true"></span>Removing…';
  try {
    const response = await fetch(`/api/opportunities/${encodeURIComponent(opportunityId)}/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Could not remove document");
    renderDetail(payload);
    await load();
    toast("Document removed · response cleared for safety");
  } catch (error) {
    toast(error.message || "Could not remove document");
    await openDetail(opportunityId);
  }
}

async function openDetail(id) {
  const response = await fetch(`/api/opportunities/${encodeURIComponent(id)}`);
  if (!response.ok) return toast("Could not open this tender");
  renderDetail(await response.json());
  if (!dialog.open) dialog.showModal();
}

async function runStage(id, stage, button) {
  const labels = {
    route: "Checking official notice…",
    draft: "Building response documents…"
  };
  button.disabled = true;
  button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${labels[stage] || "Working…"}`;
  try {
    const response = await fetch(`/api/opportunities/${encodeURIComponent(id)}/${stage}`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Could not complete this step");
    renderDetail(payload);
    await load();
    toast(stage === "route" ? "Official tender route checked" : "Response pack generated");
  } catch (error) {
    toast(error.message || "Connection interrupted. Nothing was lost.");
    await openDetail(id);
  }
}

async function saveBidProfile(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(profileForm).entries());
  payload.target_discount_percent = Number(payload.target_discount_percent || 0);
  const button = profileForm.querySelector("button[type=submit]");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const response = await fetch("/api/bid-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const saved = await response.json();
    if (!response.ok) throw new Error(saved.message || "Could not save bid profile");
    bidProfile = saved;
    document.querySelector("#profile-status").textContent = "Saved · used on the next generation";
    toast("Bid profile saved");
  } catch (error) {
    toast(error.message || "Could not save bid profile");
  } finally {
    button.disabled = false;
    button.textContent = "Save bid profile";
  }
}

async function uploadBidPack(id, files) {
  if (!files.length) return;
  toast(`Uploading ${files.length} document${files.length === 1 ? "" : "s"}…`);
  let payload;
  try {
    for (const file of files) {
      const query = new URLSearchParams({ filename: file.name, media_type: file.type || "application/octet-stream" });
      const response = await fetch(`/api/opportunities/${encodeURIComponent(id)}/upload?${query}`, {
        method: "POST",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file
      });
      payload = await response.json();
      if (!response.ok) throw new Error(payload.message || "Upload failed");
    }
    renderDetail(payload);
    await load();
    toast("Bid pack stored and checked");
  } catch (error) {
    toast(error.message || "The documents could not be uploaded.");
    await openDetail(id);
  }
}

async function review(id, action) {
  const response = await fetch(`/api/drafts/${encodeURIComponent(id)}/${action}`, { method: "POST" });
  if (!response.ok) return toast("Could not update the response");
  dialog.close();
  toast(action === "approve" ? "Working draft approved" : "Marked for revision");
  await load();
}

function toast(message) {
  const element = document.querySelector("#toast");
  element.textContent = message;
  element.classList.add("visible");
  setTimeout(() => element.classList.remove("visible"), 3200);
}

async function syncInbox() {
  const button = document.querySelector("#sync-inbox");
  button.disabled = true;
  button.innerHTML = '<span class="spinner" aria-hidden="true"></span>Syncing and checking routes…';
  try {
    const response = await fetch("/api/inbox/sync", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Could not sync Gmail");
    await load();
    toast(`${payload.new} new · ${payload.duplicates} already known`);
  } catch (error) {
    toast(error.message || "Could not sync Gmail");
  } finally {
    button.disabled = false;
    button.textContent = "Sync tender emails";
  }
}

search.addEventListener("input", render);
document.querySelector("#refresh").addEventListener("click", load);
document.querySelector("#sync-inbox").addEventListener("click", syncInbox);
document.querySelector("#open-profile").addEventListener("click", openBidProfile);
document.querySelector(".profile-close").addEventListener("click", () => profileDialog.close());
profileForm.addEventListener("submit", saveBidProfile);
dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
profileDialog.addEventListener("click", event => { if (event.target === profileDialog) profileDialog.close(); });
load().catch(() => { list.innerHTML = '<div class="empty">Could not load tenders.</div>'; });
