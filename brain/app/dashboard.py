from __future__ import annotations


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Vector Brain Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101316;
      --panel: #181d22;
      --panel2: #20262d;
      --text: #e9eef2;
      --muted: #9ba8b4;
      --line: #313942;
      --good: #7bd88f;
      --warn: #ffd166;
      --bad: #ff6b6b;
      --blue: #7cc7ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
      background: #12171b;
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    .status { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: var(--muted); }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      background: var(--panel);
      white-space: nowrap;
    }
    .ok { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      min-height: calc(100vh - 65px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: #11161a;
      overflow: auto;
      max-height: calc(100vh - 65px);
    }
    .event {
      width: 100%;
      display: block;
      text-align: left;
      border: 0;
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      background: transparent;
      color: var(--text);
      cursor: pointer;
    }
    .event:hover, .event.selected { background: var(--panel); }
    .event .row { display: flex; justify-content: space-between; gap: 10px; }
    .kind { color: var(--blue); font-weight: 650; }
    .time { color: var(--muted); font-size: 12px; }
    .summary { color: var(--muted); margin-top: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    section { padding: 18px; overflow: auto; max-height: calc(100vh - 65px); }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .card {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      min-width: 0;
    }
    .card h2 {
      margin: 0;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      color: var(--muted);
      font-weight: 650;
    }
    pre {
      margin: 0;
      padding: 12px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      color: #dbe7ef;
      max-height: 420px;
    }
    .wide { grid-column: 1 / -1; }
    .empty { color: var(--muted); padding: 20px; }
    .tools {
      display: grid;
      grid-template-columns: 1fr auto auto auto auto auto;
      gap: 8px;
      margin-bottom: 14px;
    }
    input, button.action {
      border: 1px solid var(--line);
      background: var(--panel2);
      color: var(--text);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
    }
    button.action { cursor: pointer; }
    button.action:hover { border-color: var(--blue); }
    button.action.active { border-color: var(--good); color: var(--good); }
    .facts {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 10px 12px 0;
    }
    .fact {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel2);
      color: var(--text);
      padding: 6px 8px;
      min-width: 92px;
    }
    .fact span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.2;
      margin-bottom: 2px;
    }
    .battery-meter {
      height: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      overflow: hidden;
      background: #0d1114;
      margin-top: 7px;
      width: 160px;
      max-width: 100%;
    }
    .battery-meter div {
      height: 100%;
      background: var(--good);
      width: 0%;
    }
    .battery-meter.warn div { background: var(--warn); }
    .battery-meter.bad div { background: var(--bad); }
    .robot-strip {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 14px;
      margin-bottom: 14px;
    }
    .robot-strip .card { min-height: 88px; }
    .robot-strip img {
      width: 100%;
      max-height: 260px;
      object-fit: contain;
      border-top: 1px solid var(--line);
      display: block;
      background: #0b0e10;
    }
    @media (max-width: 850px) {
      main { grid-template-columns: 1fr; }
      aside { max-height: 38vh; border-right: 0; border-bottom: 1px solid var(--line); }
      section { max-height: none; }
      .grid { grid-template-columns: 1fr; }
      .tools { grid-template-columns: 1fr 1fr; }
      .tools input { grid-column: 1 / -1; }
      .robot-strip { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Vector Brain Dashboard</h1>
    <div class="status" id="status">
      <span class="pill">loading</span>
    </div>
  </header>
  <main>
    <aside id="events"><div class="empty">Waiting for Gemma output...</div></aside>
    <section>
      <form class="tools" id="chatForm">
        <input id="chatInput" autocomplete="off" placeholder="Type to Vector through the harness..." />
        <button class="action" type="submit">Plan</button>
        <button class="action" type="button" id="speakBtn">Speak</button>
        <button class="action" type="button" id="lookBtn">Look</button>
        <button class="action" type="button" id="visionBtn">Vision</button>
        <button class="action" type="button" id="micBtn">Robot Mic</button>
      </form>
      <div class="robot-strip" id="robotStrip"></div>
      <div id="detail" class="empty">Select an event.</div>
    </section>
  </main>
  <script>
    let selectedId = null;
    let latestEvents = [];
    let latestRobotState = {};
    let latestVisionStatus = {};
    let latestAudioStatus = {};
    let latestListenerStatus = {};

    function pretty(value) {
      return JSON.stringify(value ?? null, null, 2);
    }

    function localTime(ts) {
      return new Date(ts).toLocaleTimeString();
    }

    function summarize(event) {
      const p = event.payload || {};
      if (event.kind === "plan" || event.kind === "autonomy_plan") {
        const say = (p.actions || []).find(a => a.type === "say");
        return say ? say.text : (p.raw || "").slice(0, 90);
      }
      if (event.kind === "execute" || event.kind === "autonomy_execute") {
        return (p.executed || []).map(a => a.type + (a.name ? ":" + a.name : "")).join(", ");
      }
      if (event.kind === "error") return p.error || "error";
      return Object.keys(p).join(", ");
    }

    function renderStatus(health, autonomy, robot, voice, vision, audio, listener) {
      const el = document.getElementById("status");
      const enabled = autonomy && autonomy.enabled;
      const err = autonomy && autonomy.last_error;
      const voiceErr = voice && voice.last_error;
      const visionErr = vision && vision.last_error;
      const audioErr = audio && audio.last_error;
      const listenerErr = listener && listener.last_error;
      const asleep = robot && (robot.sleeping || robot.calm_power_mode);
      const charging = robot && robot.charging;
      const battery = batteryLabel(robot);
      const visionAge = vision && typeof vision.latest_age_seconds === "number" ? `${Math.round(vision.latest_age_seconds)}s` : "no image";
      const visionStale = vision && typeof vision.latest_age_seconds === "number" && vision.latest_age_seconds > (vision.interval_seconds * 2.5);
      const micTone = audio && audio.static_signal_detected ? "bad" : (audio && audio.connected ? "ok" : "");
      const micText = audio && audio.static_signal_detected ? "mic static" : (audio && audio.connected ? "mic stream" : "mic off");
      el.innerHTML = `
        <span class="pill ${health && health.ok ? "ok" : "bad"}">${health && health.ok ? "brain online" : "brain offline"}</span>
        <span class="pill">${health ? health.model : "unknown model"}</span>
        <span class="pill">${health ? health.execution_mode : "unknown mode"}</span>
        <span class="pill ${battery.tone}">${battery.text}</span>
        <span class="pill ${visionErr ? "bad" : (vision && vision.enabled ? (visionStale ? "warn" : "ok") : "")}">vision ${vision && vision.enabled ? "on" : "off"} ${visionAge}</span>
        <span class="pill ${micTone}">${micText}</span>
        <span class="pill ${listener && listener.enabled ? "ok" : ""}">listener ${listener && listener.enabled ? "on" : "off"}</span>
        <span class="pill ${enabled ? "ok" : ""}">autonomy ${enabled ? "on" : "off"}</span>
        <span class="pill ${voice && voice.enabled ? "ok" : ""}">voice ${voice && voice.enabled ? "on" : "off"}</span>
        <span class="pill ${robot && robot.connected ? "ok" : "bad"}">robot ${robot && robot.connected ? "seen" : "unknown"}</span>
        <span class="pill ${asleep ? "warn" : ""}">${asleep ? "asleep" : "awake"}</span>
        <span class="pill ${charging ? "warn" : ""}">${charging ? "charging" : "not charging"}</span>
        <span class="pill ${err || voiceErr || visionErr || audioErr || listenerErr ? "bad" : "ok"}">${err || voiceErr || visionErr || audioErr || listenerErr ? "last error" : "clean"}</span>
      `;
    }

    function batteryLabel(robot) {
      if (!robot) return {text: "battery unknown", tone: ""};
      const names = {0: "unknown", 1: "low", 2: "nominal", 3: "full"};
      const level = names[robot.battery_level] || String(robot.battery_level ?? "unknown");
      const volts = typeof robot.battery_volts === "number" ? ` ${robot.battery_volts.toFixed(2)}V` : "";
      const percent = typeof robot.battery_percent === "number" ? Math.round(robot.battery_percent) : null;
      const prefix = robot.battery_percent_source && robot.battery_percent_source.includes("estimate") ? "est. " : "";
      const tone = robot.low_battery || robot.battery_level === 1 ? "bad" : (robot.charging ? "warn" : "ok");
      const text = percent === null ? `battery ${level}${volts}` : `battery ${prefix}${percent}%`;
      return {text, tone, percent, detail: `${level}${volts}`};
    }

    function batteryMeter(battery) {
      if (battery.percent === null || battery.percent === undefined) return "";
      const percent = Math.max(0, Math.min(100, battery.percent));
      return `<div class="battery-meter ${battery.tone}"><div style="width:${percent}%"></div></div>`;
    }

    function renderRobotStrip(robot, visionStatus, audioStatus, listenerStatus) {
      const el = document.getElementById("robotStrip");
      const vision = robot && robot.latest_vision ? robot.latest_vision : null;
      const battery = batteryLabel(robot);
      const image = vision && vision.path ? `<img src="/robot/latest.jpg?ts=${Date.now()}" alt="Vector camera view" />` : "";
      const age = visionStatus && typeof visionStatus.latest_age_seconds === "number" ? ` · ${Math.round(visionStatus.latest_age_seconds)}s old` : "";
      const micProblem = audioStatus && audioStatus.static_signal_detected;
      const micStatus = micProblem
        ? "Stream is repeating static frames. Robot gateway has not opened real mic audio."
        : (audioStatus && audioStatus.connected ? "AudioFeed is connected." : "AudioFeed is stopped.");
      const listenerSummary = listenerStatus && listenerStatus.enabled
        ? `${listenerStatus.frames_seen || 0} frames, ${listenerStatus.transcripts_seen || 0} transcripts`
        : "Listener stopped";
      el.innerHTML = `
        <div class="card">
          <h2>Live Robot State</h2>
          <div class="facts">
            <div class="fact"><span>Battery</span>${escapeHtml(battery.text.replace("battery ", ""))}${batteryMeter(battery)}</div>
            <div class="fact"><span>Detail</span>${escapeHtml(battery.detail || "unknown")}</div>
            <div class="fact"><span>Power</span>${robot && robot.charging ? "charging" : "not charging"}</div>
            <div class="fact"><span>Mode</span>${robot && (robot.sleeping || robot.calm_power_mode) ? "sleeping" : "awake"}</div>
            <div class="fact"><span>Dock</span>${robot && robot.on_charger ? "on charger" : "off charger"}</div>
          </div>
          <pre>${escapeHtml(pretty(robot || null))}</pre>
        </div>
        <div class="card">
          <h2>Latest View${age}</h2>
          <pre>${escapeHtml(vision && (vision.description || vision.vision_error || vision.error) ? (vision.description || vision.vision_error || vision.error) : "No camera observation yet.")}</pre>
          ${image}
        </div>
        <div class="card wide">
          <h2>Robot Mic</h2>
          <div class="facts">
            <div class="fact"><span>AudioFeed</span>${escapeHtml(micStatus)}</div>
            <div class="fact"><span>Frames</span>${escapeHtml(String(audioStatus && audioStatus.frames_seen || 0))}</div>
            <div class="fact"><span>Signal Bytes</span>${escapeHtml(String(audioStatus && audioStatus.signal_bytes_seen || 0))}</div>
            <div class="fact"><span>Repeated</span>${escapeHtml(String(audioStatus && audioStatus.repeated_frame_count || 0))}</div>
            <div class="fact"><span>STT</span>${escapeHtml(listenerSummary)}</div>
          </div>
          <pre>${escapeHtml(pretty({audio: audioStatus || null, listener: listenerStatus || null}))}</pre>
        </div>
      `;
    }

    function renderVisionButton(visionStatus) {
      const btn = document.getElementById("visionBtn");
      const enabled = visionStatus && visionStatus.enabled;
      btn.classList.toggle("active", !!enabled);
      btn.textContent = enabled ? "Vision On" : "Vision";
    }

    function renderMicButton(listenerStatus) {
      const btn = document.getElementById("micBtn");
      const enabled = listenerStatus && listenerStatus.enabled;
      btn.classList.toggle("active", !!enabled);
      btn.textContent = enabled ? "Mic On" : "Robot Mic";
    }

    function renderEvents(events) {
      const el = document.getElementById("events");
      if (!events.length) {
        el.innerHTML = '<div class="empty">Waiting for Gemma output...</div>';
        return;
      }
      if (!selectedId) selectedId = events[0].id;
      el.innerHTML = events.map(event => `
        <button class="event ${event.id === selectedId ? "selected" : ""}" data-id="${event.id}">
          <div class="row"><span class="kind">${event.kind}</span><span class="time">${localTime(event.ts)}</span></div>
          <div class="summary">${escapeHtml(summarize(event))}</div>
        </button>
      `).join("");
      el.querySelectorAll("button").forEach(btn => {
        btn.addEventListener("click", () => {
          selectedId = Number(btn.dataset.id);
          renderEvents(latestEvents);
          renderDetail();
        });
      });
    }

    function renderDetail() {
      const event = latestEvents.find(e => e.id === selectedId);
      const el = document.getElementById("detail");
      if (!event) {
        el.innerHTML = '<div class="empty">Select an event.</div>';
        return;
      }
      const p = event.payload || {};
      el.innerHTML = `
        <div class="grid">
          <div class="card"><h2>Event</h2><pre>${escapeHtml(pretty({id: event.id, ts: event.ts, kind: event.kind}))}</pre></div>
          <div class="card"><h2>Actions</h2><pre>${escapeHtml(pretty(p.actions || p.executed || []))}</pre></div>
          <div class="card"><h2>Denied / Safety</h2><pre>${escapeHtml(pretty({denied_actions: p.denied_actions || [], safety_notes: p.safety_notes || []}))}</pre></div>
          <div class="card"><h2>Robot State</h2><pre>${escapeHtml(pretty(p.robot_state || null))}</pre></div>
          <div class="card wide"><h2>Gemma Raw</h2><pre>${escapeHtml(p.raw || "")}</pre></div>
          <div class="card wide"><h2>Full Payload</h2><pre>${escapeHtml(pretty(p))}</pre></div>
        </div>
      `;
    }

    function escapeHtml(str) {
      return String(str).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    async function poll() {
      try {
        const [health, autonomy, robot, voice, vision, audio, listener, events] = await Promise.all([
          fetch("/health").then(r => r.json()),
          fetch("/autonomy/status").then(r => r.json()),
          fetch("/robot/state").then(r => r.json()),
          fetch("/voice/status").then(r => r.json()),
          fetch("/vision/status").then(r => r.json()),
          fetch("/audio/status").then(r => r.json()),
          fetch("/listener/status").then(r => r.json()),
          fetch("/events?limit=100").then(r => r.json())
        ]);
        latestRobotState = robot || {};
        latestVisionStatus = vision || {};
        latestAudioStatus = audio || {};
        latestListenerStatus = listener || {};
        renderStatus(health, autonomy, latestRobotState, voice, latestVisionStatus, latestAudioStatus, latestListenerStatus);
        renderRobotStrip(latestRobotState, latestVisionStatus, latestAudioStatus, latestListenerStatus);
        renderVisionButton(latestVisionStatus);
        renderMicButton(latestListenerStatus);
        latestEvents = events.events || [];
        renderEvents(latestEvents);
        renderDetail();
      } catch (err) {
        document.getElementById("status").innerHTML = `<span class="pill bad">${escapeHtml(err.message)}</span>`;
      }
    }

    async function toggleVision() {
      const btn = document.getElementById("visionBtn");
      btn.disabled = true;
      try {
        if (latestVisionStatus && latestVisionStatus.enabled) {
          await fetch("/vision/stop", {method: "POST"});
        } else {
          await fetch("/vision/start", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled: true, interval_seconds: 20, respect_sleep: true})
          });
        }
        await poll();
      } finally {
        btn.disabled = false;
      }
    }

    async function toggleMic() {
      const btn = document.getElementById("micBtn");
      btn.disabled = true;
      try {
        if (latestListenerStatus && latestListenerStatus.enabled) {
          await fetch("/listener/stop", {method: "POST"});
        } else {
          await fetch("/listener/start", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              enabled: true,
              auto_route: true,
              execute: false,
              dry_run: true,
              sample_rate: 16000,
              vad_mode: 2,
              frame_ms: 20,
              min_speech_ms: 300,
              silence_ms: 700,
              max_utterance_ms: 8000,
              pre_roll_ms: 300,
              min_rms: 120,
              stt_model: "tiny.en",
              language: "en",
              compute_type: "int8",
              mute_after_route_seconds: 5.0
            })
          });
        }
        await poll();
      } finally {
        btn.disabled = false;
      }
    }

    async function sendChat(execute, suppliedText) {
      const input = document.getElementById("chatInput");
      const text = (suppliedText || input.value).trim();
      if (!text) return;
      input.value = "";
      await fetch("/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          user_text: text,
          execute,
          dry_run: !execute,
          robot_state: latestRobotState || {}
        })
      });
      await poll();
    }

    async function lookNow() {
      const btn = document.getElementById("lookBtn");
      btn.disabled = true;
      btn.textContent = "Looking";
      try {
        await fetch("/robot/look", {method: "POST"});
        await poll();
      } finally {
        btn.disabled = false;
        btn.textContent = "Look";
      }
    }

    document.getElementById("chatForm").addEventListener("submit", event => {
      event.preventDefault();
      sendChat(false);
    });
    document.getElementById("speakBtn").addEventListener("click", () => sendChat(true));
    document.getElementById("lookBtn").addEventListener("click", lookNow);
    document.getElementById("visionBtn").addEventListener("click", toggleVision);
    document.getElementById("micBtn").addEventListener("click", toggleMic);

    poll();
  setInterval(poll, 7500);
  </script>
</body>
</html>
"""
