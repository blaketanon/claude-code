const $ = (sel, root = document) => root.querySelector(sel);
const app = $("#app");
let config = { features: {} };
let cleanupView = () => {};

// ---------- api ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && !(opts.body instanceof FormData) ? { "content-type": "application/json" } : undefined,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(json.error || res.statusText), { status: res.status });
  return json;
}

/** POST /chat and stream SSE events. Resolves with the final reply text. */
async function streamChat(id, history, mode, onText, signal) {
  const res = await fetch(`/api/avatars/${id}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ history, mode }),
    signal,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i).replace(/^data: /, "");
      buf = buf.slice(i + 2);
      const evt = JSON.parse(line);
      if (evt.type === "text") onText(evt.text);
      else if (evt.type === "done") return evt.text;
      else if (evt.type === "error") throw new Error(evt.error);
    }
  }
  throw new Error("Connection closed before the reply finished");
}

const storage = {
  get(k, fallback) { try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};

// ---------- router ----------
async function route() {
  cleanupView();
  cleanupView = () => {};
  const m = location.hash.match(/^#\/a\/([a-f0-9]{16})/);
  if (m) await renderAvatar(m[1]);
  else await renderHome();
}
window.addEventListener("hashchange", route);

// ---------- home ----------
async function renderHome() {
  app.replaceChildren($("#tpl-home").content.cloneNode(true));
  $("#consent-text").textContent = config.consentStatement;
  const form = $("#create-form");
  const preview = $("#preview");

  form.video.addEventListener("change", () => {
    const f = form.video.files[0];
    if (!f) return preview.classList.add("hidden");
    preview.src = URL.createObjectURL(f);
    preview.classList.remove("hidden");
    if (!form.name.value) form.name.value = f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 80);
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    $("#create-error").textContent = "";
    const f = form.video.files[0];
    if (f && f.size > config.maxUploadMb * 1024 * 1024) {
      $("#create-error").textContent = `Video is larger than ${config.maxUploadMb} MB.`;
      return;
    }
    const data = new FormData(form);
    data.set("consent", form.consent.checked ? "true" : "false");
    if (!form.portrait.files[0]) data.delete("portrait");

    // XHR (not fetch) so we get upload progress for big videos.
    const xhr = new XMLHttpRequest();
    const bar = $("#upload-progress");
    bar.classList.remove("hidden");
    form.querySelector("button").disabled = true;
    xhr.upload.onprogress = (ev) => ev.lengthComputable && (bar.firstElementChild.style.width = `${(ev.loaded / ev.total) * 100}%`);
    xhr.onload = () => {
      const json = JSON.parse(xhr.responseText || "{}");
      if (xhr.status === 201) location.hash = `#/a/${json.id}`;
      else {
        $("#create-error").textContent = json.error || "Upload failed";
        form.querySelector("button").disabled = false;
        bar.classList.add("hidden");
      }
    };
    xhr.onerror = () => {
      $("#create-error").textContent = "Upload failed - check your connection.";
      form.querySelector("button").disabled = false;
    };
    xhr.open("POST", "/api/avatars");
    xhr.send(data);
  });

  const list = $("#avatar-list");
  const avatars = await api("/api/avatars");
  if (!avatars.length) list.innerHTML = `<li class="muted">No avatars yet.</li>`;
  for (const a of avatars) {
    const li = document.createElement("li");
    li.innerHTML = `<a href="#/a/${a.id}">${a.portrait ? `<img src="/media/${a.id}/portrait.jpg" alt="">` : `<span class="ph"></span>`}
      <span><strong></strong><br><span class="muted"></span></span></a>`;
    li.querySelector("strong").textContent = a.name;
    li.querySelector(".muted").textContent = a.status === "ready" ? "Ready to chat" : a.status;
    list.append(li);
  }
}

// ---------- avatar room ----------
async function renderAvatar(id) {
  app.replaceChildren($("#tpl-avatar").content.cloneNode(true));
  const root = app;
  let avatar;
  let pollTimer;
  const historyKey = `history:${id}`;
  const history = storage.get(historyKey, []);
  const saveHistory = () => storage.set(historyKey, history.slice(-80));

  const refresh = async () => {
    try {
      avatar = await api(`/api/avatars/${id}`);
    } catch (err) {
      if (err.status === 404) { location.hash = "#/"; return; }
      throw err;
    }
    paintSide();
    if (avatar.status === "queued" || avatar.status === "processing") pollTimer = setTimeout(refresh, 2000);
  };

  function paintSide() {
    const img = $(".portrait", root);
    if (avatar.portrait) img.src = `/media/${id}/portrait.jpg?v=${encodeURIComponent(avatar.updatedAt)}`;
    $(".stage-portrait", root).src = img.src || "";
    $(".name", root).textContent = avatar.name;
    const pill = $(".status-pill", root);
    pill.className = `status-pill ${avatar.status}`;
    pill.textContent = avatar.status === "error" ? `Error: ${avatar.error}` : avatar.status;
    const steps = $(".steps", root);
    steps.replaceChildren(...avatar.steps.map((s) => {
      const li = document.createElement("li");
      li.className = s.status;
      li.textContent = s.label;
      if (s.detail) {
        const d = document.createElement("span");
        d.className = "detail";
        d.textContent = s.detail;
        li.append(d);
      }
      return li;
    }));
    if (avatar.persona) {
      $(".persona", root).classList.remove("hidden");
      $(".summary", root).textContent = avatar.persona.summary;
      $(".traits", root).replaceChildren(...avatar.persona.traits.map((t) => Object.assign(document.createElement("span"), { textContent: t })));
      $(".style", root).textContent = avatar.persona.speakingStyle;
    }
    const ready = avatar.status === "ready";
    root.querySelectorAll(".composer input, .composer button, .start-call").forEach((el) => (el.disabled = !ready));
    $(".retrain", root).disabled = avatar.status === "processing" || avatar.status === "queued";
    if (ready && !$(".messages", root).childElementCount) paintHistory();
  }

  // ----- chat -----
  const messages = $(".messages", root);
  function addMsg(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    messages.append(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }
  function addPlayButton(div, text) {
    const b = document.createElement("button");
    b.className = "play";
    b.textContent = "▶";
    b.title = "Hear it in their voice";
    b.onclick = async () => {
      b.disabled = true;
      try { await speakAndPlay(text, { video: false }); } finally { b.disabled = false; }
    };
    div.append(b);
  }
  function paintHistory() {
    messages.replaceChildren();
    if (avatar.persona?.greeting && !history.length) addMsg("assistant", avatar.persona.greeting);
    for (const m of history) {
      const d = addMsg(m.role, m.content);
      if (m.role === "assistant") addPlayButton(d, m.content);
    }
  }

  let inflight = null;
  /** Sends one user turn; streams the reply into the chat log and, in a call, the caption. */
  async function converse(text, mode) {
    inflight?.abort();
    const ctrl = (inflight = new AbortController());
    const turn = { role: "user", content: text };
    history.push(turn);
    saveHistory();
    addMsg("user", text);
    const div = addMsg("assistant", "…");
    let acc = "";
    try {
      const reply = await streamChat(id, history, mode, (t) => {
        acc += t;
        div.textContent = acc;
        if (mode === "voice") setCaption(acc);
        messages.scrollTop = messages.scrollHeight;
      }, ctrl.signal);
      div.textContent = reply;
      addPlayButton(div, reply);
      history.push({ role: "assistant", content: reply });
      saveHistory();
      return reply;
    } catch (err) {
      div.remove();
      if (err.name !== "AbortError") addMsg("error", err.message);
      // keep history valid: drop this unanswered user turn (a newer turn may already follow it)
      const i = history.lastIndexOf(turn);
      if (i >= 0) { history.splice(i, 1); saveHistory(); }
      return null;
    }
  }

  $(".tab-chat .composer", root).addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = e.target.text;
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await converse(text, "chat");
  });

  // ----- tabs -----
  root.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      root.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
      $(".tab-chat", root).classList.toggle("hidden", b.dataset.tab !== "chat");
      $(".tab-call", root).classList.toggle("hidden", b.dataset.tab !== "call");
    }),
  );

  // ----- playback (shared by chat ▶ and the call) -----
  const stage = $(".stage", root);
  const stageImg = $(".stage-portrait", root);
  const stageVideo = $(".stage-video", root);
  const callStatus = $(".call-status", root);
  const setCaption = (t) => ($(".caption", root).textContent = t);
  let audioCtx;
  let stopPlayback = () => {};

  function animatePortrait(level) {
    stageImg.style.transform = `scale(${1 + level * 0.05}) translateY(${-level * 4}px)`;
  }

  /** Play cloned-voice audio, lip-synced video, or browser TTS - whichever is available. */
  async function speakAndPlay(text, { video, isCurrent = () => true }) {
    stopPlayback();
    callStatus.textContent = video && config.features.video ? "Rendering video…" : "Generating voice…";
    let result = { audioUrl: null, videoUrl: null };
    try {
      result = await api(`/api/avatars/${id}/speak`, { method: "POST", body: { text, video } });
    } catch (err) {
      console.warn("speak failed, falling back to browser voice", err);
    }
    if (!isCurrent()) return; // the user spoke again while this was rendering
    callStatus.textContent = result.videoError ? "Video failed - voice only" : "";
    stage.classList.add("speaking");
    try {
      if (result.videoUrl) await playVideo(result.videoUrl);
      else if (result.audioUrl) await playAudio(result.audioUrl);
      else await playBrowserVoice(text);
    } finally {
      stage.classList.remove("speaking");
      animatePortrait(0);
    }
  }

  function playVideo(url) {
    return new Promise((resolve) => {
      stageVideo.src = url;
      stageVideo.classList.remove("hidden");
      const done = () => { stageVideo.classList.add("hidden"); stageVideo.pause(); resolve(); };
      stageVideo.onended = done;
      stageVideo.onerror = done;
      stopPlayback = done;
      stageVideo.play().catch(done);
    });
  }

  function playAudio(url) {
    return new Promise((resolve) => {
      audioCtx ??= new AudioContext();
      const audio = new Audio(url);
      const src = audioCtx.createMediaElementSource(audio);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser).connect(audioCtx.destination);
      const buf = new Uint8Array(analyser.fftSize);
      let raf;
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += ((v - 128) / 128) ** 2;
        animatePortrait(Math.min(1, Math.sqrt(sum / buf.length) * 4));
        raf = requestAnimationFrame(tick);
      };
      const done = () => { cancelAnimationFrame(raf); audio.pause(); src.disconnect(); resolve(); };
      audio.onended = done;
      audio.onerror = done;
      stopPlayback = done;
      audioCtx.resume().then(() => audio.play()).then(tick, done);
    });
  }

  function playBrowserVoice(text) {
    return new Promise((resolve) => {
      if (!("speechSynthesis" in window)) return resolve();
      const u = new SpeechSynthesisUtterance(text);
      let raf;
      const tick = () => { animatePortrait(0.3 + Math.random() * 0.5); raf = setTimeout(tick, 110); };
      const done = () => { clearTimeout(raf); speechSynthesis.cancel(); resolve(); };
      u.onstart = tick;
      u.onend = done;
      u.onerror = done;
      stopPlayback = done;
      speechSynthesis.speak(u);
    });
  }

  // ----- video call -----
  const talkBtn = $(".talk", root);
  const startBtn = $(".start-call", root);
  const endBtn = $(".end-call", root);
  const camBtn = $(".camera", root);
  const selfview = $(".selfview", root);
  const callComposer = $(".call-composer", root);
  let micStream, camStream, recorder, chunks = [], recognition, inCall = false, turnSeq = 0;
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const useServerStt = () => config.features.voice;

  /** New input always wins: it cuts off the avatar mid-sentence and supersedes any reply in flight. */
  async function takeTurn(text) {
    if (!text) return;
    const seq = ++turnSeq;
    const isCurrent = () => seq === turnSeq && inCall;
    stopPlayback();
    callStatus.textContent = "Thinking…";
    setCaption("");
    const reply = await converse(text, "voice");
    if (!isCurrent()) return;
    callStatus.textContent = "";
    if (reply) await speakAndPlay(reply, { video: true, isCurrent });
    if (isCurrent()) callStatus.textContent = "";
  }

  async function startTalking() {
    if (!inCall || talkBtn.classList.contains("recording")) return;
    stopPlayback();
    talkBtn.classList.add("recording");
    talkBtn.textContent = "Listening… release to send";
    if (useServerStt()) {
      chunks = [];
      recorder = new MediaRecorder(micStream);
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.start();
    } else if (SpeechRec) {
      recognition = new SpeechRec();
      recognition.interimResults = true;
      recognition.continuous = true;
      recognition.lang = navigator.language || "en-US";
      recognition.transcript = "";
      recognition.onresult = (e) => {
        recognition.transcript = Array.from(e.results).map((r) => r[0].transcript).join(" ");
        setCaption(`You: ${recognition.transcript}`);
      };
      recognition.start();
    }
  }

  async function stopTalking() {
    if (!talkBtn.classList.contains("recording")) return;
    talkBtn.classList.remove("recording");
    talkBtn.textContent = "Hold to talk";
    let text = "";
    if (recorder && recorder.state !== "inactive") {
      const stopped = new Promise((r) => (recorder.onstop = r));
      recorder.stop();
      await stopped;
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (blob.size < 2000) return; // tap, not speech
      callStatus.textContent = "Transcribing…";
      const form = new FormData();
      form.append("audio", blob, "mic.webm");
      try {
        text = (await api("/api/transcribe", { method: "POST", body: form })).text;
      } catch (err) {
        callStatus.textContent = "";
        setCaption(`Couldn't transcribe: ${err.message}`);
        return;
      }
    } else if (recognition) {
      const ended = new Promise((r) => (recognition.onend = r));
      recognition.stop();
      await ended;
      text = recognition.transcript;
    }
    await takeTurn(text.trim());
  }

  startBtn.addEventListener("click", async () => {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      micStream = null; // typed input still works
    }
    inCall = true;
    startBtn.classList.add("hidden");
    endBtn.classList.remove("hidden");
    camBtn.classList.remove("hidden");
    callComposer.classList.remove("hidden");
    const canTalk = micStream && (useServerStt() || SpeechRec);
    talkBtn.classList.toggle("hidden", !canTalk);
    if (!canTalk) setCaption("Mic unavailable - type below to talk.");
    const greeting = history.length ? `Hey, I'm back. Where were we?` : avatar.persona?.greeting;
    if (greeting) {
      const seq = ++turnSeq;
      setCaption(greeting);
      await speakAndPlay(greeting, { video: true, isCurrent: () => seq === turnSeq && inCall });
    }
  });

  function endCall() {
    inCall = false;
    turnSeq++;
    stopPlayback();
    inflight?.abort();
    recorder?.state === "recording" && recorder.stop();
    recognition?.abort?.();
    micStream?.getTracks().forEach((t) => t.stop());
    camStream?.getTracks().forEach((t) => t.stop());
    camStream = null;
    selfview.classList.add("hidden");
    camBtn.textContent = "Camera on";
    startBtn.classList.remove("hidden");
    [endBtn, camBtn, talkBtn, callComposer].forEach((el) => el.classList.add("hidden"));
    setCaption("");
    callStatus.textContent = "";
  }
  endBtn.addEventListener("click", endCall);

  camBtn.addEventListener("click", async () => {
    if (camStream) {
      camStream.getTracks().forEach((t) => t.stop());
      camStream = null;
      selfview.classList.add("hidden");
      camBtn.textContent = "Camera on";
      return;
    }
    try {
      camStream = await navigator.mediaDevices.getUserMedia({ video: true });
      selfview.srcObject = camStream;
      selfview.classList.remove("hidden");
      camBtn.textContent = "Camera off";
    } catch (err) {
      setCaption(`Camera unavailable: ${err.message}`);
    }
  });

  talkBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); startTalking(); });
  talkBtn.addEventListener("pointerup", stopTalking);
  talkBtn.addEventListener("pointerleave", stopTalking);
  const isTyping = () => ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
  const onKeyDown = (e) => { if (e.code === "Space" && inCall && !e.repeat && !isTyping()) { e.preventDefault(); startTalking(); } };
  const onKeyUp = (e) => { if (e.code === "Space" && inCall && !isTyping()) { e.preventDefault(); stopTalking(); } };
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("keyup", onKeyUp);

  callComposer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = e.target.text.value.trim();
    e.target.text.value = "";
    await takeTurn(text);
  });

  // ----- side actions -----
  $(".delete", root).addEventListener("click", async () => {
    if (!confirm(`Delete ${avatar.name}'s avatar, cloned voice, and all their media? This can't be undone.`)) return;
    await api(`/api/avatars/${id}`, { method: "DELETE" });
    storage.del(historyKey);
    location.hash = "#/";
  });
  $(".retrain", root).addEventListener("click", async () => {
    await api(`/api/avatars/${id}/retrain`, { method: "POST", body: {} });
    clearTimeout(pollTimer);
    refresh();
  });

  cleanupView = () => {
    clearTimeout(pollTimer);
    endCall();
    document.removeEventListener("keydown", onKeyDown);
    document.removeEventListener("keyup", onKeyUp);
  };

  await refresh();
}

// ---------- boot ----------
config = await api("/api/config");
const f = config.features;
$("#modes").innerHTML = [
  ["LLM persona", f.llm],
  ["Voice clone", f.voice],
  ["Lip-sync video", f.video],
].map(([label, on]) => `<span class="mode ${on ? "on" : ""}" title="${on ? "connected" : "not reachable - using fallback"}">${label}${on ? "" : " (off)"}</span>`).join("");
route();
