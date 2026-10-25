/* Cutoff video recorder, v3: frame-stepped, glitch-free rendering.
 * Pages call Rec.init({ title, slug, duration, cues:[{t, say}], build(tl), prepare? }).
 *
 * Render (one share dialog: "Entire screen" + "Share system audio", with the page in F11 full screen):
 *   Pass 1, voice (real time, 58 s): the timeline plays and each cue is spoken by the browser's
 *     neural voice; only the system audio is recorded.
 *   Pass 2, picture (frame by frame, about 2 minutes): the timeline is paused and stepped exactly
 *     1/30 s at a time. After each step the page settles, one fresh captured frame is cropped to the
 *     stage and encoded with WebCodecs (H.264), with an exact timestamp. Nothing depends on how fast
 *     the laptop is, so there are no dropped or doubled frames.
 *   The voice is encoded (AAC, else Opus) and both tracks are muxed into one MP4 (mp4-muxer).
 * Browser APIs plus two JS libraries from a CDN (GSAP, mp4-muxer). Nothing is installed. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const raf2 = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
                  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } } };
  const FPS = 30;
  const PREFERRED = ["Andrew", "Ava", "Brian", "Emma", "Aria", "Guy", "Jenny", "Christopher", "Michelle", "Steffan"];
  const Rec = {};
  let opt, tl, stage, voices = [], timing = {}, mute = false;

  function fit() {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    stage.style.transform = `translate(-50%, -50%) scale(${s})`;
  }
  function status(msg) { $("#ctl .status").textContent = msg; }
  const plainText = (s) => String(s || "").replace(/\*/g, "");
  const capHtml = (s) => String(s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])).replace(/\*([^*]+)\*/g, "<em>$1</em>");

  // ---------------------------------------------------------------- voice
  function loadVoices() {
    voices = (speechSynthesis.getVoices() || []).filter((v) => /^en/i.test(v.lang));
    const score = (v) => {
      let s = 0;
      if (/Natural/i.test(v.name)) s += 100;
      if (/Online/i.test(v.name)) s += 20;
      if (/en-US/i.test(v.lang)) s += 10;
      const i = PREFERRED.findIndex((p) => v.name.includes(p));
      if (i >= 0) s += 50 - i;
      return s;
    };
    voices.sort((a, b) => score(b) - score(a));
    const sel = $("#voice");
    if (!sel) return;
    const want = store.get(`cutoff.voice.${opt.slug}`, opt.voice || "");
    sel.innerHTML = voices.length ? voices.map((v, i) => `<option value="${i}">${v.name.replace(/ - English.*$/, "")} (${v.lang})</option>`).join("")
      : `<option value="-1">No English voice found</option>`;
    const wi = voices.findIndex((v) => want && v.name.includes(want));
    if (wi >= 0) sel.value = wi;
  }
  const curVoice = () => voices[+($("#voice") || {}).value] || null;
  const timingKey = () => `cutoff.timing2.${opt.slug}.${(curVoice() || {}).name || "none"}`;

  function speakCue(i) {
    if (mute || !$("#speak").checked || !window.speechSynthesis) return;
    const c = opt.cues[i];
    if (!c || !c.say) return;
    const slot = ((opt.cues[i + 1] || {}).t ?? opt.duration) - c.t - 0.25;
    const base = +$("#rate").value;
    const fac = timing[i] && timing[i].dur ? Math.max(1, (timing[i].dur * (timing[i].rate / base)) / slot) : 1;
    const u = new SpeechSynthesisUtterance(plainText(c.say));
    const v = curVoice();
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = Math.min(1.6, base * fac);
    // Never cancel: in Edge a speak() right after cancel() is often dropped, which skipped whole lines.
    // A line that is still talking just delays the next one; timing is measured from when the line was
    // due, so any delay counts and that line is sped up on the next run.
    const due = performance.now();
    let started = false;
    u.onstart = () => { started = true; };
    u.onend = () => {
      if (!started) return;
      timing[i] = { dur: (performance.now() - due) / 1000, rate: u.rate, slot };
      store.set(timingKey(), timing);
      reportTiming();
    };
    speechSynthesis.resume();
    speechSynthesis.speak(u);
  }
  function reportTiming() {
    const over = opt.cues.map((c, i) => [i, timing[i]]).filter(([, t]) => t && t.dur > t.slot + 0.05);
    const known = Object.keys(timing).length;
    $("#timing").textContent = !known ? "" : over.length
      ? `Voice timing: ${over.length} line(s) ran long and will be sped up next run (${over.map(([i, t]) => `#${i + 1} ${t.dur.toFixed(1)}s/${t.slot.toFixed(1)}s`).join(", ")}). Preview again, then render.`
      : `Voice timing: all ${known} measured lines fit.`;
  }

  // ---------------------------------------------------------------- timeline
  async function build() {
    if (tl) tl.kill();
    gsap.globalTimeline.getChildren().forEach((c) => c.kill());
    if (window.speechSynthesis && (speechSynthesis.speaking || speechSynthesis.pending)) speechSynthesis.cancel();
    $("#captions").innerHTML = "";
    if (opt.prepare) { status("Preparing…"); await opt.prepare(); }
    tl = gsap.timeline({ paused: true });
    opt.build(tl, stage);
    // captions live on the timeline, so they step frame-accurately too
    opt.cues.forEach((c, i) => {
      tl.call(() => { $("#captions").innerHTML = c.say ? `<span>${capHtml(c.say)}</span>` : ""; speakCue(i); }, null, c.t);
      tl.fromTo("#captions", { opacity: 0, y: 12 }, { opacity: 1, y: 0, duration: .3, ease: "power2.out", immediateRender: false }, c.t);
    });
    tl.set({}, {}, opt.duration);
    tl.eventCallback("onUpdate", () => { const r = $("#scrub"); if (r && document.activeElement !== r) r.value = tl.time(); });
    status(`Ready · ${opt.duration}s`);
  }
  async function preview() {
    $("#play").disabled = true;
    mute = false;
    await build();
    tl.play(0);
    tl.eventCallback("onComplete", () => { $("#play").disabled = false; status("Preview finished."); });
  }

  // ---------------------------------------------------------------- render
  async function countdown() {
    const el = $("#countdown");
    el.style.display = "grid";
    for (const n of [3, 2, 1]) { el.textContent = n; await wait(800); }
    el.style.display = "none";
    await raf2();
  }

  async function voicePass(atrack) {
    mute = false;
    await build();
    await countdown();
    const mt = ["audio/webm;codecs=opus", "audio/webm", ""].find((m) => !m || MediaRecorder.isTypeSupported(m));
    const rec = new MediaRecorder(new MediaStream([atrack]), mt ? { mimeType: mt, audioBitsPerSecond: 256000 } : {});
    const chunks = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    const stopped = new Promise((r) => { rec.onstop = r; });
    rec.start(500);
    tl.play(0);
    await new Promise((r) => tl.eventCallback("onComplete", r));
    await wait(400);
    rec.stop();
    await stopped;
    speechSynthesis.cancel();
    const ac = new AudioContext({ sampleRate: 48000 });
    const buf = await ac.decodeAudioData(await new Blob(chunks).arrayBuffer());
    ac.close();
    let peak = 0;
    for (let c = 0; c < buf.numberOfChannels; c++) { const d = buf.getChannelData(c); for (let i = 0; i < d.length; i += 8) peak = Math.max(peak, Math.abs(d[i])); }
    return { buf, peak };
  }

  async function render() {
    if (typeof Mp4Muxer === "undefined" || typeof VideoEncoder === "undefined" || typeof MediaStreamTrackProcessor === "undefined") {
      status("This browser lacks WebCodecs. Use a current Microsoft Edge."); return;
    }
    let display;
    try {
      display = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 60 }, displaySurface: "monitor" },
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, suppressLocalAudioPlayback: false },
        systemAudio: "include", monitorTypeSurfaces: "include", selfBrowserSurface: "include", surfaceSwitching: "exclude",
      });
    } catch (e) { status("Screen capture was cancelled."); return; }
    const vtrack = display.getVideoTracks()[0], atrack = display.getAudioTracks()[0];
    const surface = (vtrack.getSettings() || {}).displaySurface || "monitor";
    document.body.classList.add("recording");
    fit();
    const t0 = performance.now();
    try {
      // ---- pass 1: voice
      let voice = null;
      if ($("#speak").checked && atrack) { document.title = "● recording voice…"; voice = await voicePass(atrack); }

      // ---- pass 2: picture, frame by frame
      mute = true;
      if (window.speechSynthesis) speechSynthesis.cancel();
      await build();
      tl.pause(0);
      const reader = new MediaStreamTrackProcessor({ track: vtrack }).readable.getReader();
      let latest = null, count = 0, live = true;
      (async () => { while (live) { const { value, done } = await reader.read(); if (done) break; if (latest) latest.close(); latest = value; count++; } })();
      const canvas = new OffscreenCanvas(1920, 1080), ctx = canvas.getContext("2d");
      const hasAudio = !!(voice && voice.peak > 0.002);
      let acodec = null;
      if (hasAudio) {
        for (const [c, mc] of [["mp4a.40.2", "aac"], ["opus", "opus"]]) {
          const ok = await AudioEncoder.isConfigSupported({ codec: c, sampleRate: 48000, numberOfChannels: voice.buf.numberOfChannels, bitrate: 192000 }).catch(() => ({}));
          if (ok.supported) { acodec = [c, mc]; break; }
        }
      }
      let vcodec = null;
      for (const c of ["avc1.640028", "avc1.4d0028", "avc1.42003e"]) {
        const ok = await VideoEncoder.isConfigSupported({ codec: c, width: 1920, height: 1080, bitrate: 16e6, framerate: FPS }).catch(() => ({}));
        if (ok.supported) { vcodec = c; break; }
      }
      if (!vcodec) throw new Error("No H.264 encoder available");
      const muxer = new Mp4Muxer.Muxer({
        target: new Mp4Muxer.ArrayBufferTarget(), fastStart: "in-memory", firstTimestampBehavior: "offset",
        video: { codec: "avc", width: 1920, height: 1080, frameRate: FPS },
        audio: acodec ? { codec: acodec[1], numberOfChannels: voice.buf.numberOfChannels, sampleRate: 48000 } : undefined,
      });
      let encErr = null;
      const venc = new VideoEncoder({ output: (ch, meta) => muxer.addVideoChunk(ch, meta), error: (e) => { encErr = e; } });
      venc.configure({ codec: vcodec, width: 1920, height: 1080, bitrate: 16e6, framerate: FPS, latencyMode: "quality", avc: { format: "avc" } });

      const N = Math.round(opt.duration * FPS);
      for (let i = 0; i < N; i++) {
        if (encErr) throw encErr;
        tl.seek(Math.max(i / FPS, 0.0005), false);
        await new Promise((r) => requestAnimationFrame(r));                           // one paint is enough
        const c0 = count, start = performance.now();
        while (count < c0 + 1 && performance.now() - start < 45) await wait(2);      // first fresh frame (static frames time out fast)
        // (fast mode: no second wait)
        if (latest) {
          const vw = latest.displayWidth, r = stage.getBoundingClientRect();
          let sx, ox = 0, oy = 0;
          if (surface === "monitor") {
            sx = vw / screen.width;
            if (Math.abs(innerWidth - screen.width) > 3) {
              const border = (outerWidth - innerWidth) / 2;
              ox = (screenX - (screen.availLeft || 0)) + border; oy = (screenY - (screen.availTop || 0)) + (outerHeight - innerHeight) - border;
            }
          } else sx = vw / innerWidth;
          ctx.drawImage(latest, (ox + r.left) * sx, (oy + r.top) * sx, r.width * sx, r.height * sx, 0, 0, 1920, 1080);
        }
        const vf = new VideoFrame(canvas, { timestamp: Math.round(i * 1e6 / FPS), duration: Math.round(1e6 / FPS) });
        venc.encode(vf, { keyFrame: i % (FPS * 2) === 0 });
        vf.close();
        while (venc.encodeQueueSize > 6) await wait(5);
        if (i % 15 === 0) document.title = `● rendering ${Math.round(100 * i / N)}%`;
      }
      await venc.flush();
      live = false; try { reader.cancel(); } catch (e) { /* ignore */ }
      if (latest) latest.close();

      if (acodec) {
        const b = voice.buf, ch = b.numberOfChannels, total = Math.min(b.length, Math.round(opt.duration * 48000));
        const aenc = new AudioEncoder({ output: (c, meta) => muxer.addAudioChunk(c, meta), error: (e) => { encErr = e; } });
        aenc.configure({ codec: acodec[0], sampleRate: 48000, numberOfChannels: ch, bitrate: 192000 });
        const step = 1024;
        for (let off = 0; off < total; off += step) {
          const n = Math.min(step, total - off), data = new Float32Array(n * ch);
          for (let c = 0; c < ch; c++) data.set(b.getChannelData(c).subarray(off, off + n), c * n);
          const ad = new AudioData({ format: "f32-planar", sampleRate: 48000, numberOfFrames: n, numberOfChannels: ch, timestamp: Math.round(off * 1e6 / 48000), data });
          aenc.encode(ad); ad.close();
        }
        await aenc.flush();
      }
      muxer.finalize();
      const blob = new Blob([muxer.target.buffer], { type: "video/mp4" });
      const url = URL.createObjectURL(blob);
      const name = `cutoff-${opt.slug}${acodec ? "-voice" : "-silent"}.mp4`;
      const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
      $("#out").innerHTML = `<video src="${url}" controls></video><p>Saved <b>${name}</b> (${(blob.size / 1e6).toFixed(1)} MB). <a href="${url}" download="${name}">Download again</a></p>`;
      const audioNote = !$("#speak").checked ? "voice off"
        : !atrack ? "NO AUDIO: the share had no audio. Choose “Entire screen” and switch on “Share system audio”."
        : !hasAudio ? `AUDIO SILENT (peak ${voice ? voice.peak.toFixed(4) : 0}). System audio did not carry the voice.`
        : `Voice OK (${acodec[1].toUpperCase()}, peak ${(20 * Math.log10(voice.peak)).toFixed(1)} dBFS) · ${(curVoice() || {}).name || ""}`;
      status(`Done in ${Math.round((performance.now() - t0) / 1000)} s: ${name}\ncapture: ${surface} · ${vcodec} 1920x1080 ${FPS} fps, frame-stepped\n${audioNote}`);
      store.set(`cutoff.lastRender.${opt.slug}`, { at: new Date().toISOString(), surface, voice: (curVoice() || {}).name || "", audio: audioNote });
    } catch (e) {
      status("Render failed: " + (e && e.message || e));
    } finally {
      mute = false;
      display.getTracks().forEach((t) => t.stop());
      document.body.classList.remove("recording");
      document.title = opt.title;
    }
  }

  // ---------------------------------------------------------------- UI
  Rec.init = function (o) {
    opt = Object.assign({ duration: 58, cues: [], scrub: true }, o);
    stage = $("#stage");
    stage.insertAdjacentHTML("beforeend", `<div id="captions"></div><div id="countdown"></div>`);
    document.body.insertAdjacentHTML("beforeend", `
      <div id="ctl">
        <button class="min" title="Collapse">–</button>
        <h1>${opt.title}</h1>
        <p>${opt.help || ""}</p>
        <div class="row">
          <button id="play">▶ Preview (+ calibrate voice)</button>
          <button id="rec" class="primary">● Render MP4</button>
        </div>
        ${opt.scrub ? `<input id="scrub" type="range" min="0" max="${opt.duration}" step="0.05" value="0">` : ""}
        <label><input type="checkbox" id="speak" checked> AI voice (browser neural voice)</label>
        <label>Voice <select id="voice" style="flex:1;min-width:0"></select></label>
        <label>Speed <input id="rate" type="range" min="0.8" max="1.4" step="0.05" value="1.05" style="flex:1"> <span id="ratev">1.05</span></label>
        <div class="row"><button id="testv">Test voice</button><button id="resett">Reset timing</button></div>
        <label><input type="checkbox" id="caps" checked> Burned-in captions</label>
        <p><b>Render:</b> press <b>F11</b>. Share <b>Entire screen</b> with <b>Share system audio</b> on, click <b>Hide</b> on the sharing bar, and don't touch anything. First 58 s records the voice, then the picture is rendered frame by frame (about 2 to 3 min; progress shows in the tab title).</p>
        <div class="status"></div>
        <div class="status" id="timing" style="color:#fbbf24"></div>
        <div id="out"></div>
        <p><a href="index.html">← all videos</a></p>
      </div>`);
    fit();
    addEventListener("resize", fit);
    $("#play").onclick = preview;
    $("#rec").onclick = render;
    $("#caps").onchange = (e) => document.body.classList.toggle("no-captions", !e.target.checked);
    $("#ctl .min").onclick = () => $("#ctl").classList.toggle("collapsed");
    $("#rate").value = store.get(`cutoff.rate.${opt.slug}`, opt.rate || 1.05);
    $("#ratev").textContent = $("#rate").value;
    $("#rate").oninput = (e) => { $("#ratev").textContent = e.target.value; store.set(`cutoff.rate.${opt.slug}`, +e.target.value); };
    $("#voice").onchange = () => { const v = curVoice(); if (v) store.set(`cutoff.voice.${opt.slug}`, v.name); timing = store.get(timingKey(), {}); reportTiming(); };
    $("#testv").onclick = () => { speechSynthesis.cancel(); setTimeout(() => { const u = new SpeechSynthesisUtterance(plainText((opt.cues[0] || {}).say || "Cutoff.")); const v = curVoice(); if (v) { u.voice = v; u.lang = v.lang; } u.rate = +$("#rate").value; speechSynthesis.speak(u); }, 150); };
    $("#resett").onclick = () => { timing = {}; store.set(timingKey(), {}); reportTiming(); };
    if (opt.scrub) {
      $("#scrub").oninput = async (e) => {
        if (!tl) await build();
        tl.pause(); if (window.speechSynthesis) speechSynthesis.cancel();
        mute = true; tl.seek(+e.target.value, false); mute = false;
        $("#play").disabled = false;
      };
    }
    gsap.ticker.lagSmoothing(0);
    if (window.speechSynthesis) { loadVoices(); speechSynthesis.onvoiceschanged = () => { loadVoices(); timing = store.get(timingKey(), {}); reportTiming(); }; }
    timing = store.get(timingKey(), {});
    reportTiming();
    status(typeof VideoEncoder !== "undefined" ? "Ready. Frame-stepped MP4 rendering available." : "This browser lacks WebCodecs.");
    // show a real frame while idle instead of the un-started stage
    build().then(() => { mute = true; let t = 0; const end = opt.poster ?? 1.5; while (t < end) { t = Math.min(end, t + 0.1); tl.seek(t, false); } mute = false;
      status("Ready. Press Preview to watch, then Render MP4."); });
  };
  Rec._tl = () => tl;                       // debugging: step the timeline from the console
  Rec._mute = (m) => { mute = m; };
  window.Rec = Rec;
})();
