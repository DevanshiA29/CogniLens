const storeInput = document.querySelector("#storeId");
const slider = document.querySelector("#slider");
const selectedTime = document.querySelector("#selectedTime");
const summary = document.querySelector("#summary");
const videoUpload = document.querySelector("#videoUpload");
const processUpload = document.querySelector("#processUpload");
const uploadStatus = document.querySelector("#uploadStatus");
const videoPreview = document.querySelector("#videoPreview");
const videoMeta = document.querySelector("#videoMeta");
const metricsEl = document.querySelector("#metrics");
const executiveSummary = document.querySelector("#executiveSummary");
const summaryCards = document.querySelector("#summaryCards");
const workspaceEl = document.querySelector(".workspace");
const lowerEl = document.querySelector(".lower");
const emptyState = document.querySelector("#emptyState");
const scorePanel = document.querySelector("#scorePanel");
const scoreTotal = document.querySelector("#scoreTotal");
const scoreBreakdown = document.querySelector("#scoreBreakdown");
const scoreLabel = document.querySelector("#scoreLabel");
const scoreEvidence = document.querySelector("#scoreEvidence");
const systemStatus = document.querySelector("#systemStatus");
const videoStage = document.querySelector("#videoStage");
const debugMeta = document.querySelector("#debugMeta");
const overlayCanvas = document.querySelector("#retailOverlayCanvas");
const overlayCtx = overlayCanvas.getContext("2d");
const overlayBadges = document.querySelector("#overlayBadges");
const overlayTooltip = document.querySelector("#overlayTooltip");
const journeyReplay = document.querySelector("#journeyReplay");
const toggleVideoPlayback = document.querySelector("#toggleVideoPlayback");
const toggleVideoFocus = document.querySelector("#toggleVideoFocus");
const downloadInsights = document.querySelector("#downloadInsights");
const saveReview = document.querySelector("#saveReview");
const savedReviews = document.querySelector("#savedReviews");
const loadReview = document.querySelector("#loadReview");
let timelineStart = null;
let hasProcessedInput = false;
let timelineRequestId = 0;
let lastRenderedSecond = null;
let videoDrivenRefresh = null;
let lastVideoTimelineSecond = null;
let currentVideoFrameUrl = null;
let currentVideoCacheKey = null;
let currentVideoSourceSize = null;
let currentTimelineData = null;
let currentMetrics = null;
let currentFunnel = null;
let currentAnomalies = [];
let currentScore = null;
let hoverTarget = null;
let selectedVisitorId = null;
let overlayAnimationId = null;
let videoFocusEnabled = false;
const activeBadges = new Map();
const journeyCache = new Map();
const insightRepeatMemory = new Map();
const overlayOptions = {
  customers: true,
  employees: true,
  productEvents: true,
  heatmap: false,
  journeyPaths: true,
  anomalies: true,
};

const overlayColors = {
  customer: "#147cff",
  employee: "#ff8a00",
  returning: "#a855f7",
  group: "#00cfe8",
  product: "#00b84a",
  checkout: "#ffd23f",
  exit: "#f43f5e",
  anomaly: "#ff3030",
  zone: "rgba(255, 255, 255, 0.52)",
};

const fmt = (value) => value.toISOString().replace(".000Z", "Z");
const storeId = () => storeInput.value.trim() || "STORE_BLR_002";
const REQUEST_TIMEOUT_MS = 60000;
const DEMO_TIMEOUT_MS = 60000;
const UPLOAD_START_TIMEOUT_MS = 5 * 60 * 1000;
const VIDEO_JOB_TIMEOUT_MS = 30 * 60 * 1000;
const VIDEO_JOB_POLL_MS = 2500;

async function getJson(url, options = {}) {
  const timeoutMs = options.timeoutMs || REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  const fetchOptions = { ...options, signal: controller.signal };
  delete fetchOptions.timeoutMs;
  let response;
  try {
    response = await fetch(url, fetchOptions);
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(`The request timed out after ${Math.round(timeoutMs / 1000)}s. The hosted server may still be busy; please retry once the Space is fully awake.`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return response.json();
}

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function pollVideoJob(jobId) {
  const startedAt = Date.now();
  let lastStatus = "";
  while (Date.now() - startedAt < VIDEO_JOB_TIMEOUT_MS) {
    const job = await getJson(`/videos/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 15000 });
    if (job.message && job.message !== lastStatus) {
      uploadStatus.textContent = job.message;
      setSystemStatus(job.message, "processing");
      lastStatus = job.message;
    }
    if (job.status === "completed") return job.result || job;
    if (job.status === "failed") {
      throw new Error(job.error || job.message || "Video processing failed.");
    }
    await sleep(VIDEO_JOB_POLL_MS);
  }
  throw new Error("Processing is still running after 30 minutes. Try a smaller MP4 or restart the Space before retrying.");
}

function metric(label, value, status = "neutral", helper = "") {
  return `
    <div class="metric metric-${status}">
      <span>${label}</span>
      <strong>${value}</strong>
      ${helper ? `<small>${helper}</small>` : ""}
    </div>
  `;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function statusForPercent(value, goodAt = 0.65, warnAt = 0.35) {
  if (value >= goodAt) return "good";
  if (value >= warnAt) return "warn";
  return "risk";
}

function statusForQueue(queueDepth) {
  if (queueDepth >= 4) return "risk";
  if (queueDepth >= 2) return "warn";
  return "good";
}

function engagementScore(metrics) {
  const dwellValues = Object.values(metrics.average_dwell_ms_by_zone || {});
  const avgDwellSec = dwellValues.length ? dwellValues.reduce((sum, value) => sum + Number(value || 0), 0) / dwellValues.length / 1000 : 0;
  const score = Math.min(100, Math.round(avgDwellSec * 12 + Number(metrics.conversion_rate || 0) * 35));
  return score;
}

function queueRiskScore(metrics) {
  return Math.min(100, Math.round(Number(metrics.queue_depth || 0) * 25 + Number(metrics.abandonment_rate || 0) * 50));
}

function revenueOpportunity(metrics, funnel) {
  const visitors = Number(metrics.unique_visitors || 0);
  const checkout = Number(funnel?.checkout_visit ?? funnel?.billing_queue_join ?? 0);
  const missed = Math.max(visitors - checkout, 0);
  const dwellValues = Object.values(metrics.average_dwell_ms_by_zone || {}).map((value) => Number(value || 0));
  const avgDwellSec = dwellValues.length ? dwellValues.reduce((sum, value) => sum + value, 0) / dwellValues.length / 1000 : 0;
  const productSignals = Number(funnel?.product_interaction || 0) + Number(funnel?.visited_product_zone || funnel?.zone_enter || 0);
  const engagement = engagementScore(metrics);
  const queuePenalty = Math.min(180, Number(metrics.queue_depth || 0) * 28 + Number(metrics.abandonment_rate || 0) * 150);
  const basketEstimate = 240 + Math.min(620, engagement * 3.8 + avgDwellSec * 16 + productSignals * 34);
  return Math.round(missed * Math.max(120, basketEstimate - queuePenalty));
}

function money(value) {
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

function processedUploadMessage(result = {}) {
  const input = result.input || {};
  const events = Number(result.events_inserted ?? result.events_generated ?? 0);
  const duration = Number(input.original_duration_sec || input.duration_sec || 0);
  const analyzed = Number(input.analysis_duration_sec || input.duration_sec || duration);
  const chunks = Array.isArray(input.analysis_chunks) ? input.analysis_chunks.length : 0;
  const visitorCount = Number(result.metrics?.unique_visitors || 0);
  const eventText = events === 1 ? "1 retail event" : `${events} retail events`;
  const visitorText = visitorCount === 1 ? "1 visitor" : `${visitorCount} visitors`;
  const durationText = duration ? ` across ${duration}s of video` : "";
  const chunkText = chunks > 1 ? ` in ${chunks} processing chunks` : "";
  const analyzedText = duration && analyzed < duration ? `; analyzed first ${analyzed}s` : "";
  return `Processing complete: ${eventText} generated for ${visitorText}${durationText}${chunkText}${analyzedText}.`;
}

function setSystemStatus(text, state = "ready") {
  if (!systemStatus) return;
  systemStatus.textContent = "";
  const dot = document.createElement("span");
  systemStatus.append(dot, document.createTextNode(` ${text}`));
  systemStatus.dataset.state = state;
}

function csvEscape(value) {
  const text = value === undefined || value === null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function csvRow(values) {
  return values.map(csvEscape).join(",");
}

function buildInsightsCsv() {
  const rows = [
    ["section", "metric", "value", "detail"],
    ["Project", "System", "CogniLens", "Agentic CCTV store intelligence"],
    ["Project", "Store", storeId(), ""],
    ["Project", "Selected Timestamp", selectedTime.textContent || "", `Video second ${slider.value || 0}`],
  ];

  if (currentMetrics) {
    const engagement = engagementScore(currentMetrics);
    const queueRisk = queueRiskScore(currentMetrics);
    rows.push(["KPI", "Total Visitors", currentMetrics.unique_visitors ?? 0, "Staff excluded from customer metrics"]);
    rows.push(["KPI", "Conversion Rate", percent(currentMetrics.conversion_rate), "Visitors reaching checkout"]);
    rows.push(["KPI", "Customer Engagement Score", `${engagement}/100`, "Derived from observed engagement time"]);
    rows.push(["KPI", "Queue Risk Score", `${queueRisk}/100`, "Higher score means greater checkout risk"]);
    rows.push(["KPI", "Estimated Revenue Opportunity", money(revenueOpportunity(currentMetrics, currentFunnel)), "Dynamic estimate from missed checkout and engagement signals"]);
    rows.push(["KPI", "Store Health Score", `${currentScore?.total ? Math.round(currentScore.total) : Math.max(0, 100 - queueRisk)}/100`, "Rubric and operating readiness signal"]);
    for (const [zone, dwellMs] of Object.entries(currentMetrics.average_dwell_ms_by_zone || {})) {
      rows.push(["Area", zoneLabel(zone), `${Math.round(Number(dwellMs || 0) / 1000)} sec`, "Average customer engagement time"]);
    }
  }

  if (currentFunnel) {
    const steps = currentFunnel.flow || [
      { label: "Entered Store", count: currentFunnel.entry },
      { label: "Visited Product Zone", count: currentFunnel.zone_enter },
      { label: "Product Interaction", count: currentFunnel.product_interaction },
      { label: "Billing Counter", count: currentFunnel.billing_queue_join },
      { label: "Exit", count: currentFunnel.exit },
    ];
    for (const step of steps) rows.push(["Funnel", step.label, step.count ?? 0, "Session-based journey count"]);
    for (const item of currentFunnel.attention_scores || []) rows.push(["Attention", item.visitor, item.attention_score, "Purchase intent score"]);
  }

  if (currentTimelineData) {
    rows.push(["Timeline", "Summary", currentTimelineData.summary || "", currentTimelineData.timestamp || ""]);
    for (const event of currentTimelineData.display_events || []) {
      rows.push(["Activity", businessActivityHeadline(event), event.visitor || "", zoneLabel(event.zone)]);
    }
  }

  for (const item of currentAnomalies || []) {
    const proof = item.proof || {};
    const proofText = [
      proof.timestamp ? `timestamp ${proof.timestamp}` : "",
      proof.zone ? `area ${zoneLabel(proof.zone)}` : "",
      proof.measured_value !== undefined && proof.threshold !== undefined ? `observed ${proof.measured_value} ${proof.unit || ""}, expected ${proof.threshold}` : "",
    ].filter(Boolean).join("; ");
    rows.push(["AI Insight", humanizeType(item.anomaly_type), item.message, proofText]);
  }

  if (currentScore) {
    rows.push(["Rubric", "Total Score", `${Math.round(currentScore.total)}/100`, currentScore.label || "Self-evaluation based on rubric"]);
    rows.push(["Rubric", "Detection", `${Number(currentScore.detection || 0).toFixed(1)}/30`, ""]);
    rows.push(["Rubric", "API", `${Number(currentScore.api || 0).toFixed(1)}/35`, ""]);
    rows.push(["Rubric", "Production", `${Number(currentScore.production || 0).toFixed(1)}/20`, ""]);
    rows.push(["Rubric", "Thinking", `${Number(currentScore.thinking || 0).toFixed(1)}/15`, ""]);
  }

  return `${rows.map(csvRow).join("\n")}\n`;
}

function downloadInsightsCsv() {
  if (!hasProcessedInput) return;
  const csv = buildInsightsCsv();
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const safeStore = storeId().replace(/[^a-z0-9_-]+/gi, "_");
  link.href = url;
  link.download = `cognilens-insights-${safeStore}-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function setVideoPlaybackButton() {
  if (!toggleVideoPlayback) return;
  const hasSource = Boolean(videoPreview.currentSrc || videoPreview.src);
  toggleVideoPlayback.disabled = !hasSource;
  toggleVideoPlayback.textContent = videoPreview.paused ? "Play Preview" : "Pause Preview";
  if (toggleVideoFocus) toggleVideoFocus.disabled = !hasSource;
  if (saveReview) saveReview.disabled = !hasSource || !hasProcessedInput;
}

function setProcessingState(message) {
  hasProcessedInput = false;
  metricsEl.hidden = true;
  executiveSummary.hidden = true;
  workspaceEl.hidden = true;
  lowerEl.hidden = true;
  scorePanel.hidden = true;
  emptyState.hidden = false;
  emptyState.classList.add("processing");
  emptyState.querySelector("h2").textContent = "Processing CCTV Video";
  emptyState.querySelector("p").textContent = message;
  setSystemStatus(message, "working");
}

function setProcessedState() {
  emptyState.classList.remove("processing");
  emptyState.querySelector("h2").textContent = "No Video Processed";
  emptyState.querySelector("p").textContent = "Upload an MP4 or click Use Demo Video to run the agents and display verified analytics.";
}

function setProcessingFailedState(message) {
  hasProcessedInput = false;
  emptyState.classList.remove("processing");
  metricsEl.hidden = true;
  executiveSummary.hidden = true;
  workspaceEl.hidden = true;
  lowerEl.hidden = true;
  scorePanel.hidden = true;
  emptyState.hidden = false;
  emptyState.querySelector("h2").textContent = "Processing Could Not Complete";
  emptyState.querySelector("p").textContent = message;
  setSystemStatus("Processing stopped", "risk");
}

function setVideoFocus(enabled) {
  videoFocusEnabled = Boolean(enabled);
  workspaceEl.classList.toggle("video-focus", videoFocusEnabled);
  if (toggleVideoFocus) {
    toggleVideoFocus.textContent = videoFocusEnabled ? "Return To Insight Layout" : "Make Video Centre Focus";
  }
  renderOverlay();
}

function formatAttentionDetail(item) {
  const dwellSec = Math.round(Number(item.dwell_ms || 0) / 1000);
  const interactions = Number(item.product_interactions || 0);
  const shelfEngagement = Number(item.shelf_engagement || 0);
  return `${dwellSec}s product engagement · ${interactions} product signal${interactions === 1 ? "" : "s"} · ${shelfEngagement} shelf moment${shelfEngagement === 1 ? "" : "s"}`;
}

async function refreshSavedReviews() {
  if (!savedReviews || !loadReview) return;
  try {
    const data = await getJson(`/demo/reviews?store_id=${encodeURIComponent(storeId())}`);
    const reviews = data.reviews || [];
    savedReviews.innerHTML = reviews.length
      ? reviews
          .map((review) => {
            const label = `${review.title} · ${review.events} events · ${review.duration_sec}s`;
            return `<option value="${review.review_id}">${label}</option>`;
          })
          .join("")
      : '<option value="">No saved CCTV reviews</option>';
    savedReviews.disabled = reviews.length === 0;
    loadReview.disabled = reviews.length === 0;
  } catch {
    savedReviews.innerHTML = '<option value="">Saved reviews unavailable</option>';
    savedReviews.disabled = true;
    loadReview.disabled = true;
  }
}

async function saveCurrentReview() {
  if (!hasProcessedInput) return;
  saveReview.disabled = true;
  setSystemStatus("Saving analyzed CCTV", "working");
  try {
    const data = await getJson("/demo/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store_id: storeId() }),
    });
    uploadStatus.textContent = `${data.title} saved for later review with ${data.events} analyzed events.`;
    await refreshSavedReviews();
    if (savedReviews) savedReviews.value = data.review_id;
    setSystemStatus("Insights live", "live");
  } catch (error) {
    uploadStatus.textContent = error.message;
    setSystemStatus("Insights live", "live");
  } finally {
    setVideoPlaybackButton();
  }
}

async function loadSavedReview() {
  const reviewId = savedReviews?.value;
  if (!reviewId) return;
  showDashboard();
  setSystemStatus("Loading saved review", "working");
  summary.textContent = "Loading saved CCTV analytics without reprocessing video...";
  try {
    const data = await getJson(`/demo/reviews/${encodeURIComponent(reviewId)}/load`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store_id: storeId() }),
    });
    uploadStatus.textContent = `${data.title} loaded from saved analytics. No video reprocessing required.`;
    await refreshAll();
    await refreshSavedReviews();
    if (savedReviews) savedReviews.value = reviewId;
    setSystemStatus("Insights live", "live");
  } catch (error) {
    uploadStatus.textContent = error.message;
    resetDashboard();
  }
}

function humanizeType(value) {
  return String(value || "")
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function businessActivityHeadline(event) {
  const headline = String(event.headline || event.event_type || "");
  const zone = zoneLabel(event.zone || event.zone_id);
  const type = String(event.event_type || "").toUpperCase();
  if (type === "INSIGHT") return headline;
  if (type === "PRODUCT_INTERACTION") return `Product Interest in ${zone}`;
  if (["CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(type)) return "Reached Checkout";
  if (type === "ENTRY") return "Entered Store";
  if (type === "REENTRY") return "Returned To Store";
  if (type === "EXIT") return "Exited Store";
  if (type === "ZONE_ENTER") return `Moved Into ${zone}`;
  if (type === "ZONE_EXIT") return `Moved Out Of ${zone}`;
  if (type === "ZONE_DWELL") return `Shopping in ${zone}`;
  if (headline.startsWith("Currently in")) return `Shopping in ${zone}`;
  if (headline.includes("Product Interaction")) return `Product Interest in ${zone}`;
  if (headline.includes("Moved into")) return `Moved Into ${zone}`;
  if (headline.includes("Moved out")) return `Moved Out Of ${zone}`;
  if (headline.includes("entered")) return "Entered Store";
  if (headline.includes("exited")) return "Completed Visit";
  if (headline.includes("queue")) return "Reached Checkout";
  if (headline.includes("Dwelling")) return `Engaged in ${zone}`;
  return headline.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function parseMetadata(event) {
  if (!event || !event.metadata) return {};
  if (typeof event.metadata === "object") return event.metadata;
  try {
    return JSON.parse(event.metadata);
  } catch {
    return {};
  }
}

function visitorNumber(visitorId) {
  const match = String(visitorId || "").match(/(\d+)$/);
  return match ? match[1] : "1";
}

function roleNumber(event) {
  return visitorNumber(event.visitor_id);
}

function zoneLabel(zoneId) {
  const key = String(zoneId || "").toUpperCase().replaceAll(" ", "_");
  const labels = {
    ENTRY: "Entrance",
    EXIT: "Exit",
    AISLE_A: "Product Aisle",
    WALL_PRODUCTS: "Wall Products",
    PRODUCT_AISLE: "Product Aisle",
    CENTER_DISPLAY: "Center Display",
    PREMIUM: "Premium Section",
    BILLING: "Checkout",
    PMU: "PMU Service",
  };
  return labels[key] || String(zoneId || "Unknown").replaceAll("_", " ");
}

function retailMerchandiseLabel(zoneId) {
  const key = String(zoneId || "").toUpperCase();
  const labels = {
    WALL_PRODUCTS: "Face Products",
    PRODUCT_AISLE: "Face Products",
    CENTER_DISPLAY: "Lipstick Section",
    PREMIUM: "Premium Section",
    BILLING: "Checkout",
    PMU: "Staff Service Area",
  };
  return labels[key] || zoneLabel(zoneId);
}

function effectiveConfidence(event) {
  const metadata = parseMetadata(event);
  let confidence = Math.max(Number(event.confidence || 0), Number(metadata.role_confidence || 0));
  if (event.event_type === "PRODUCT_INTERACTION" && metadata.evidence?.rule) confidence = Math.max(confidence, 0.86);
  if (event.event_type === "ZONE_DWELL" && Number(event.dwell_ms || 0) >= 10000) confidence = Math.max(confidence, 0.84);
  if (["CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(event.event_type)) confidence = Math.max(confidence, 0.86);
  return confidence;
}

function eventSecond(event) {
  if (Number.isFinite(Number(event.video_time_sec))) return Math.round(Number(event.video_time_sec));
  if (!timelineStart || !event.timestamp) return clampTimelineSecond(slider.value);
  return Math.max(0, Math.round((new Date(event.timestamp).getTime() - timelineStart.getTime()) / 1000));
}

function insightColor(kind) {
  return {
    product: overlayColors.product,
    checkout: overlayColors.checkout,
    staff: overlayColors.employee,
    employee: overlayColors.employee,
    exit: overlayColors.exit,
    intent: overlayColors.returning,
  }[kind] || overlayColors.customer;
}

function stableHash(value) {
  return String(value || "track").split("").reduce((hash, char) => {
    return ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  }, 0);
}

function personOutlineColor(event) {
  const customerPalette = ["#147cff", "#00a7b5", "#6d5dfc", "#10b981", "#2dd4bf", "#4f46e5", "#0891b2", "#2563eb"];
  const employeePalette = ["#ff8a00", "#f97316", "#d97706", "#fb923c", "#ea580c", "#f59e0b"];
  const isStaff = Boolean(event.is_staff || event.role === "staff" || event._insightKind === "staff" || event._insightKind === "employee");
  const palette = isStaff ? employeePalette : customerPalette;
  const key = event.visitor_id || event.track_id || event.event_id || event._insightText;
  return palette[Math.abs(stableHash(key)) % palette.length];
}

function bboxCenter(bbox) {
  if (!Array.isArray(bbox)) return null;
  const [x, y, w, h] = bbox.map(Number);
  if (![x, y, w, h].every(Number.isFinite)) return null;
  return { x: x + w / 2, y: y + h / 2, w, h };
}

function normalizedDistance(first, second) {
  const width = currentVideoSourceSize?.width || videoPreview.videoWidth || 1920;
  const height = currentVideoSourceSize?.height || videoPreview.videoHeight || 1080;
  return Math.hypot((first.x - second.x) / width, (first.y - second.y) / height);
}

function hasNearbyCustomerSignal(staffEvent, allEvents) {
  const staffCenter = bboxCenter(parseMetadata(staffEvent).bbox);
  if (!staffCenter) return false;
  return (allEvents || []).some((event) => {
    if (event.visitor_id === staffEvent.visitor_id) return false;
    if (event.is_staff || event.role === "staff") return false;
    if (effectiveConfidence(event) <= 0.72) return false;
    const customerCenter = bboxCenter(parseMetadata(event).bbox);
    if (!customerCenter) return false;
    return normalizedDistance(staffCenter, customerCenter) <= 0.22;
  });
}

function insightForEvent(event, allEvents = []) {
  if (effectiveConfidence(event) <= 0.8) return null;
  const metadata = parseMetadata(event);
  const zone = event.zone_id || event.zone;
  const merchandise = retailMerchandiseLabel(zone);
  const type = String(event.event_type || "").toUpperCase();
  const isStaff = Boolean(event.is_staff || event.role === "staff");
  const nearbyCustomer = isStaff ? hasNearbyCustomerSignal(event, allEvents) : false;
  const dwellSec = Math.max(
    Math.round(Number(event.dwell_ms || 0) / 1000),
    Math.round(Number(metadata.evidence?.zone_streak_sec || 0))
  );
  const productSignals = allEvents.filter(
    (candidate) => candidate.visitor_id === event.visitor_id && candidate.event_type === "PRODUCT_INTERACTION"
  ).length;

  if (isStaff && nearbyCustomer && ["ZONE_DWELL", "ZONE_ENTER", "CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(type)) {
    return { text: "Staff Interaction Started", kind: "staff", event };
  }
  if (type === "PRODUCT_INTERACTION") {
    if (productSignals >= 3 || dwellSec >= 8) return { text: `Compared ${Math.max(3, productSignals + 1)} Lipstick Variants`, kind: "product", event };
    if (dwellSec >= 5) return { text: `Browsing ${merchandise} (${dwellSec}s)`, kind: "product", event };
    return { text: `Shelf Engagement: ${merchandise}`, kind: "product", event };
  }
  if (type === "ZONE_DWELL" && !isStaff) {
    if (dwellSec >= 10) return { text: `Browsing ${merchandise} (${dwellSec}s)`, kind: "product", event };
    if (["CENTER_DISPLAY", "WALL_PRODUCTS", "PRODUCT_AISLE"].includes(String(zone))) {
      return { text: `Engaged with ${merchandise}`, kind: "product", event };
    }
  }
  if (["CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(type)) {
    return { text: type === "BILLING_QUEUE_JOIN" ? "Queue Waiting" : "Moved to Checkout", kind: "checkout", event };
  }
  if (type === "ZONE_ENTER" && String(zone) === "BILLING") {
    return { text: "Checkout Intent", kind: "checkout", event };
  }
  if (type === "ZONE_ENTER" && ["CENTER_DISPLAY", "WALL_PRODUCTS", "PRODUCT_AISLE"].includes(String(zone))) {
    return { text: `Browsing ${merchandise}`, kind: "product", event };
  }
  if (type === "EXIT") {
    if (metadata.source === "tracker_absence") return null;
    const hadCheckoutSignal = allEvents.some(
      (candidate) => candidate.visitor_id === event.visitor_id && ["CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(candidate.event_type)
    );
    return { text: hadCheckoutSignal ? "Purchase Completed" : "Exited Store", kind: "exit", event };
  }
  if (type === "REENTRY") return { text: "Returning Visitor Detected", kind: "intent", event };
  return null;
}

function observationInsightForEvent(event) {
  const metadata = parseMetadata(event);
  if (!isUsefulOverlayBBox(metadata.bbox)) return null;
  if (String(event.event_type || "").toUpperCase() === "EXIT" && metadata.source === "tracker_absence") return null;
  const zone = event.zone_id || event.zone;
  const zoneName = retailMerchandiseLabel(zone);
  const type = String(event.event_type || "").toUpperCase();
  const isStaff = Boolean(event.is_staff || event.role === "staff");
  if (isStaff) {
    const staffText = zone === "BILLING" || zone === "PMU"
      ? `Employee Monitoring ${zoneLabel(zone)}`
      : `Employee Available Near ${zoneName}`;
    return { text: staffText, kind: "staff", event };
  }
  if (["WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY", "PREMIUM"].includes(String(zone))) {
    const text = type === "PRODUCT_INTERACTION"
      ? `Shelf Engagement: ${zoneName}`
      : `Customer Observed in ${zoneName}`;
    return { text, kind: "product", event };
  }
  if (zone === "BILLING") return { text: "Customer Near Checkout", kind: "checkout", event };
  if (["ENTRY", "EXIT"].includes(String(zone))) return { text: "Customer Near Entrance", kind: "customer", event };
  return { text: `Customer Activity in ${zoneLabel(zone)}`, kind: "customer", event };
}

function impactObservationLabel(event) {
  const insight = insightForEvent(event, [event]) || observationInsightForEvent(event);
  if (insight) return { text: insight.text, color: insightColor(insight.kind), kind: insight.kind };
  const zone = String(event.zone_id || event.zone || "").toUpperCase();
  const isStaff = Boolean(event.is_staff || event.role === "staff");
  if (isStaff) return { text: `Employee Available: ${zoneLabel(zone)}`, color: overlayColors.employee, kind: "employee" };
  if (["WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY", "PREMIUM"].includes(zone)) {
    return { text: `Browsing ${retailMerchandiseLabel(zone)}`, color: overlayColors.product, kind: "product" };
  }
  if (zone === "BILLING") return { text: "Customer Near Checkout", color: overlayColors.checkout, kind: "checkout" };
  if (["ENTRY", "EXIT"].includes(zone)) return { text: "Customer Movement Near Entrance", color: overlayColors.customer, kind: "customer" };
  return { text: "Customer Movement Observed", color: overlayColors.customer, kind: "customer" };
}

function fallbackObservation(events) {
  const candidates = (events || [])
    .map((event) => {
      const insight = observationInsightForEvent(event);
      if (!insight) return null;
      const type = String(event.event_type || "").toUpperCase();
      const zone = String(event.zone_id || event.zone || "");
      const priority = {
        PRODUCT_INTERACTION: 8,
        CHECKOUT_VISIT: 7,
        BILLING_QUEUE_JOIN: 7,
        ZONE_ENTER: 5,
        ZONE_DWELL: 4,
        ENTRY: 3,
      }[type] || 2;
      const zoneBoost = ["WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY", "BILLING", "PMU"].includes(zone) ? 2 : 0;
      const roleBoost = event.is_staff || event.role === "staff" ? 1 : 0;
      return { ...insight, priority: priority + zoneBoost + roleBoost };
    })
    .filter(Boolean)
    .sort((a, b) => b.priority - a.priority);
  return candidates[0] || null;
}

function sceneObservationForSecond(data = currentTimelineData) {
  if (!data) return null;
  const summaryText = String(data.summary || "").trim();
  if (/staff member/i.test(summaryText)) return "Store Staff Visible";
  if (/customer visible/i.test(summaryText) || /active customers/i.test(summaryText)) return summaryText;
  return "Store Area Quiet: No Active Customer Movement";
}

function shouldShowInsight(insight) {
  const second = eventSecond(insight.event);
  const key = `${insight.event.visitor_id}:${insight.text}`;
  const previousSecond = insightRepeatMemory.get(key);
  if (previousSecond !== undefined && previousSecond !== second && Math.abs(second - previousSecond) < 10) return false;
  insightRepeatMemory.set(key, second);
  return true;
}

function retailInsights(events, { rateLimit = false } = {}) {
  const byVisitor = new Map();
  for (const event of events || []) {
    const metadata = parseMetadata(event);
    if (!isUsefulOverlayBBox(metadata.bbox)) continue;
    const insight = insightForEvent(event, events);
    if (!insight) continue;
    if (rateLimit && !shouldShowInsight(insight)) continue;
    const priority = { checkout: 6, product: 5, intent: 4, staff: 3, exit: 2 }[insight.kind] || 1;
    const visitorInsights = byVisitor.get(event.visitor_id) || [];
    if (!visitorInsights.some((item) => item.text === insight.text)) {
      visitorInsights.push({ ...insight, priority, metadata });
    }
    byVisitor.set(event.visitor_id, visitorInsights);
  }
  return [...byVisitor.values()]
    .flatMap((items) => items.sort((a, b) => b.priority - a.priority).slice(0, 2))
    .sort((a, b) => b.priority - a.priority)
    .slice(0, 8);
}

function eventBusinessLabel(event, groupSize = 0) {
  if (event._insightText) return { text: event._insightText, color: insightColor(event._insightKind), kind: event._insightKind };
  return impactObservationLabel(event);
  const zone = zoneLabel(event.zone_id || event.zone);
  if (event.event_type === "ENTRY") {
    return { text: `Entry • ${event.is_staff || event.role === "staff" ? `Employee #${roleNumber(event)}` : `Customer #${roleNumber(event)}`}`, color: overlayColors.customer, kind: event.is_staff || event.role === "staff" ? "employee" : "customer" };
  }
  if (event.event_type === "ZONE_ENTER" && !(event.is_staff || event.role === "staff")) {
    return { text: `Customer #${roleNumber(event)} entered ${zone}`, color: overlayColors.customer, kind: "customer" };
  }
  if (event.event_type === "PRODUCT_INTERACTION") {
    return { text: `Product Interest • Customer #${roleNumber(event)}`, color: overlayColors.product, kind: "product" };
  }
  if (["CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(event.event_type)) {
    return { text: `Billing • Customer #${roleNumber(event)}`, color: overlayColors.checkout, kind: "checkout" };
  }
  if (event.event_type === "EXIT") {
    return { text: `Exit • ${event.is_staff || event.role === "staff" ? `Employee #${roleNumber(event)}` : `Customer #${roleNumber(event)}`}`, color: overlayColors.exit, kind: "exit" };
  }
  if (event.event_type === "REENTRY") return { text: `Returning Visitor • Customer #${roleNumber(event)}`, color: overlayColors.returning, kind: "returning" };
  if (event.is_staff || event.role === "staff") return { text: `Employee #${roleNumber(event)} • ${zone}`, color: overlayColors.employee, kind: "employee" };
  return { text: `Customer #${roleNumber(event)} • ${zone}`, color: overlayColors.customer, kind: "customer" };
}

function shouldShowOverlayEvent(event, label) {
  const isStaff = Boolean(event.is_staff || event.role === "staff" || label.kind === "employee" || label.kind === "staff");
  if (isStaff) return overlayOptions.employees;
  if (["product", "checkout"].includes(label.kind)) return overlayOptions.productEvents || overlayOptions.customers;
  return overlayOptions.customers;
}

function currentOverlayEvents() {
  const exactEvents = currentTimelineData?.events || [];
  const stateEvents = currentTimelineData?.active_events || [];
  const importantTypes = new Set(["ENTRY", "EXIT", "REENTRY", "ZONE_ENTER", "ZONE_EXIT", "PRODUCT_INTERACTION", "CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"]);
  const actionEvents = exactEvents.filter((event) => importantTypes.has(event.event_type));
  const events = [...actionEvents, ...stateEvents];
  const byVisitor = new Map();
  for (const event of [...stateEvents, ...actionEvents]) {
    const metadata = parseMetadata(event);
    if (!isUsefulOverlayBBox(metadata.bbox)) continue;
    const key = event.visitor_id || event.track_id || event.event_id;
    if (!key) continue;
    const type = String(event.event_type || "").toUpperCase();
    const priority = {
      PRODUCT_INTERACTION: 8,
      CHECKOUT_VISIT: 7,
      BILLING_QUEUE_JOIN: 7,
      REENTRY: 6,
      ZONE_ENTER: 5,
      ZONE_DWELL: 4,
      ENTRY: 3,
    }[type] || 2;
    const existing = byVisitor.get(key);
    if (!existing || priority >= existing._overlayPriority) {
      byVisitor.set(key, { ...event, _overlayPriority: priority });
    }
  }
  for (const insight of retailInsights(events, { rateLimit: true })) {
    const key = insight.event.visitor_id || insight.event.track_id || insight.event.event_id;
    if (!key) continue;
    const base = byVisitor.get(key) || insight.event;
    byVisitor.set(key, {
      ...base,
      ...insight.event,
      _insightText: insight.text,
      _insightKind: insight.kind,
      _overlayPriority: 99,
    });
  }
  const visibleEvents = [...byVisitor.values()].map((event) => {
    if (event._insightText) return event;
    const observation = observationInsightForEvent(event);
    if (!observation) return event;
    return { ...event, _insightText: observation.text, _insightKind: observation.kind };
  });
  if (visibleEvents.length) {
    return visibleEvents.sort((a, b) => (b._overlayPriority || 0) - (a._overlayPriority || 0)).slice(0, 12);
  }
  const observation = fallbackObservation(events);
  if (!observation) return [];
  return [{ ...observation.event, _insightText: observation.text, _insightKind: observation.kind }];
}

function groupSizes(events) {
  const members = {};
  for (const event of events) {
    if (!event.group_id) continue;
    members[event.group_id] ||= new Set();
    members[event.group_id].add(event.visitor_id);
  }
  return Object.fromEntries(Object.entries(members).map(([groupId, visitors]) => [groupId, visitors.size]));
}

function isUsefulOverlayBBox(bbox) {
  if (!Array.isArray(bbox) || bbox.length < 4) return false;
  const [x, y, w, h] = bbox.map(Number);
  if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return false;
  const aspect = h / Math.max(w, 1);
  const inferredWidth = Math.max(currentVideoSourceSize?.width || 0, x + w, 320);
  const inferredHeight = Math.max(currentVideoSourceSize?.height || 0, y + h, 180);
  const areaRatio = (w * h) / Math.max(inferredWidth * inferredHeight, 1);
  return aspect >= 1.05 && aspect <= 5.8 && areaRatio >= 0.004 && areaRatio <= 0.5;
}

function sourceDimensions(events) {
  let width = videoPreview.videoWidth || currentVideoSourceSize?.width || 0;
  let height = videoPreview.videoHeight || currentVideoSourceSize?.height || 0;
  for (const event of events) {
    const bbox = parseMetadata(event).bbox;
    if (Array.isArray(bbox)) {
      width = Math.max(width, bbox[0] + bbox[2]);
      height = Math.max(height, bbox[1] + bbox[3]);
    }
  }
  return { width: width || 960, height: height || 540 };
}

function canvasPointFromBBox(bbox, source, canvasRect) {
  const scaleX = canvasRect.width / source.width;
  const scaleY = canvasRect.height / source.height;
  return {
    x: bbox[0] * scaleX,
    y: bbox[1] * scaleY,
    w: bbox[2] * scaleX,
    h: bbox[3] * scaleY,
  };
}

function resizeOverlayCanvas() {
  const rect = videoStage.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  overlayCanvas.width = Math.max(1, Math.round(rect.width * ratio));
  overlayCanvas.height = Math.max(1, Math.round(rect.height * ratio));
  overlayCanvas.style.width = `${rect.width}px`;
  overlayCanvas.style.height = `${rect.height}px`;
  overlayCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width: rect.width, height: rect.height };
}

function renderExecutiveSummary() {
  if (!currentMetrics || !currentFunnel) return;
  const engagement = engagementScore(currentMetrics);
  const queueRisk = queueRiskScore(currentMetrics);
  const opportunity = revenueOpportunity(currentMetrics, currentFunnel);
  const conversion = Number(currentMetrics.conversion_rate || 0);
  const alerts = currentAnomalies.length;
  const visitors = Number(currentMetrics.unique_visitors || 0);
  const topArea = Object.entries(currentMetrics.average_dwell_ms_by_zone || {})
    .sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0];
  const overview = visitors
    ? `${visitors} total visitors with ${percent(conversion)} conversion. Store health is ${Math.round(currentScore?.total || Math.max(0, 100 - queueRisk))}/100.`
    : "No processed visitor activity is available yet.";
  const behavior = topArea
    ? `Customers are spending the most engagement time around ${zoneLabel(topArea)}. Engagement score is ${engagement}/100.`
    : `Customer engagement score is ${engagement}/100 based on observed area activity.`;
  const bottleneck = queueRisk >= 60
    ? "Checkout pressure is high. Add staff or open another billing point."
    : queueRisk >= 30
      ? "Checkout risk is moderate. Watch the queue during peak moments."
      : "No major checkout bottleneck detected.";
  const revenue = opportunity
    ? `${money(opportunity)} estimated opportunity from visitors who did not reach checkout.`
    : "No immediate missed-revenue opportunity detected in this clip.";
  const recommendation = alerts
    ? `${alerts} AI insight${alerts === 1 ? "" : "s"} need review. Prioritize staff response in the highlighted area.`
    : conversion < 0.4
      ? "Improve product-area assistance and guide interested shoppers toward checkout."
      : "Maintain current flow and keep monitoring engagement around high-interest areas.";
  summaryCards.innerHTML = [
    ["Store performance overview", overview, statusForPercent(conversion)],
    ["Customer behavior summary", behavior, statusForPercent(engagement / 100)],
    ["Bottlenecks detected", bottleneck, statusForQueue(currentMetrics.queue_depth)],
    ["Revenue opportunities", revenue, opportunity ? "warn" : "good"],
    ["Key recommendations", recommendation, alerts ? "warn" : "good"],
  ]
    .map(([title, text, status]) => `<article class="summary-card summary-${status}"><strong>${title}</strong><p>${text}</p></article>`)
    .join("");
}

async function refreshMetrics() {
  const data = await getJson(`/stores/${storeId()}/metrics`);
  currentMetrics = data;
  const engagement = engagementScore(data);
  const queueRisk = queueRiskScore(data);
  const storeHealth = currentScore?.total ? Math.round(currentScore.total) : Math.max(0, 100 - queueRisk);
  const opportunity = revenueOpportunity(data, currentFunnel);
  metricsEl.innerHTML = [
    metric("Total Visitors", data.unique_visitors, data.unique_visitors ? "good" : "neutral", "Customer traffic"),
    metric("Conversion Rate", percent(data.conversion_rate), statusForPercent(data.conversion_rate), "Reached checkout"),
    metric("Customer Engagement Score", `${engagement}/100`, statusForPercent(engagement / 100), "Time spent in key areas"),
    metric("Queue Risk Score", `${queueRisk}/100`, statusForQueue(data.queue_depth), "Lower is better"),
    metric("Estimated Revenue Opportunity", money(opportunity), opportunity ? "warn" : "good", "From unconverted visitors"),
    metric("Store Health Score", `${storeHealth}/100`, statusForPercent(storeHealth / 100), "Overall operating signal"),
  ].join("");
  renderExecutiveSummary();
}

async function refreshAgentScore() {
  const data = await getJson(`/score?store_id=${encodeURIComponent(storeId())}`);
  currentScore = data;
  const evidence = data.evidence || {};
  scoreTotal.textContent = `${Math.round(data.total)} / 100`;
  scoreLabel.textContent = data.label || "Self-Evaluation Based on Rubric";
  scoreBreakdown.innerHTML = [
    ["Detection", data.detection, 30],
    ["API", data.api, 35],
    ["Production", data.production, 20],
    ["Thinking", data.thinking, 15],
  ]
    .map(([label, value, max]) => `<div class="score-item"><strong>${Number(value).toFixed(1)} / ${max}</strong><span>${label}</span></div>`)
    .join("");
  scoreEvidence.innerHTML = [
    ["Events generated", evidence.events_generated ?? 0],
    ["Unique visitors", evidence.unique_visitors ?? 0],
    ["Reentries handled", evidence.reentries_handled ?? 0],
    ["Staff excluded", evidence.staff_excluded ?? 0],
    ["Groups detected", evidence.groups_detected ?? 0],
    ["APIs passing", `${evidence.apis_passing ?? 0}/${evidence.apis_total ?? 0}`],
    ["Docs present", evidence.docs_present ? "yes" : "no"],
  ]
    .map(([label, value]) => `<div class="evidence-item"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  renderExecutiveSummary();
}

function clampTimelineSecond(value) {
  const max = Number(slider.max) || 0;
  const second = Number(value);
  if (!Number.isFinite(second)) return 0;
  return Math.min(max, Math.max(0, Math.round(second)));
}

function timestampForSecond(second) {
  return new Date(timelineStart.getTime() + second * 1000);
}

function syncPreviewFrameToSlider() {
  if (!currentVideoFrameUrl || !currentVideoCacheKey) return;
  const second = clampTimelineSecond(slider.value);
  videoPreview.poster = `${currentVideoFrameUrl}?second=${second}&v=${currentVideoCacheKey}`;
}

function zoneActivityFromEvents(events) {
  const counts = {};
  for (const event of events || []) {
    const zone = event.zone_id || event.zone;
    if (!zone || ["ZONE_EXIT", "EXIT", "BILLING_QUEUE_ABANDON"].includes(event.event_type)) continue;
    counts[zone] = (counts[zone] || 0) + 1;
  }
  return counts;
}

function timelineRowsForSecond(data) {
  const insightRows = retailInsights(data.events || []).map((insight) => ({
    headline: insight.text,
    visitor: insight.kind === "staff" ? "Employee" : "Customer",
    zone: insight.event.zone_id || insight.event.zone,
    zone_id: insight.event.zone_id,
    event_type: "INSIGHT",
  }));
  if (insightRows.length) return insightRows;
  const observation = fallbackObservation([...(data.events || []), ...(data.active_events || [])]);
  if (observation) {
    return [{
      headline: observation.text,
      visitor: observation.kind === "staff" ? "Employee" : "Customer",
      zone: observation.event.zone_id || observation.event.zone,
      zone_id: observation.event.zone_id,
      event_type: "OBSERVATION",
    }];
  }
  return [{
    headline: sceneObservationForSecond(data),
    visitor: "Store view",
    zone: "Current frame",
    event_type: "SCENE_OBSERVATION",
  }];
}

async function refreshTimeline({ syncVideo = false, force = false } = {}) {
  if (!timelineStart) {
    summary.textContent = "No processed events yet. Upload an MP4 or run the demo.";
    selectedTime.textContent = "--";
    document.querySelector("#events").innerHTML = "";
    renderTimestampHeatmap({});
    currentTimelineData = null;
    renderOverlay();
    return;
  }
  const second = clampTimelineSecond(slider.value);
  slider.value = second;
  if (!force && second === lastRenderedSecond) {
    if (syncVideo) syncVideoToSlider();
    return;
  }
  lastRenderedSecond = second;
  const requestId = ++timelineRequestId;
  const timestamp = timestampForSecond(second);
  selectedTime.textContent = fmt(timestamp);
  syncPreviewFrameToSlider();
  const data = await getJson(`/stores/${storeId()}/timeline?timestamp=${encodeURIComponent(fmt(timestamp))}`);
  if (requestId !== timelineRequestId) return;
  currentTimelineData = data;
  summary.textContent = data.summary;
  const instantActivity = zoneActivityFromEvents(data.events || []);
  renderTimestampHeatmap(Object.keys(instantActivity).length ? instantActivity : data.zone_activity, data.events || []);
  if (syncVideo) syncVideoToSlider();
  renderOverlay();
  const displayEvents = timelineRowsForSecond(data);
  document.querySelector("#events").innerHTML = displayEvents.length
    ? displayEvents
        .map(
          (event) => `
        <div class="event-row">
          <strong>${businessActivityHeadline(event)}</strong>
          <span>${event.visitor}</span>
          <span>${zoneLabel(event.zone || event.zone_id)}</span>
        </div>
      `
        )
        .join("")
    : '<div class="event-row muted-row"><strong>No high-confidence retail insight at this second</strong><span>Video remains unobstructed</span><span></span></div>';
  updateDebugDetails();
}

async function refreshTimelineRange() {
  const data = await getJson(`/stores/${storeId()}/timeline/range`);
  if (!data.start_timestamp) {
    timelineStart = null;
    slider.min = 0;
    slider.max = 0;
    slider.value = 0;
    selectedTime.textContent = "--";
    return data;
  }
  timelineStart = new Date(data.start_timestamp);
  slider.min = 0;
  slider.max = Math.max(data.duration_sec, 0);
  slider.value = 0;
  lastRenderedSecond = null;
  lastVideoTimelineSecond = null;
  selectedTime.textContent = data.start_timestamp;
  return data;
}

async function refreshHeatmap() {
  const data = await getJson(`/stores/${storeId()}/heatmap`);
  const zones = ["ENTRY", "WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY", "BILLING", "PMU"];
  const maxValue = Math.max(...zones.map((zone) => data.zones[zone] || data.activity[zone] || 1));
  document.querySelector("#heatmap").innerHTML = zones
    .map((zone) => {
      const value = data.zones[zone] || data.activity[zone] || 0;
      const alpha = 0.35 + 0.55 * (value / maxValue);
      const color = zone === "ENTRY" ? "61,139,123" : ["BILLING", "PMU"].includes(zone) ? "182,95,78" : "91,99,164";
      return `<div class="zone" style="background: rgba(${color}, ${alpha})"><strong>${zoneLabel(zone)}</strong><small>${value} ms/activity</small></div>`;
    })
    .join("");
}

function renderTimestampHeatmap(zoneActivity, events = []) {
  const zones = ["ENTRY", "WALL_PRODUCTS", "PRODUCT_AISLE", "CENTER_DISPLAY", "BILLING", "PMU"];
  const actionCounts = zoneActivityFromEvents(events.filter((event) => event.event_type !== "ZONE_DWELL"));
  const maxValue = Math.max(...zones.map((zone) => zoneActivity[zone] || 0), 1);
  document.querySelector("#heatmap").innerHTML = zones
    .map((zone) => {
      const value = zoneActivity[zone] || 0;
      const actions = actionCounts[zone] || 0;
      const alpha = value ? 0.3 + 0.6 * (value / maxValue) : 0.16;
      const color = zone === "ENTRY" ? "61,139,123" : ["BILLING", "PMU"].includes(zone) ? "182,95,78" : "91,99,164";
      const label = zoneLabel(zone);
      const noun = value === 1 ? "person" : "people";
      const actionText = actions ? ` · ${actions} action${actions === 1 ? "" : "s"}` : "";
      return `<div class="zone" style="background: rgba(${color}, ${alpha}); outline:${actions ? "2px solid rgba(16,24,32,0.28)" : "none"}"><strong>${label}</strong><small>${value} ${noun} now${actionText}</small></div>`;
    })
    .join("");
}

async function refreshVideoPreview() {
  try {
    const data = await getJson(`/stores/${storeId()}/video/current`);
    const cacheKey = encodeURIComponent(data.cache_key || data.updated_at || Date.now());
    currentVideoFrameUrl = data.frame_url || data.poster_url;
    currentVideoCacheKey = cacheKey;
    currentVideoSourceSize = { width: data.width || 960, height: data.height || 540 };
    videoPreview.pause();
    videoPreview.removeAttribute("src");
    videoPreview.removeAttribute("poster");
    videoPreview.load();
    syncPreviewFrameToSlider();
    videoPreview.src = `${data.video_url}?v=${cacheKey}`;
    videoMeta.textContent = `${data.duration_sec}s customer-flow preview`;
    debugMeta.innerHTML = `
      <div><strong>Camera ID</strong><span>${data.camera_id}</span></div>
      <div><strong>Video Size</strong><span>${data.width || "?"} × ${data.height || "?"}</span></div>
      <div><strong>FPS</strong><span>${data.fps}</span></div>
      <div><strong>Updated At</strong><span>${data.updated_at}</span></div>
    `;
    videoPreview.load();
    videoPreview.onloadedmetadata = () => {
      const duration = Number.isFinite(videoPreview.duration) ? Math.floor(videoPreview.duration) : data.duration_sec;
      slider.max = Math.max(Number(slider.max), duration);
      syncVideoToSlider();
      setVideoPlaybackButton();
    };
    setVideoPlaybackButton();
  } catch {
    videoPreview.removeAttribute("src");
    videoPreview.removeAttribute("poster");
    videoPreview.onloadedmetadata = null;
    currentVideoFrameUrl = null;
    currentVideoCacheKey = null;
    currentVideoSourceSize = null;
    videoMeta.textContent = "No video loaded";
    debugMeta.textContent = "No internal metadata available.";
    setVideoPlaybackButton();
  }
}

function updateDebugDetails() {
  if (!currentTimelineData) return;
  const rawEvents = currentTimelineData.events || [];
  const eventLines = rawEvents.slice(0, 8).map((event) => {
    const metadata = parseMetadata(event);
    return `
      <div>
        <strong>${event.event_id}</strong>
        <span>${event.camera_id} · ${event.event_type} · ${event.visitor_id} · ${Math.round(Number(event.confidence || 0) * 100)}% confidence${metadata.bbox ? ` · bbox ${metadata.bbox.join(",")}` : ""}</span>
      </div>
    `;
  });
  debugMeta.innerHTML = `
    <div><strong>Timestamp</strong><span>${currentTimelineData.timestamp}</span></div>
    <div><strong>Raw Events At This Time</strong><span>${rawEvents.length}</span></div>
    ${eventLines.join("")}
  `;
}

function syncVideoToSlider() {
  if (!videoPreview.src) return;
  const desiredTime = clampTimelineSecond(slider.value);
  if (Number.isFinite(desiredTime) && Math.abs(videoPreview.currentTime - desiredTime) > 0.35) {
    videoPreview.currentTime = desiredTime;
  }
  syncPreviewFrameToSlider();
}

function syncSliderToVideo({ refresh = true } = {}) {
  if (!timelineStart || !videoPreview.src) return;
  const second = clampTimelineSecond(videoPreview.currentTime);
  const secondChanged = second !== lastVideoTimelineSecond || String(second) !== slider.value;
  if (String(second) !== slider.value) {
    slider.value = second;
    selectedTime.textContent = fmt(timestampForSecond(second));
    syncPreviewFrameToSlider();
  }
  if (!refresh) return;
  if (!secondChanged) return;
  lastVideoTimelineSecond = second;
  lastRenderedSecond = null;
  window.clearTimeout(videoDrivenRefresh);
  videoDrivenRefresh = window.setTimeout(() => {
    refreshTimeline({ syncVideo: false, force: true }).catch((error) => {
      summary.textContent = error.message;
    });
  }, 80);
}

function drawZones(rect) {
  const zones = [
    { name: "Entrance / Exit", color: "rgba(20,124,255,0.5)", points: [[0.01, 0.34], [0.18, 0.34], [0.18, 0.96], [0.01, 0.96]] },
    { name: "Wall Products", color: "rgba(0,184,74,0.48)", points: [[0.1, 0.02], [0.88, 0.02], [0.88, 0.22], [0.1, 0.22]] },
    { name: "Product Aisle", color: "rgba(0,184,74,0.48)", points: [[0.13, 0.72], [0.86, 0.72], [0.86, 0.98], [0.13, 0.98]] },
    { name: "Center Display", color: "rgba(0,184,74,0.48)", points: [[0.32, 0.34], [0.66, 0.34], [0.66, 0.72], [0.32, 0.72]] },
    { name: "Cash Counter", color: "rgba(255,138,0,0.56)", points: [[0.82, 0.22], [0.96, 0.22], [0.96, 0.78], [0.82, 0.78]] },
    { name: "PMU Service", color: "rgba(255,138,0,0.56)", points: [[0.9, 0.58], [0.99, 0.58], [0.99, 0.9], [0.9, 0.9]] },
    {
      name: "Mirror / Reflection Area",
      color: "rgba(96,165,250,0.72)",
      fill: "rgba(96,165,250,0.055)",
      dashed: true,
      labelPoint: [0.136, 0.39],
      points: [[0.132, 0.367], [0.183, 0.355], [0.215, 0.385], [0.217, 0.694], [0.138, 0.704], [0.129, 0.404]],
    },
    {
      name: "Mirror / Reflection Area",
      color: "rgba(96,165,250,0.72)",
      fill: "rgba(96,165,250,0.052)",
      dashed: true,
      labelPoint: [0.63, 0.06],
      points: [[0.612, 0.0], [0.985, 0.0], [0.985, 0.985], [0.71, 0.985], [0.66, 0.82], [0.622, 0.56]],
    },
  ];
  overlayCtx.save();
  overlayCtx.font = "11px Inter, system-ui, sans-serif";
  overlayCtx.lineWidth = 1;
  for (const zone of zones) {
    overlayCtx.beginPath();
    zone.points.forEach(([px, py], index) => {
      const x = px * rect.width;
      const y = py * rect.height;
      if (index === 0) overlayCtx.moveTo(x, y);
      else overlayCtx.lineTo(x, y);
    });
    overlayCtx.closePath();
    overlayCtx.fillStyle = zone.fill || "rgba(255, 255, 255, 0.035)";
    overlayCtx.strokeStyle = zone.color || overlayColors.zone;
    overlayCtx.setLineDash(zone.dashed ? [5, 4] : []);
    overlayCtx.fill();
    overlayCtx.stroke();
    overlayCtx.setLineDash([]);
    overlayCtx.fillStyle = "rgba(255, 255, 255, 0.76)";
    const labelPoint = zone.labelPoint || zone.points[0];
    overlayCtx.fillText(zone.name, labelPoint[0] * rect.width + 8, labelPoint[1] * rect.height + 18);
  }
  overlayCtx.restore();
}

function drawHeatmap(rect) {
  if (!overlayOptions.heatmap) return;
  const points = [];
  for (const samples of journeyCache.values()) {
    for (const sample of samples.slice(-80)) {
      const bbox = sample.bbox;
      points.push({ x: bbox.x + bbox.w / 2, y: bbox.y + bbox.h * 0.82 });
    }
  }
  overlayCtx.save();
  for (const point of points) {
    const gradient = overlayCtx.createRadialGradient(point.x, point.y, 2, point.x, point.y, Math.max(26, rect.width * 0.04));
    gradient.addColorStop(0, "rgba(47, 128, 255, 0.22)");
    gradient.addColorStop(1, "rgba(47, 128, 255, 0)");
    overlayCtx.fillStyle = gradient;
    overlayCtx.beginPath();
    overlayCtx.arc(point.x, point.y, Math.max(26, rect.width * 0.04), 0, Math.PI * 2);
    overlayCtx.fill();
  }
  overlayCtx.restore();
}

function drawJourneyPaths() {
  if (!overlayOptions.journeyPaths) return;
  overlayCtx.save();
  for (const [visitorId, samples] of journeyCache.entries()) {
    if (selectedVisitorId && selectedVisitorId !== visitorId) continue;
    if (samples.length < 2) continue;
    overlayCtx.beginPath();
    samples.slice(-24).forEach((sample, index) => {
      const x = sample.bbox.x + sample.bbox.w / 2;
      const y = sample.bbox.y + sample.bbox.h;
      if (index === 0) overlayCtx.moveTo(x, y);
      else overlayCtx.lineTo(x, y);
    });
    overlayCtx.strokeStyle = selectedVisitorId === visitorId ? "rgba(255, 255, 255, 0.9)" : "rgba(47, 128, 255, 0.38)";
    overlayCtx.lineWidth = selectedVisitorId === visitorId ? 2 : 1;
    overlayCtx.stroke();
  }
  overlayCtx.restore();
}

function rectsOverlap(first, second, padding = 0) {
  return !(
    first.x + first.w + padding < second.x ||
    second.x + second.w + padding < first.x ||
    first.y + first.h + padding < second.y ||
    second.y + second.h + padding < first.y
  );
}

function placeInsightLabel(bbox, textWidth, labelHeight, labelRects, personRects) {
  const labelWidth = textWidth + 14;
  const candidates = [
    { x: bbox.x, y: bbox.y - labelHeight - 7 },
    { x: bbox.x + bbox.w + 8, y: bbox.y + bbox.h * 0.42 },
    { x: bbox.x - labelWidth - 8, y: bbox.y + bbox.h * 0.42 },
    { x: bbox.x, y: bbox.y + bbox.h + 7 },
  ].map((candidate) => ({
    x: Math.max(4, Math.min(candidate.x, overlayCanvas.clientWidth - labelWidth - 4)),
    y: Math.max(4, Math.min(candidate.y, overlayCanvas.clientHeight - labelHeight - 4)),
    w: labelWidth,
    h: labelHeight,
  }));
  return candidates.find((candidate) => {
    const overlapsLabel = labelRects.some((existing) => rectsOverlap(candidate, existing, 3));
    const overlapsFace = personRects.some((person) => rectsOverlap(candidate, { x: person.x, y: person.y, w: person.w, h: person.h * 0.34 }, 2));
    return !overlapsLabel && !overlapsFace;
  }) || candidates[0];
}

function drawPerson(event, bbox, label, zoomedOut, labelRects, personRects) {
  if (!shouldShowOverlayEvent(event, label)) return;
  const selected = selectedVisitorId === event.visitor_id;
  const outlineColor = personOutlineColor(event);
  overlayCtx.save();
  overlayCtx.lineWidth = selected ? 3 : 2;
  overlayCtx.strokeStyle = outlineColor;
  overlayCtx.fillStyle = label.color;
  overlayCtx.globalAlpha = selected ? 0.98 : 0.9;
  overlayCtx.strokeRect(bbox.x, bbox.y, bbox.w, bbox.h);
  overlayCtx.globalAlpha = 0.72;
  overlayCtx.lineWidth = 1;
  overlayCtx.strokeStyle = "rgba(255, 255, 255, 0.72)";
  overlayCtx.strokeRect(bbox.x + 3, bbox.y + 3, Math.max(1, bbox.w - 6), Math.max(1, bbox.h - 6));
  if (label.kind === "product") {
    overlayCtx.globalAlpha = 0.18;
    overlayCtx.fillRect(bbox.x, bbox.y, bbox.w, bbox.h);
    overlayCtx.globalAlpha = 0.98;
  }
  overlayCtx.font = "12px Inter, system-ui, sans-serif";
  const textWidth = overlayCtx.measureText(label.text).width;
  const labelHeight = 21;
  const labelRect = placeInsightLabel(bbox, textWidth, labelHeight, labelRects, personRects);
  labelRects.push(labelRect);
  overlayCtx.globalAlpha = 0.96;
  overlayCtx.fillStyle = outlineColor;
  overlayCtx.fillRect(labelRect.x, labelRect.y, labelRect.w, labelRect.h);
  overlayCtx.fillStyle = "#ffffff";
  overlayCtx.fillText(label.text, labelRect.x + 7, labelRect.y + 14.5);
  if (selected) {
    overlayCtx.strokeStyle = "rgba(255, 255, 255, 0.88)";
    overlayCtx.strokeRect(bbox.x - 3, bbox.y - 3, bbox.w + 6, bbox.h + 6);
  }
  overlayCtx.restore();
}

function drawSceneObservation(text, rect) {
  if (!text) return;
  overlayCtx.save();
  overlayCtx.font = "13px Inter, system-ui, sans-serif";
  const label = String(text).slice(0, 72);
  const textWidth = overlayCtx.measureText(label).width;
  const width = Math.min(rect.width - 20, textWidth + 22);
  const height = 30;
  const x = 10;
  const y = 10;
  overlayCtx.globalAlpha = 0.88;
  overlayCtx.fillStyle = "rgba(16, 24, 32, 0.86)";
  overlayCtx.fillRect(x, y, width, height);
  overlayCtx.fillStyle = "#ffffff";
  overlayCtx.fillText(label, x + 11, y + 19);
  overlayCtx.restore();
}

function updateJourneyCache(events, source, rect) {
  const second = clampTimelineSecond(slider.value);
  for (const event of events) {
    const bbox = parseMetadata(event).bbox;
    if (!Array.isArray(bbox)) continue;
    const scaled = canvasPointFromBBox(bbox, source, rect);
    const samples = journeyCache.get(event.visitor_id) || [];
    if (!samples.some((sample) => sample.second === second)) {
      samples.push({ second, bbox: scaled, eventType: event.event_type, zone: event.zone_id });
      journeyCache.set(event.visitor_id, samples.slice(-120));
    }
  }
}

function registerBadges(events) {
  for (const event of events) {
    let key = `${event.timestamp}:${event.event_type}:${event.visitor_id}:${event.group_id || ""}`;
    let badge = null;
    const insight = insightForEvent(event, events);
    if (overlayOptions.productEvents && insight && ["product", "checkout", "intent", "staff", "exit"].includes(insight.kind)) {
      if (!shouldShowInsight(insight)) continue;
      key = `${event.visitor_id}:${insight.text}`;
      badge = { text: insight.text, detail: zoneLabel(event.zone_id), color: insightColor(insight.kind), ttl: 3200 };
    }
    if (badge && !activeBadges.has(key)) {
      activeBadges.set(key, { ...badge, createdAt: performance.now() });
    }
  }
  for (const item of currentAnomalies) {
    const proof = item.proof || {};
    if (!overlayOptions.anomalies || proof.timestamp !== currentTimelineData?.timestamp) continue;
    const key = `${item.anomaly_id}:${proof.timestamp}`;
    if (!activeBadges.has(key)) {
      activeBadges.set(key, { text: "Anomaly", detail: item.message, color: overlayColors.anomaly, ttl: 4200, createdAt: performance.now() });
    }
  }
}

function renderBadges() {
  const now = performance.now();
  overlayBadges.innerHTML = [...activeBadges.entries()]
    .filter(([key, badge]) => {
      const alive = now - badge.createdAt < badge.ttl;
      if (!alive) activeBadges.delete(key);
      return alive;
    })
    .slice(-4)
    .map(([, badge]) => {
      const opacity = Math.max(0.18, 1 - (now - badge.createdAt) / badge.ttl);
      return `<div class="overlay-badge" style="color:${badge.color}; opacity:${opacity}"><strong>${badge.text}</strong><br>${badge.detail}</div>`;
    })
    .join("");
}

function renderOverlay() {
  const rect = resizeOverlayCanvas();
  overlayCtx.clearRect(0, 0, rect.width, rect.height);
  if (!currentTimelineData || !timelineStart) {
    overlayBadges.innerHTML = "";
    return;
  }
  const events = currentOverlayEvents();
  const source = sourceDimensions(events);
  const groupCounts = groupSizes(events);
  const labelRects = [];
  const zoomedOut = rect.width < 520;
  const personRects = events
    .map((event) => parseMetadata(event).bbox)
    .filter(Array.isArray)
    .map((bbox) => canvasPointFromBBox(bbox, source, rect));
  updateJourneyCache(events, source, rect);
  drawZones(rect);
  drawHeatmap(rect);
  drawJourneyPaths();
  for (const event of events) {
    const bbox = parseMetadata(event).bbox;
    if (!Array.isArray(bbox)) continue;
    const scaled = canvasPointFromBBox(bbox, source, rect);
    const label = eventBusinessLabel(event, groupCounts[event.group_id] || 0);
    drawPerson(event, scaled, label, zoomedOut, labelRects, personRects);
  }
  if (!events.length) {
    drawSceneObservation(sceneObservationForSecond(), rect);
  }
  registerBadges(currentTimelineData.events || events);
  renderBadges();
}

function startOverlayAnimation() {
  if (overlayAnimationId) return;
  const tick = () => {
    renderBadges();
    overlayAnimationId = requestAnimationFrame(tick);
  };
  overlayAnimationId = requestAnimationFrame(tick);
}

function stopOverlayAnimation() {
  if (overlayAnimationId) cancelAnimationFrame(overlayAnimationId);
  overlayAnimationId = null;
}

async function renderJourneyReplay(visitorId) {
  if (!visitorId) {
    journeyReplay.hidden = true;
    journeyReplay.innerHTML = "";
    return;
  }
  journeyReplay.hidden = false;
  journeyReplay.innerHTML = `<h3>${eventBusinessLabel({ visitor_id: visitorId }).text} Journey</h3><p class="summary">Loading journey...</p>`;
  try {
    const data = await getJson(`/visitor/${encodeURIComponent(visitorId)}/timeline?store_id=${encodeURIComponent(storeId())}`);
    const steps = (data.events || []).slice(0, 10).map((event) => {
      const title = event.event_type.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
      return `<div class="journey-step"><span class="journey-dot"></span><div><strong>${title}</strong>${zoneLabel(event.zone)} · ${event.timestamp}</div></div>`;
    });
    journeyReplay.innerHTML = `<h3>${eventBusinessLabel({ visitor_id: visitorId }).text} Journey</h3>${steps.join("") || '<p class="summary">No journey events available.</p>'}`;
  } catch (error) {
    journeyReplay.innerHTML = `<h3>Journey Replay</h3><p class="summary">${error.message}</p>`;
  }
}

async function refreshFunnel() {
  const data = await getJson(`/stores/${storeId()}/funnel`);
  currentFunnel = data;
  const steps = data.flow || [
    { label: "Entered Store", count: data.entry },
    { label: "Visited Product Zone", count: data.zone_enter },
    { label: "Product Interaction", count: data.product_interaction },
    { label: "Billing Counter", count: data.billing_queue_join },
    { label: "Exit", count: data.exit },
  ];
  const maxValue = Math.max(...steps.map((step) => step.count), 1);
  const attention = (data.attention_scores || []).slice(0, 4);
  document.querySelector("#funnel").innerHTML = steps
    .map((step, index) => {
      const value = Number(step.count || 0);
      return `
        <div class="funnel-step">
          <div class="funnel-node">${index + 1}</div>
          <div>
            <span>${step.label}</span>
            <div class="bar-fill" style="width:${Math.max(8, (value / maxValue) * 100)}%"></div>
          </div>
          <strong>${value}</strong>
        </div>
      `;
    })
    .join("") +
    `
      <div class="attention-panel">
        <h3>Customer Attention Scores</h3>
        ${
          attention.length
            ? attention
                .map(
                  (item) => `
                    <div class="attention-row">
                      <span>${item.visitor}</span>
                      <div class="attention-track"><i style="width:${Math.max(3, Number(item.attention_score || 0))}%"></i></div>
                      <strong>${item.attention_score}</strong>
                      <small>${formatAttentionDetail(item)}</small>
                    </div>
                  `
                )
                .join("")
            : '<p class="summary">No product attention signals yet.</p>'
        }
      </div>
    `;
  renderExecutiveSummary();
}

async function refreshAnomalies() {
  const data = await getJson(`/stores/${storeId()}/anomalies`);
  currentAnomalies = data.anomalies || [];
  renderOverlay();
  renderExecutiveSummary();
  document.querySelector("#anomalies").innerHTML =
    data.anomalies.length === 0
      ? '<p class="summary">No current AI insights need attention.</p>'
      : data.anomalies.map((item) => {
          const proof = item.proof || {};
          const proofBits = [
            proof.timestamp ? `time ${proof.timestamp}` : "",
            proof.zone ? `area ${zoneLabel(proof.zone)}` : "",
            proof.measured_value !== undefined && proof.threshold !== undefined ? `${proof.measured_value} ${proof.unit || ""} vs expected ${proof.threshold}` : "",
          ].filter(Boolean).join(" · ");
          return `<div class="anomaly"><strong>${humanizeType(item.anomaly_type)}</strong><p>${item.message}</p><small>${proofBits}</small></div>`;
        }).join("");
}

async function refreshAll() {
  if (!hasProcessedInput) {
    resetDashboard();
    return;
  }
  await refreshTimelineRange();
  await Promise.all([refreshFunnel(), refreshAnomalies(), refreshVideoPreview(), refreshAgentScore()]);
  await Promise.all([refreshMetrics(), refreshTimeline()]);
}

function resetDashboard() {
  timelineStart = null;
  timelineRequestId += 1;
  lastRenderedSecond = null;
  lastVideoTimelineSecond = null;
  currentTimelineData = null;
  currentMetrics = null;
  currentFunnel = null;
  currentAnomalies = [];
  currentScore = null;
  hoverTarget = null;
  selectedVisitorId = null;
  setVideoFocus(false);
  activeBadges.clear();
  journeyCache.clear();
  stopOverlayAnimation();
  window.clearTimeout(videoDrivenRefresh);
  metricsEl.hidden = true;
  executiveSummary.hidden = true;
  workspaceEl.hidden = true;
  lowerEl.hidden = true;
  scorePanel.hidden = true;
  emptyState.hidden = false;
  setProcessedState();
  metricsEl.innerHTML = "";
  summaryCards.innerHTML = "";
  document.querySelector("#events").innerHTML = "";
  document.querySelector("#funnel").innerHTML = "";
  document.querySelector("#anomalies").innerHTML = "";
  scoreBreakdown.innerHTML = "";
  scoreEvidence.innerHTML = "";
  scoreTotal.textContent = "0 / 100";
  scoreLabel.textContent = "Self-Evaluation Based on Rubric";
  summary.textContent = "No processed video yet.";
  selectedTime.textContent = "--";
  slider.min = 0;
  slider.max = 0;
  slider.value = 0;
  renderTimestampHeatmap({});
  videoPreview.removeAttribute("src");
  videoPreview.removeAttribute("poster");
  videoPreview.onloadedmetadata = null;
  videoPreview.load();
  currentVideoFrameUrl = null;
  currentVideoCacheKey = null;
  currentVideoSourceSize = null;
  videoMeta.textContent = "No video loaded";
  debugMeta.textContent = "No internal metadata available.";
  overlayBadges.innerHTML = "";
  overlayTooltip.hidden = true;
  journeyReplay.hidden = true;
  journeyReplay.innerHTML = "";
  setSystemStatus("Ready for analysis", "ready");
  if (downloadInsights) downloadInsights.disabled = true;
  if (saveReview) saveReview.disabled = true;
  setVideoPlaybackButton();
  renderOverlay();
}

function showDashboard() {
  setProcessedState();
  hasProcessedInput = true;
  metricsEl.hidden = false;
  executiveSummary.hidden = false;
  workspaceEl.hidden = false;
  lowerEl.hidden = false;
  scorePanel.hidden = false;
  emptyState.hidden = true;
  if (downloadInsights) downloadInsights.disabled = false;
  if (saveReview) saveReview.disabled = false;
  startOverlayAnimation();
}

document.querySelector("#runDemo").addEventListener("click", async () => {
  setProcessingState("Processing demo CCTV through the analytics agents...");
  try {
    await getJson("/demo/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store_id: storeId(), duration_sec: 8, fps: 10 }),
      timeoutMs: DEMO_TIMEOUT_MS,
    });
    uploadStatus.textContent = "Demo video processed. Use this when you want sample data without uploading your own MP4.";
    showDashboard();
    await refreshAll();
    await refreshSavedReviews();
    setSystemStatus("Insights live", "live");
  } catch (error) {
    uploadStatus.textContent = error.message;
    setProcessingFailedState(error.message);
  }
});

videoUpload.addEventListener("change", () => {
  const file = videoUpload.files[0];
  processUpload.disabled = !file;
  uploadStatus.textContent = file ? `${file.name} ready to process.` : "Select a store camera clip and process it through the analytics agents.";
});

processUpload.addEventListener("click", async () => {
  const file = videoUpload.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".mp4")) {
    uploadStatus.textContent = "Please choose an MP4 file.";
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  formData.append("store_id", storeId());
  formData.append("camera_id", "CAM_UPLOAD_01");
  formData.append("async_mode", "true");
  setProcessingState("Processing uploaded CCTV through the analytics agents...");
  processUpload.disabled = true;
  uploadStatus.textContent = "Uploading CCTV and starting the analytics agents...";
  try {
    let result = await getJson("/videos/upload", {
      method: "POST",
      body: formData,
      timeoutMs: UPLOAD_START_TIMEOUT_MS,
    });
    if (result.status === "queued" && result.job_id) {
      uploadStatus.textContent = "Upload received. Processing continues in the background...";
      result = await pollVideoJob(result.job_id);
    }
    uploadStatus.textContent = processedUploadMessage(result);
    showDashboard();
    await refreshAll();
    await refreshSavedReviews();
    setSystemStatus("Insights live", "live");
  } catch (error) {
    uploadStatus.textContent = error.message;
    setProcessingFailedState(error.message);
  } finally {
    processUpload.disabled = false;
  }
});

slider.addEventListener("input", () => {
  lastRenderedSecond = null;
  selectedTime.textContent = timelineStart ? fmt(timestampForSecond(clampTimelineSecond(slider.value))) : "--";
  syncPreviewFrameToSlider();
  syncVideoToSlider();
  refreshTimeline({ syncVideo: false }).catch((error) => {
    summary.textContent = error.message;
  });
});
videoPreview.addEventListener("seeked", () => {
  lastRenderedSecond = null;
  syncSliderToVideo();
});
videoPreview.addEventListener("timeupdate", () => syncSliderToVideo());
videoPreview.addEventListener("play", setVideoPlaybackButton);
videoPreview.addEventListener("pause", setVideoPlaybackButton);
videoPreview.addEventListener("loadedmetadata", setVideoPlaybackButton);
toggleVideoPlayback?.addEventListener("click", async () => {
  if (!videoPreview.currentSrc && !videoPreview.src) return;
  try {
    if (videoPreview.paused) await videoPreview.play();
    else videoPreview.pause();
  } catch (error) {
    summary.textContent = `Video preview could not start automatically. Use the native play control. ${error.message}`;
  } finally {
    setVideoPlaybackButton();
  }
});
toggleVideoFocus?.addEventListener("click", () => {
  setVideoFocus(!videoFocusEnabled);
});
downloadInsights?.addEventListener("click", downloadInsightsCsv);
saveReview?.addEventListener("click", saveCurrentReview);
loadReview?.addEventListener("click", loadSavedReview);
document.querySelectorAll("[data-overlay-toggle]").forEach((toggle) => {
  toggle.addEventListener("change", () => {
    overlayOptions[toggle.dataset.overlayToggle] = toggle.checked;
    renderOverlay();
  });
});
overlayCanvas.addEventListener("mousemove", (event) => {
  if (!currentTimelineData) return;
  const rect = overlayCanvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const events = currentOverlayEvents();
  const source = sourceDimensions(events);
  hoverTarget = null;
  for (const item of events) {
    const bbox = parseMetadata(item).bbox;
    if (!Array.isArray(bbox)) continue;
    const scaled = canvasPointFromBBox(bbox, source, rect);
    if (x >= scaled.x && x <= scaled.x + scaled.w && y >= scaled.y && y <= scaled.y + scaled.h) {
      hoverTarget = { event: item, bbox: scaled };
      break;
    }
  }
  if (!hoverTarget) {
    overlayTooltip.hidden = true;
    return;
  }
  const label = eventBusinessLabel(hoverTarget.event);
  overlayTooltip.hidden = false;
  overlayTooltip.style.left = `${Math.min(rect.width - 230, x + 12)}px`;
  overlayTooltip.style.top = `${Math.max(8, y - 12)}px`;
  overlayTooltip.innerHTML = `<strong>${label.text}</strong><br>${zoneLabel(hoverTarget.event.zone_id)} · ${hoverTarget.event.event_type.replaceAll("_", " ").toLowerCase()}<br>Time ${currentTimelineData.timestamp}`;
});
overlayCanvas.addEventListener("mouseleave", () => {
  hoverTarget = null;
  overlayTooltip.hidden = true;
});
overlayCanvas.addEventListener("click", () => {
  if (!hoverTarget) return;
  selectedVisitorId = selectedVisitorId === hoverTarget.event.visitor_id ? null : hoverTarget.event.visitor_id;
  renderJourneyReplay(selectedVisitorId);
  renderOverlay();
});
window.addEventListener("resize", renderOverlay);
storeInput.addEventListener("change", () => {
  hasProcessedInput = false;
  resetDashboard();
  refreshSavedReviews();
});
resetDashboard();
refreshSavedReviews();
