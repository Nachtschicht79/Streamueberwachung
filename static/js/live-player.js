(function () {
  const MANIFEST_POLL_MS = 4000;
  const PLAYER_POLL_MS = 1000;

  function proxyUrl(url) {
    const raw = String(url || "").trim();
    if (raw.indexOf("/api/hls/proxy/") === 0) return raw;
    let name = "playlist.m3u8";
    try {
      const parsed = new URL(raw);
      const tail = parsed.pathname.split("/").pop();
      if (tail) name = tail;
    } catch (_err) { /* Platzhalter-Dateiname reicht. */ }
    return "/api/hls/proxy/" + encodeURIComponent(name) + "?url=" + encodeURIComponent(raw);
  }

  function isHttpUrl(url) {
    return /^https?:\/\//i.test(String(url || "").trim());
  }

  function $(root, selector) {
    return root.querySelector(selector);
  }

  function setText(node, value) {
    if (node) node.textContent = value;
  }

  function formatBitrate(bps) {
    if (!Number.isFinite(bps) || bps <= 0) return "–";
    if (bps >= 1000000) return (bps / 1000000).toFixed(1) + " Mbit/s";
    return Math.round(bps / 1000) + " kbit/s";
  }

  function formatLatency(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "–";
    if (seconds < 1) return Math.round(seconds * 1000) + " ms";
    return seconds.toFixed(1) + " s";
  }

  function shouldUseNativeHls() {
    const probe = document.createElement("video");
    const canNative = probe.canPlayType("application/vnd.apple.mpegurl") !== "";
    const hlsJs = window.Hls && window.Hls.isSupported();
    if (canNative && !hlsJs) return true;
    const ua = navigator.userAgent || "";
    const safari = /Safari/.test(ua) && !/Chrome|Chromium|Android|CriOS|FxiOS|Edg/.test(ua);
    return Boolean(canNative && safari);
  }

  function parsePlaylist(text) {
    const lines = String(text || "").split(/\r?\n/);
    let targetDuration = null;
    let isMaster = false;
    let isLive = true;
    let segments = 0;
    let variantUrl = "";
    let pendingVariant = false;
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i].trim();
      if (!line) continue;
      if (line.startsWith("#EXT-X-TARGETDURATION:")) {
        const parsed = Number(line.slice("#EXT-X-TARGETDURATION:".length));
        if (Number.isFinite(parsed)) targetDuration = parsed;
      } else if (line.startsWith("#EXT-X-STREAM-INF")) {
        isMaster = true;
        pendingVariant = true;
      } else if (line === "#EXT-X-ENDLIST") {
        isLive = false;
      } else if (line.startsWith("#EXTINF")) {
        segments += 1;
      } else if (!line.startsWith("#") && pendingVariant && !variantUrl) {
        variantUrl = line;
        pendingVariant = false;
      }
    }
    return {
      isMaster: isMaster,
      isLive: isLive,
      segments: segments,
      targetDuration: targetDuration,
      variantUrl: variantUrl
    };
  }

  function StreamPane(root, controller) {
    this.root = root;
    this.controller = controller;
    this.video = $(root, "video");
    this.muteBtn = $(root, "[data-mute]");
    this.empty = $(root, "[data-empty]");
    this.playerWrap = $(root, "[data-player-wrap]");
    this.hls = null;
    this.engine = "";
    this.stalls = 0;
    this.fragments = 0;
    this.hasPlayed = false;
    this.playerError = "";
    this.manifestDelay = 0;
    this.manifestTimer = 0;
    this.playerTimer = 0;
    this.url = "";
    this.mediaPlaylistUrl = "";
    this.nativeLimited = false;
    this._onWaiting = this._onWaiting.bind(this);
    this._onPlaying = this._onPlaying.bind(this);
    this._onError = this._onError.bind(this);
    this.muteBtn.addEventListener("click", function () {
      this.setMuted(!this.video.muted);
    }.bind(this));
  }

  StreamPane.prototype._onWaiting = function () {
    if (this.hasPlayed) this.stalls += 1;
    this.renderPlayer();
  };

  StreamPane.prototype._onPlaying = function () {
    this.hasPlayed = true;
    this.playerError = "";
    this.renderPlayer();
  };

  StreamPane.prototype._onError = function () {
    this.playerError = "Wiedergabefehler";
    this.renderPlayer();
  };

  StreamPane.prototype.setMuted = function (muted) {
    this.video.muted = muted;
    if (!muted) this.controller.unmuteOnly(this);
    this.muteBtn.textContent = muted ? "Ton an" : "Ton aus";
  };

  StreamPane.prototype.resetMetrics = function () {
    this.stalls = 0;
    this.fragments = 0;
    this.hasPlayed = false;
    this.playerError = "";
    this.engine = "";
    this.nativeLimited = false;
    this.renderManifest({
      ok: false,
      state: "–",
      live: false,
      kind: "",
      latency: "–",
      segments: "–",
      target: "–",
      details: ""
    });
    this.renderPlayer();
  };

  StreamPane.prototype.showEmpty = function (message) {
    this.empty.textContent = message;
    this.empty.classList.remove("hidden");
    this.video.classList.add("hidden");
    this.muteBtn.disabled = true;
  };

  StreamPane.prototype.showVideo = function () {
    this.empty.classList.add("hidden");
    this.video.classList.remove("hidden");
    this.muteBtn.disabled = false;
  };

  StreamPane.prototype.renderManifest = function (data) {
    const status = $(this.root, "[data-manifest-status]");
    status.classList.toggle("is-ok", !!data.ok);
    status.classList.toggle("is-error", data.ok === false && data.state !== "–");
    setText($(this.root, "[data-manifest-state]"), data.state);
    const live = $(this.root, "[data-manifest-live]");
    live.hidden = !data.live;
    const kind = $(this.root, "[data-manifest-kind]");
    kind.hidden = !data.kind;
    setText(kind, data.kind);
    setText($(this.root, "[data-manifest-latency]"), data.latency);
    setText($(this.root, "[data-manifest-segments]"), data.segments);
    setText($(this.root, "[data-manifest-target]"), data.target);
    setText($(this.root, "[data-manifest-details]"), data.details);
  };

  StreamPane.prototype.renderPlayer = function () {
    let state = "Bereit";
    const video = this.video;
    if (this.playerError) state = "Fehler";
    else if (!this.url) state = "–";
    else if (video.readyState < 2 && !video.paused) state = "Puffer";
    else if (!video.paused && !video.ended) state = "Spielt";
    else if (this.hasPlayed) state = "Pausiert";
    const status = $(this.root, "[data-player-status]");
    status.classList.toggle("is-ok", state === "Spielt");
    status.classList.toggle("is-error", state === "Fehler");
    setText($(this.root, "[data-player-state]"), state);
    const engine = $(this.root, "[data-player-engine]");
    engine.hidden = !this.engine;
    setText(engine, this.engine);
    const width = video.videoWidth;
    const height = video.videoHeight;
    setText(
      $(this.root, "[data-player-resolution]"),
      width && height ? width + "\u00d7" + height : "–"
    );
    let bandwidth = "–";
    let liveLatency = "–";
    if (this.hls) {
      bandwidth = formatBitrate(this.hls.bandwidthEstimate);
      if (typeof this.hls.latency === "number") liveLatency = formatLatency(this.hls.latency);
    }
    setText($(this.root, "[data-player-bandwidth]"), bandwidth);
    setText($(this.root, "[data-player-live-latency]"), liveLatency);
    setText($(this.root, "[data-player-stalls]"), this.url ? String(this.stalls) : "–");
    setText($(this.root, "[data-player-fragments]"), this.engine === "hls.js" ? String(this.fragments) : this.url ? "0" : "–");
    const hint = $(this.root, "[data-player-hint]");
    if (this.playerError) {
      hint.hidden = false;
      hint.textContent = this.playerError;
    } else if (this.nativeLimited) {
      hint.hidden = false;
      hint.textContent = "Eingeschränkte Metriken (Native HLS)";
    } else {
      hint.hidden = true;
      hint.textContent = "Eingeschränkte Metriken (Native HLS)";
    }
  };

  StreamPane.prototype.stop = function () {
    window.clearTimeout(this.manifestDelay);
    window.clearInterval(this.manifestTimer);
    window.clearInterval(this.playerTimer);
    this.manifestDelay = 0;
    this.manifestTimer = 0;
    this.playerTimer = 0;
    this.video.removeEventListener("waiting", this._onWaiting);
    this.video.removeEventListener("playing", this._onPlaying);
    this.video.removeEventListener("error", this._onError);
    if (this.hls) {
      this.hls.destroy();
      this.hls = null;
    }
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    this.url = "";
    this.mediaPlaylistUrl = "";
    this.resetMetrics();
    this.setMuted(true);
    this.showEmpty("Kein Stream");
  };

  StreamPane.prototype.start = function (url) {
    this.stop();
    this.url = String(url || "").trim();
    if (!this.url) {
      this.showEmpty("Keine Stream-URL hinterlegt");
      this.renderManifest({
        ok: false,
        state: "–",
        live: false,
        kind: "",
        latency: "–",
        segments: "–",
        target: "–",
        details: "Keine URL in den Einstellungen."
      });
      return;
    }
    if (!isHttpUrl(this.url)) {
      this.showEmpty("Dieser Player spielt nur HLS/HTTP. RTMP wird nur von der Überwachung ausgewertet.");
      this.renderManifest({
        ok: false,
        state: "Nicht unterstützt",
        live: false,
        kind: "",
        latency: "–",
        segments: "–",
        target: "–",
        details: "Browser-Wiedergabe benötigt eine http(s)-HLS-Adresse."
      });
      return;
    }

    this.showVideo();
    this.video.muted = true;
    this.video.playsInline = true;
    this.setMuted(true);
    this.video.addEventListener("waiting", this._onWaiting);
    this.video.addEventListener("playing", this._onPlaying);
    this.video.addEventListener("error", this._onError);

    const source = proxyUrl(this.url);
    if (shouldUseNativeHls()) {
      this.engine = "Native HLS";
      this.nativeLimited = true;
      this.video.src = source;
    } else if (window.Hls && window.Hls.isSupported()) {
      this.engine = "hls.js";
      this.nativeLimited = false;
      this.hls = new window.Hls({
        enableWorker: true,
        lowLatencyMode: true,
        liveSyncDurationCount: 3
      });
      this.hls.on(window.Hls.Events.LEVEL_LOADED, function (_event, data) {
        if (data && data.details && data.details.url) {
          this.mediaPlaylistUrl = data.details.url;
        }
      }.bind(this));
      this.hls.on(window.Hls.Events.FRAG_LOADED, function () {
        this.fragments += 1;
      }.bind(this));
      this.hls.on(window.Hls.Events.ERROR, function (_event, data) {
        if (!data) return;
        if (!data.fatal) return;
        const detail = data.details || "Wiedergabefehler";
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && this.hls) {
          try {
            this.hls.recoverMediaError();
            this.playerError = "";
            this.renderPlayer();
            return;
          } catch (_err) { /* Fallback auf Fehlerstatus. */ }
        }
        this.playerError = detail;
        this.renderPlayer();
      }.bind(this));
      this.hls.loadSource(source);
      this.hls.attachMedia(this.video);
    } else {
      this.showEmpty("Dieser Browser kann HLS nicht wiedergeben.");
      return;
    }

    const playAttempt = this.video.play();
    if (playAttempt && typeof playAttempt.catch === "function") {
      playAttempt.catch(function () { /* Autoplay darf still scheitern. */ });
    }

    const self = this;
    this.manifestDelay = window.setTimeout(function () {
      if (!self.url) return;
      self.pollManifest();
      self.manifestTimer = window.setInterval(self.pollManifest.bind(self), MANIFEST_POLL_MS);
    }, 600);
    this.playerTimer = window.setInterval(this.renderPlayer.bind(this), PLAYER_POLL_MS);
    this.renderPlayer();
  };

  StreamPane.prototype.pollManifest = async function () {
    if (!this.url || !isHttpUrl(this.url)) return;
    const started = performance.now();
    const inspectTarget = this.mediaPlaylistUrl
      ? (this.mediaPlaylistUrl.indexOf("/api/hls/proxy/") >= 0
        ? this.mediaPlaylistUrl.slice(this.mediaPlaylistUrl.indexOf("/api/hls/proxy/"))
        : this.mediaPlaylistUrl)
      : proxyUrl(this.url);
    try {
      const first = await fetch(inspectTarget, { cache: "no-store" });
      const latencyMs = Math.max(0, Math.round(performance.now() - started));
      if (!first.ok) {
        throw new Error("HTTP " + first.status);
      }
      let text = await first.text();
      let info = parsePlaylist(text);
      let kind = info.isMaster ? "master" : "media";
      if (info.isMaster && info.variantUrl) {
        const nextUrl = info.variantUrl.indexOf("/api/hls/proxy") === 0
          ? info.variantUrl
          : proxyUrl(info.variantUrl);
        const media = await fetch(nextUrl, { cache: "no-store" });
        if (media.ok) {
          text = await media.text();
          info = parsePlaylist(text);
          kind = "media";
        }
      }
      const liveLabel = info.isLive ? "Live" : "VOD";
      const details = kind === "media"
        ? "Media OK (" + info.segments + " Segmente, " + liveLabel + ")"
        : "Master-Playlist";
      this.renderManifest({
        ok: true,
        state: "OK",
        live: info.isLive,
        kind: kind,
        latency: latencyMs + " ms",
        segments: String(info.segments),
        target: info.targetDuration === null ? "–" : info.targetDuration + " s",
        details: details
      });
    } catch (err) {
      this.renderManifest({
        ok: false,
        state: "Fehler",
        live: false,
        kind: "",
        latency: "–",
        segments: "–",
        target: "–",
        details: err && err.message ? err.message : "Manifest nicht lesbar"
      });
    }
  };

  function createController() {
    const modal = document.getElementById("live-modal");
    const backdrop = document.getElementById("live-backdrop");
    const btnOpen = document.getElementById("btn-open-live");
    const btnClose = document.getElementById("btn-close-live");
    const masterPane = new StreamPane(document.querySelector('[data-stream="master"]'), null);
    const backupPane = new StreamPane(document.querySelector('[data-stream="backup"]'), null);
    const panes = [masterPane, backupPane];
    const controller = {
      unmuteOnly: function (pane) {
        panes.forEach(function (other) {
          if (other !== pane) other.setMuted(true);
        });
      }
    };
    masterPane.controller = controller;
    backupPane.controller = controller;
    let open = false;
    let lastFocus = null;

    function setOpen(next) {
      open = next;
      modal.classList.toggle("is-open", next);
      backdrop.classList.toggle("is-open", next);
      backdrop.hidden = !next;
      document.body.classList.toggle("is-modal-open", next);
      btnOpen.setAttribute("aria-expanded", next ? "true" : "false");
      modal.setAttribute("aria-hidden", next ? "false" : "true");
      if (next) {
        modal.removeAttribute("inert");
        btnClose.focus();
      } else {
        modal.setAttribute("inert", "");
        panes.forEach(function (pane) { pane.stop(); });
        if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
      }
    }

    const api = {
      isOpen: function () { return open; },
      open: function (settings) {
        const data = settings || {};
        if (!open) {
          lastFocus = document.activeElement;
          setOpen(true);
        }
        masterPane.start(data.stream_url || "");
        backupPane.start(data.backup_stream_url || "");
      },
      close: function () {
        if (open) setOpen(false);
      }
    };

    btnClose.addEventListener("click", api.close);
    backdrop.addEventListener("click", api.close);
    return api;
  }

  window.LiveStreams = createController();
})();
