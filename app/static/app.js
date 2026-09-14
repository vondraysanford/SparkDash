/* SparkDash frontend: polling dashboard + streaming chat client. */
"use strict";

const POLL_MS = 2000;
const WINDOW_POINTS = 150; // 5 min at 2s polls

// ---------------------------------------------------------------------------
// Metrics polling + chart
// ---------------------------------------------------------------------------

const rateChart = new Chart(document.getElementById("rate-chart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "prompt tok/s",
        data: [],
        borderColor: "#58a6ff",
        backgroundColor: "rgba(88,166,255,0.12)",
        tension: 0.3,
        pointRadius: 0,
        fill: true,
      },
      {
        label: "generation tok/s",
        data: [],
        borderColor: "#f78136",
        backgroundColor: "rgba(247,129,54,0.12)",
        tension: 0.3,
        pointRadius: 0,
        fill: true,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
      x: { ticks: { color: "#8b949e", maxTicksLimit: 6 }, grid: { color: "#21262d" } },
      y: { beginAtZero: true, ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
    },
    plugins: { legend: { labels: { color: "#e6edf3", boxWidth: 12 } } },
  },
});

const badge = document.getElementById("health-badge");
const healthText = document.getElementById("health-text");
const kvFill = document.getElementById("kv-fill");
const kvValue = document.getElementById("kv-value");
const runningEl = document.getElementById("running");
const waitingEl = document.getElementById("waiting");

function pushPoint(p) {
  const time = new Date(p.t * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  rateChart.data.labels.push(time);
  rateChart.data.datasets[0].data.push(p.prompt_tokens_per_sec);
  rateChart.data.datasets[1].data.push(p.generation_tokens_per_sec);
  if (rateChart.data.labels.length > WINDOW_POINTS) {
    rateChart.data.labels.shift();
    rateChart.data.datasets.forEach((d) => d.data.shift());
  }
  rateChart.update("none");
}

async function pollHealth() {
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    badge.classList.toggle("up", h.ok);
    badge.classList.toggle("down", !h.ok);
    healthText.textContent = h.ok ? `up · ${h.latency_ms} ms` : "down";
  } catch {
    badge.classList.remove("up");
    badge.classList.add("down");
    healthText.textContent = "down";
  }
}

async function pollMetrics() {
  try {
    const r = await fetch("/api/metrics");
    const m = await r.json();
    pushPoint(m);
    kvFill.style.width = `${Math.min(100, m.kv_cache_usage_pct)}%`;
    kvValue.textContent = `${m.kv_cache_usage_pct}%`;
    runningEl.textContent = m.requests_running;
    waitingEl.textContent = m.requests_waiting;
  } catch {
    /* backend itself unreachable — chart just stalls, next poll retries */
  }
}

async function backfillHistory() {
  try {
    const r = await fetch("/api/metrics/history");
    const rows = await r.json();
    rows.slice(-WINDOW_POINTS).forEach(pushPoint);
  } catch {
    /* ignore */
  }
}

pollHealth();
backfillHistory().then(() => pollMetrics());
setInterval(pollHealth, POLL_MS);
setInterval(pollMetrics, POLL_MS);

// ---------------------------------------------------------------------------
// Chat over SSE
// ---------------------------------------------------------------------------

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const stopBtn = document.getElementById("stop-btn");

const tplUser = document.getElementById("tpl-user");
const tplAssistant = document.getElementById("tpl-assistant");
const tplError = document.getElementById("tpl-error");

/** messages payload sent upstream; mirrors what's rendered. */
const messages = [];

let controller = null; // AbortController for the in-flight stream

function scrollBottom() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

function addUserBubble(text) {
  const node = tplUser.content.cloneNode(true);
  node.querySelector(".bubble").textContent = text;
  chatLog.appendChild(node);
  scrollBottom();
}

function addErrorBubble(text) {
  const node = tplError.content.cloneNode(true);
  node.textContent = text;
  chatLog.appendChild(node);
  scrollBottom();
}

/**
 * Create an assistant message skeleton.
 * Returns handles for streaming into reasoning/content separately.
 */
function addAssistantShell() {
  const node = tplAssistant.content.cloneNode(true);
  const root = node.querySelector(".msg.assistant");
  const details = node.querySelector("details.thinking");
  const reasoningEl = node.querySelector(".reasoning");
  const bubble = node.querySelector(".bubble");
  chatLog.appendChild(node);
  return {
    root,
    details,
    reasoningEl,
    bubble,
    reasoningText: "",
    contentText: "",
    appendReasoning(piece) {
      this.reasoningText += piece;
      this.reasoningEl.textContent = this.reasoningText;
      this.reasoningEl.scrollTop = this.reasoningEl.scrollHeight;
      if (this.reasoningText) {
        this.details.classList.add("has-reasoning");
        if (!this.details.open && !this._autoOpened) {
          // Peek while thinking; user can collapse it any time.
          this.details.open = true;
          this._autoOpened = true;
        }
      }
      scrollBottom();
    },
    appendContent(piece) {
      this.contentText += piece;
      this.bubble.textContent = this.contentText;
      scrollBottom();
    },
    finalize() {
      // Collapse thinking once the answer starts arriving for real.
      if (this.contentText) this.details.open = false;
      if (!this.reasoningText) this.details.remove();
    },
  };
}

function setBusy(busy) {
  sendBtn.disabled = busy;
  stopBtn.disabled = !busy;
}

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

stopBtn.addEventListener("click", () => {
  if (controller) controller.abort();
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || controller) return;
  chatInput.value = "";

  addUserBubble(text);
  messages.push({ role: "user", content: text });

  const shell = addAssistantShell();
  // Insert a placeholder assistant entry so follow-up turns have context.
  const assistantRecord = { role: "assistant", content: "", reasoning: "" };
  messages.push(assistantRecord);
  shell._record = assistantRecord;

  setBusy(true);
  controller = new AbortController();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const payload = line.slice(6);

        if (payload === "[DONE]") break;

        let evt;
        try {
          evt = JSON.parse(payload);
        } catch {
          continue;
        }

        if (evt.type === "delta") {
          if (evt.reasoning) shell.appendReasoning(evt.reasoning);
          if (evt.content) shell.appendContent(evt.content);
          if (evt.content) assistantRecord.content += evt.content;
          if (evt.reasoning) assistantRecord.reasoning += evt.reasoning;
        } else if (evt.type === "error") {
          addErrorBubble(`⚠ ${evt.error}`);
        }
        // type === "done": nothing to do, stream will close
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      addErrorBubble(`⚠ ${err.message || "stream failed"}`);
    }
  } finally {
    shell.finalize();
    if (!assistantRecord.content && !assistantRecord.reasoning) {
      // Remove the empty placeholder so history stays clean.
      messages.splice(messages.indexOf(assistantRecord), 1);
    }
    controller = null;
    setBusy(false);
    chatInput.focus();
  }
});
