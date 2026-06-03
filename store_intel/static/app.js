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
const downloadInsights = document.querySelector("#downloadInsights");
let timelineStart = null;
let hasProcessedInput = false;
let timelineRequestId = 0;
let lastRenderedSecond = null;
let videoDrivenRefresh = null;
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
const activeBadges = new Map();
const journeyCache = new Map();
const overlayOptions = {
  customers: true,
  employees: true,
  productEvents: true,
  heatmap: false,
  journeyPaths: true,
  anomalies: true,
};

const overlayColors = {
  customer: "#2f80ff",
  employee: "#f28a24",
  returning: "#8b5cf6",
  group: "#20cde2",
  product: "#28b463",
  anomaly: "#e53935",
  zone: "rgba(255, 255, 255, 0.52)",
};

const fmt = (value) => value.toISOString().replace(".000Z", "Z");
const storeId = () => storeInput.value.trim() || "STORE_BLR_002";

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
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
  if (headline.startsWith("Currently in")) return `Shopping in ${zone}`;
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

function zoneLabel(zoneId) {
  const key = String(zoneId || "").toUpperCase().replaceAll(" ", "_");
  const labels = {
    ENTRY: "Entrance",
    AISLE_A: "Product Aisle",
    PREMIUM: "Premium Section",
    BILLING: "Checkout",
    EXIT: "Exit",
  };
  return labels[key] || String(zoneId || "Unknown").replaceAll("_", " ");
}

function eventBusinessLabel(event, groupSize = 0) {
  if (event.event_type === "REENTRY") return { text: "Returning Visitor", color: overlayColors.returning, kind: "returning" };
  if (event.is_staff || event.role === "staff") return { text: "Employee", color: overlayColors.employee, kind: "employee" };
  if (event.event_type === "PRODUCT_INTERACTION") return { text: "Product Interest", color: overlayColors.product, kind: "product" };
  if (groupSize > 1) return { text: `Group (${groupSize})`, color: overlayColors.group, kind: "group" };
  return { text: `Customer #${visitorNumber(event.visitor_id)}`, color: overlayColors.customer, kind: "customer" };
}

function shouldShowOverlayEvent(event, label) {
  if (label.kind === "employee") return overlayOptions.employees;
  if (label.kind === "product") return overlayOptions.productEvents;
  return overlayOptions.customers;
}

function currentOverlayEvents() {
  const events = currentTimelineData?.events || [];
  const grouped = new Map();
  for (const event of events) {
    const metadata = parseMetadata(event);
    if (!Array.isArray(metadata.bbox)) continue;
    const priority = {
      PRODUCT_INTERACTION: 6,
      CHECKOUT_VISIT: 5,
      BILLING_QUEUE_JOIN: 5,
      REENTRY: 4,
      ZONE_DWELL: 3,
      ZONE_ENTER: 2,
      ENTRY: 1,
    }[event.event_type] || 0;
    const existing = grouped.get(event.visitor_id);
    if (!existing || priority >= existing.priority) {
      grouped.set(event.visitor_id, { event, metadata, priority });
    }
  }
  return [...grouped.values()].map((item) => item.event);
}

function groupSizes(events) {
  const counts = {};
  for (const event of events) {
    if (event.group_id) counts[event.group_id] = (counts[event.group_id] || 0) + 1;
  }
  return counts;
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
  renderTimestampHeatmap(data.zone_activity);
  if (syncVideo) syncVideoToSlider();
  renderOverlay();
  const displayEvents = data.display_events || data.events.map((event) => ({
    headline: event.event_type,
    visitor: event.visitor_id,
    zone: event.zone_id || "Unknown Zone",
  }));
  document.querySelector("#events").innerHTML = displayEvents
    .map(
      (event) => `
        <div class="event-row">
          <strong>${businessActivityHeadline(event)}</strong>
          <span>${event.visitor}</span>
          <span>${zoneLabel(event.zone)}</span>
        </div>
      `
    )
    .join("");
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
  selectedTime.textContent = data.start_timestamp;
  return data;
}

async function refreshHeatmap() {
  const data = await getJson(`/stores/${storeId()}/heatmap`);
  const zones = ["ENTRY", "AISLE_A", "BILLING"];
  const maxValue = Math.max(...zones.map((zone) => data.zones[zone] || data.activity[zone] || 1));
  document.querySelector("#heatmap").innerHTML = zones
    .map((zone) => {
      const value = data.zones[zone] || data.activity[zone] || 0;
      const alpha = 0.35 + 0.55 * (value / maxValue);
      const color = zone === "ENTRY" ? "61,139,123" : zone === "BILLING" ? "182,95,78" : "91,99,164";
      return `<div class="zone" style="background: rgba(${color}, ${alpha})"><strong>${zone}</strong><small>${value} ms/activity</small></div>`;
    })
    .join("");
}

function renderTimestampHeatmap(zoneActivity) {
  const zones = ["ENTRY", "AISLE_A", "BILLING"];
  const maxValue = Math.max(...zones.map((zone) => zoneActivity[zone] || 0), 1);
  document.querySelector("#heatmap").innerHTML = zones
    .map((zone) => {
      const value = zoneActivity[zone] || 0;
      const alpha = value ? 0.3 + 0.6 * (value / maxValue) : 0.16;
      const color = zone === "ENTRY" ? "61,139,123" : zone === "BILLING" ? "182,95,78" : "91,99,164";
      const label = zone === "AISLE_A" ? "AISLE A" : zone;
      const noun = value === 1 ? "person" : "people";
      return `<div class="zone" style="background: rgba(${color}, ${alpha})"><strong>${label}</strong><small>${value} ${noun} now</small></div>`;
    })
    .join("");
}

async function refreshVideoPreview() {
  try {
    const data = await getJson(`/stores/${storeId()}/video/current`);
    const cacheKey = encodeURIComponent(data.updated_at);
    currentVideoFrameUrl = data.frame_url || data.poster_url;
    currentVideoCacheKey = cacheKey;
    currentVideoSourceSize = { width: data.width || 960, height: data.height || 540 };
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
  if (String(second) !== slider.value) {
    slider.value = second;
    selectedTime.textContent = fmt(timestampForSecond(second));
    syncPreviewFrameToSlider();
  }
  if (!refresh) return;
  window.clearTimeout(videoDrivenRefresh);
  videoDrivenRefresh = window.setTimeout(() => {
    refreshTimeline({ syncVideo: false }).catch((error) => {
      summary.textContent = error.message;
    });
  }, 120);
}

function drawZones(rect) {
  const zones = [
    { name: "Entrance", points: [[0.02, 0.05], [0.34, 0.05], [0.34, 0.94], [0.02, 0.94]] },
    { name: "Product Aisle", points: [[0.35, 0.05], [0.68, 0.05], [0.68, 0.94], [0.35, 0.94]] },
    { name: "Premium Section", points: [[0.45, 0.18], [0.63, 0.18], [0.63, 0.62], [0.45, 0.62]] },
    { name: "Checkout", points: [[0.69, 0.05], [0.98, 0.05], [0.98, 0.94], [0.69, 0.94]] },
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
    overlayCtx.fillStyle = "rgba(255, 255, 255, 0.035)";
    overlayCtx.strokeStyle = overlayColors.zone;
    overlayCtx.fill();
    overlayCtx.stroke();
    overlayCtx.fillStyle = "rgba(255, 255, 255, 0.76)";
    overlayCtx.fillText(zone.name, zone.points[0][0] * rect.width + 8, zone.points[0][1] * rect.height + 18);
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

function drawPerson(event, bbox, label, zoomedOut, labelRows) {
  if (!shouldShowOverlayEvent(event, label)) return;
  const selected = selectedVisitorId === event.visitor_id;
  overlayCtx.save();
  overlayCtx.lineWidth = selected ? 2 : 1.25;
  overlayCtx.strokeStyle = label.color;
  overlayCtx.fillStyle = label.color;
  if (!zoomedOut) overlayCtx.strokeRect(bbox.x, bbox.y, bbox.w, bbox.h);
  overlayCtx.font = "11px Inter, system-ui, sans-serif";
  const textWidth = overlayCtx.measureText(label.text).width;
  const labelHeight = 18;
  let labelX = Math.max(4, Math.min(bbox.x, overlayCanvas.clientWidth - textWidth - 14));
  let labelY = Math.max(6, bbox.y - labelHeight - 4);
  while (labelRows.some((row) => Math.abs(row.y - labelY) < labelHeight && labelX < row.x + row.w && labelX + textWidth + 14 > row.x)) {
    labelY += labelHeight + 2;
  }
  labelRows.push({ x: labelX, y: labelY, w: textWidth + 14 });
  overlayCtx.globalAlpha = 0.94;
  overlayCtx.fillRect(labelX, labelY, textWidth + 14, labelHeight);
  overlayCtx.fillStyle = "#ffffff";
  overlayCtx.fillText(label.text, labelX + 7, labelY + 12.5);
  if (selected) {
    overlayCtx.strokeStyle = "rgba(255, 255, 255, 0.88)";
    overlayCtx.strokeRect(bbox.x - 3, bbox.y - 3, bbox.w + 6, bbox.h + 6);
  }
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
  const groupCounts = groupSizes(events);
  for (const event of events) {
    const key = `${event.timestamp}:${event.event_type}:${event.visitor_id}:${event.group_id || ""}`;
    let badge = null;
    if (overlayOptions.productEvents && event.event_type === "PRODUCT_INTERACTION") {
      badge = { text: "Product Interest", detail: `${eventBusinessLabel(event).text} in ${zoneLabel(event.zone_id)}`, color: overlayColors.product, ttl: 2600 };
    } else if (overlayOptions.productEvents && ["CHECKOUT_VISIT", "BILLING_QUEUE_JOIN"].includes(event.event_type)) {
      badge = { text: "Checkout Visit", detail: `${eventBusinessLabel(event).text} approached checkout`, color: overlayColors.employee, ttl: 3000 };
    } else if (event.event_type === "ZONE_DWELL" && Number(event.dwell_ms || 0) >= 3000) {
      badge = { text: "High Purchase Intent", detail: `${eventBusinessLabel(event).text} dwell threshold crossed`, color: overlayColors.product, ttl: 3000 };
    } else if (event.event_type === "REENTRY") {
      badge = { text: "Returning Visitor", detail: `${eventBusinessLabel(event).text} returned at entrance`, color: overlayColors.returning, ttl: 4000 };
    } else if (event.group_id && groupCounts[event.group_id] > 1) {
      badge = { text: `Group of ${groupCounts[event.group_id]}`, detail: "Synchronized entry or movement", color: overlayColors.group, ttl: 3000 };
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
  const labelRows = [];
  const zoomedOut = rect.width < 520;
  updateJourneyCache(events, source, rect);
  drawZones(rect);
  drawHeatmap(rect);
  drawJourneyPaths();
  for (const event of events) {
    const bbox = parseMetadata(event).bbox;
    if (!Array.isArray(bbox)) continue;
    const scaled = canvasPointFromBBox(bbox, source, rect);
    const label = eventBusinessLabel(event, groupCounts[event.group_id] || 0);
    drawPerson(event, scaled, label, zoomedOut, labelRows);
  }
  registerBadges(events);
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
                      <div class="attention-track"><i style="width:${item.attention_score}%"></i></div>
                      <strong>${item.attention_score}</strong>
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
  currentTimelineData = null;
  currentMetrics = null;
  currentFunnel = null;
  currentAnomalies = [];
  currentScore = null;
  hoverTarget = null;
  selectedVisitorId = null;
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
  setVideoPlaybackButton();
  renderOverlay();
}

function showDashboard() {
  hasProcessedInput = true;
  metricsEl.hidden = false;
  executiveSummary.hidden = false;
  workspaceEl.hidden = false;
  lowerEl.hidden = false;
  scorePanel.hidden = false;
  emptyState.hidden = true;
  if (downloadInsights) downloadInsights.disabled = false;
  startOverlayAnimation();
}

document.querySelector("#runDemo").addEventListener("click", async () => {
  showDashboard();
  summary.textContent = "Generating and processing sample CCTV footage...";
  setSystemStatus("Processing demo video", "working");
  await getJson("/demo/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ store_id: storeId(), duration_sec: 8, fps: 10 }),
  });
  uploadStatus.textContent = "Demo video processed. Use this when you want sample data without uploading your own MP4.";
  await refreshAll();
  setSystemStatus("Insights live", "live");
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
  showDashboard();
  processUpload.disabled = true;
  uploadStatus.textContent = "Processing video through input, analyzer, event, memory, and API agents...";
  setSystemStatus("Processing uploaded video", "working");
  try {
    const result = await getJson("/videos/upload", {
      method: "POST",
      body: formData,
    });
    uploadStatus.textContent = `${result.events_inserted} events processed from ${result.input.duration_sec}s of video.`;
    await refreshAll();
    setSystemStatus("Insights live", "live");
  } catch (error) {
    uploadStatus.textContent = error.message;
    resetDashboard();
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
downloadInsights?.addEventListener("click", downloadInsightsCsv);
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
});
resetDashboard();
