/* 爆款剪辑 (Viral Editorial)，1080×1920。极简访谈：白底黑字 headline card + 红字强调 +
   蓝色 word-level 高亮字幕 + 底部暗渐变 + 左下开角白线 + 右下角时间码。
   时间码/进度条由 ffmpeg 画（逐帧变，进 overlay 会强制每帧重采）。
   前 4 秒显示开场 headline card，之后只留字幕区。renderFrame(t) 只由 t 决定。 */
(function () {
  var data = null;

  function esc(value) {
    return String(value).replace(/[&<>]/g, function (ch) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[ch];
    });
  }

  function faceReady(node, face) {
    if (!face || !document.fonts || !document.fonts.check) return true;
    var sample = String(node && node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 32) || "Ag";
    try {
      if (document.fonts.check(face, sample)) return true;
    } catch (error) { /* older check() takes one argument */ }
    return !!document.fonts.check(face);
  }

  function lineWidth(node) {
    return typeof node.scrollWidth === "number" ? node.scrollWidth : 0;
  }

  function fit(node, start, floor, step, limit, face) {
    if (!node || typeof node.scrollWidth !== "number") return;
    if (!faceReady(node, face)) {
      node.style.fontSize = start + "px";
      return;
    }
    var size = start;
    node.style.fontSize = size + "px";
    var guard = 0;
    while (size > floor && lineWidth(node) > limit && guard < 60) {
      size -= step;
      node.style.fontSize = size + "px";
      guard += 1;
    }
  }

  // A moment shows 1–5 spoken words; each word is a {w, t0, t1} slot.
  function moments() {
    if (data.moments && data.moments.length) return data.moments;
    var words = data.words || [];
    var built = [];
    for (var i = 0; i < words.length; i++) {
      var word = words[i];
      var t0 = word.start_frame / (data.fps || 30);
      var t1 = word.end_frame / (data.fps || 30);
      built.push({
        t0: t0,
        t1: t1,
        zh: [{s: String(word.text || "").replace(/\n/g, ""), t0: t0, t1: t1, key: !!word.emphasis}],
        en: String(word.original || "").split(/\s+/).filter(Boolean).map(function (token) { return {w: token, t0: t0, t1: t1}; })
      });
    }
    return built;
  }

  function momentAt(t) {
    var list = moments();
    var found = null;
    for (var i = 0; i < list.length; i++) if (t >= list[i].t0 && t < list[i].t1) found = list[i];
    return found;
  }

  function activeWordIndex(moment, t) {
    // The last spoken word whose start has begun: that is the word on air now.
    var list = (moment && moment.zh) || [];
    var best = -1;
    for (var i = 0; i < list.length; i++) {
      if (t >= list[i].t0) best = i;
    }
    if (best >= 0 && t >= list[best].t1) best = -1;
    return best;
  }

  function capHtml(moment, t) {
    var html = "";
    var list = (moment && moment.zh) || [];
    var active = activeWordIndex(moment, t);
    for (var i = 0; i < list.length; i++) {
      var word = list[i];
      var cls = "w" + (i === active ? " now" : "");
      html += '<span class="' + cls + '">' + esc(word.s) + "</span>";
    }
    return html;
  }

  function enHtml(moment, t) {
    var html = "";
    var list = (moment && moment.en) || [];
    for (var i = 0; i < list.length; i++) html += (i ? " " : "") + esc(list[i].w || "");
    return html;
  }

  function titleLines() {
    // The opening card reads the dedicated viral slot; the painted overlay strips the
    // cover title, so this is the only source the card needs.
    var lines = data.viral_title_lines || [];
    if (!lines.length && data.hook && data.hook.text) lines = [data.hook.text];
    return lines.filter(function (line) { return String(line).trim(); });
  }

  function headlineHtml() {
    // Red sits on the accent line; the rest stays black.
    var lines = titleLines();
    var accent = 0;
    try { accent = Number(data.viral_accent) || 0; } catch (error) { accent = lines.length - 1; }
    var html = "";
    for (var i = 0; i < lines.length && i < 3; i++) {
      html += '<span class="ln' + (i === accent ? " acc" : "") + '">' + esc(lines[i]) + "</span>";
    }
    return html;
  }

  function brandLabel() {
    return String(data.brand_label || "Clips Central");
  }

  // The opening card: title lines + brand, shown for the first 4 seconds.
  function cardAt(t) {
    return t < 4;
  }

  function fitCard() {
    var block = document.getElementById("h-l");
    var nodes = block.children || [];
    var limit = 984;
    for (var n = 0; n < nodes.length; n++) fit(nodes[n], 60, 34, 2, limit, '900 60px "Noto Sans CJK SC"');
  }

  window.setData = function (fill) {
    data = fill || {};
    document.getElementById("brand").textContent = brandLabel();
    document.getElementById("h-l").innerHTML = headlineHtml();
    fitCard();
  };

  window.renderFrame = function (t) {
    var cardOn = cardAt(t);
    var cap = document.getElementById("cap");
    var frame = document.getElementById("frame");
    var card = document.getElementById("card");
    var zh = document.getElementById("zh");
    var en = document.getElementById("en");
    var digestCard = "card|";
    if (cardOn) {
      card.style.display = "";
      frame.style.display = "";
      cap.style.display = "none";
      digestCard = "card|" + t.toFixed(2);
    } else {
      card.style.display = "none";
      frame.style.display = "none";
      var moment = momentAt(t);
      if (moment) {
        cap.style.display = "";
        zh.innerHTML = capHtml(moment, t);
        en.innerHTML = enHtml(moment, t);
        var activeIndex = activeWordIndex(moment, t);
        if (activeIndex >= 0 && zh.children && zh.children[activeIndex]) fit(zh, 56, 40, 2, 960, '900 56px "Noto Sans CJK SC"');
      } else {
        cap.style.display = "none";
        zh.innerHTML = "";
        en.innerHTML = "";
      }
      digestCard = "cap|" + (moment ? capHtml(moment, t) : "") + "|" + en.innerHTML;
    }
    return digestCard;
  };

  window.occupiedRects = function (t) {
    var rects = [];
    if (cardAt(t)) {
      rects.push({slot: "hook", x: 40, y: 1440, w: 1000, h: 360});
      return rects;
    }
    var moment = momentAt(t);
    if (moment) rects.push({slot: "subtitle", x: 60, y: 1360, w: 960, h: 160});
    return rects;
  };
})();
