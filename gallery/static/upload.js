(function () {
  "use strict";

  // Upload page: file picking with previews, async submit, a polling
  // status queue (server-rendered HTML swapped in, same pattern as
  // search.js), multi-select redo, and delete.

  document.addEventListener("DOMContentLoaded", function () {
    var queueEl = document.getElementById("queue");
    if (!queueEl) return; // not the upload page

    var toolbar = document.getElementById("queue-toolbar");
    var selectedCountEl = document.getElementById("selected-count");
    var dialog = document.getElementById("redo-dialog");
    var pollTimer = null;
    var lastQueueHtml = queueEl.innerHTML;

    // ---- selection -------------------------------------------------------

    function selectedCards() {
      return Array.prototype.map.call(
        queueEl.querySelectorAll(".ucard-check:checked"),
        function (c) { return c.closest(".ucard"); }
      );
    }

    function syncToolbar() {
      var n = selectedCards().length;
      toolbar.hidden = n === 0;
      selectedCountEl.textContent = n + " selected";
    }

    // ---- queue rendering / polling ---------------------------------------

    function applyQueue(data) {
      if (data.queue_html !== lastQueueHtml) {
        // Re-apply checkbox state: the swap replaces the DOM wholesale.
        var checked = {};
        selectedCards().forEach(function (card) { checked[card.dataset.id] = true; });
        queueEl.innerHTML = data.queue_html;
        lastQueueHtml = data.queue_html;
        Object.keys(checked).forEach(function (id) {
          var card = queueEl.querySelector('.ucard[data-id="' + id + '"]');
          if (card) card.querySelector(".ucard-check").checked = true;
        });
        syncToolbar();
      }
      schedulePoll(data.processing > 0);
    }

    function refreshQueue() {
      fetch("/upload/status", { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(applyQueue)
        .catch(function () { schedulePoll(true); }); // transient — retry
    }

    function schedulePoll(active) {
      clearTimeout(pollTimer);
      if (active) pollTimer = setTimeout(refreshQueue, 2500);
    }

    if (parseInt(queueEl.dataset.processing, 10) > 0) schedulePoll(true);

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refreshQueue();
    });

    // ---- upload form -------------------------------------------------------

    var form = document.getElementById("upload-form");
    var input = document.getElementById("file-input");
    var drop = document.getElementById("file-drop");
    var previews = document.getElementById("file-previews");
    var btn = document.getElementById("upload-btn");
    var errsEl = document.getElementById("upload-errors");
    var enhanceOpt = document.getElementById("opt-enhance");
    var messageField = document.getElementById("message-field");

    function syncMessageField() {
      messageField.hidden = !enhanceOpt.checked;
    }
    enhanceOpt.addEventListener("change", syncMessageField);
    syncMessageField();

    function isHeic(file) {
      return /image\/hei[cf]/.test(file.type) || /\.hei[cf]$/i.test(file.name);
    }

    // Pending files live in this array, not in the input: the camera hands
    // over one shot at a time and pickers replace their selection, so both
    // are harvested into here (deduped) and the input cleared. Submit sends
    // this list. With JS off the input keeps its files and the form posts
    // natively, so nothing breaks.
    var fileStore = [];

    function fileKey(f) {
      return f.name + "|" + f.size + "|" + f.lastModified;
    }

    function addFiles(list) {
      Array.prototype.forEach.call(list, function (f) {
        var dup = fileStore.some(function (g) { return fileKey(g) === fileKey(f); });
        if (!dup) fileStore.push(f);
      });
      syncFiles();
    }

    function syncFiles() {
      previews.innerHTML = "";
      fileStore.forEach(function (f, i) {
        var item = document.createElement("span");
        item.className = "file-preview-item";
        if (isHeic(f)) {
          // Browsers can't render HEIC in an <img> — show a name chip.
          var chip = document.createElement("span");
          chip.className = "file-preview-chip";
          chip.textContent = f.name;
          item.appendChild(chip);
        } else {
          var img = document.createElement("img");
          img.className = "file-preview";
          img.src = URL.createObjectURL(f);
          img.onload = function () { URL.revokeObjectURL(img.src); };
          item.appendChild(img);
        }
        var rm = document.createElement("button");
        rm.type = "button";
        rm.className = "file-preview-remove";
        rm.setAttribute("aria-label", "Remove " + f.name);
        rm.textContent = "×";
        rm.addEventListener("click", function (e) {
          // The previews sit inside the file-drop <label>; stop the click
          // from also opening the file picker.
          e.preventDefault();
          e.stopPropagation();
          fileStore.splice(i, 1);
          syncFiles();
        });
        item.appendChild(rm);
        previews.appendChild(item);
      });
      btn.disabled = !fileStore.length;
      btn.textContent = fileStore.length
        ? "Upload & process (" + fileStore.length + ")"
        : "Upload & process";
    }

    input.addEventListener("change", function () {
      addFiles(input.files);
      input.value = ""; // harvested; also lets re-picking the same file fire change
    });

    // Mobile devices get a button that opens the camera directly
    // (capture=environment = rear camera), one shot at a time.
    var camBtn = document.getElementById("camera-btn");
    var camInput = document.getElementById("camera-input");
    if (/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) camBtn.hidden = false;
    camBtn.addEventListener("click", function () { camInput.click(); });
    camInput.addEventListener("change", function () {
      addFiles(camInput.files);
      camInput.value = "";
    });

    drop.addEventListener("dragover", function (e) {
      e.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", function () {
      drop.classList.remove("dragover");
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      drop.classList.remove("dragover");
      if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!fileStore.length) return;
      btn.disabled = true;
      btn.textContent = "Uploading…";
      errsEl.hidden = true;

      // FormData(form) carries the option fields; the photos come from
      // fileStore (the input itself is always empty on the JS path).
      var body = new FormData(form);
      fileStore.forEach(function (f) { body.append("photos", f, f.name); });

      fetch("/upload", {
        method: "POST",
        body: body,
        headers: { "X-Requested-With": "fetch" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.errors && data.errors.length) {
            errsEl.textContent = data.errors
              .map(function (err) { return err.filename + ": " + err.error; })
              .join(" · ");
            errsEl.hidden = false;
          }
          fileStore = [];
          applyQueue(data);
        })
        .catch(function () {
          errsEl.textContent = "Upload failed — check the server and try again.";
          errsEl.hidden = false;
        })
        .finally(syncFiles);
    });

    // ---- per-card + toolbar actions ---------------------------------------

    queueEl.addEventListener("click", function (e) {
      var card = e.target.closest(".ucard");
      if (!card) return;
      if (e.target.closest(".redo-one")) openRedo([card]);
      else if (e.target.closest(".delete-one")) deleteUploads([card.dataset.id]);
      else if (e.target.closest(".stop-one")) stopUpload(card.dataset.id);
    });

    function stopUpload(id) {
      fetch("/upload/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [id] }),
      })
        .then(function (r) { return r.json(); })
        .then(applyQueue);
    }

    queueEl.addEventListener("change", function (e) {
      if (e.target.classList.contains("ucard-check")) syncToolbar();
    });

    document.getElementById("redo-selected").addEventListener("click", function () {
      var cards = selectedCards();
      if (cards.length) openRedo(cards);
    });
    document.getElementById("delete-selected").addEventListener("click", function () {
      var ids = selectedCards().map(function (c) { return c.dataset.id; });
      if (ids.length) deleteUploads(ids);
    });
    document.getElementById("clear-selection").addEventListener("click", function () {
      queueEl.querySelectorAll(".ucard-check:checked").forEach(function (c) {
        c.checked = false;
      });
      syncToolbar();
    });

    function deleteUploads(ids) {
      var what = ids.length === 1 ? "this photo" : ids.length + " photos";
      if (!confirm("Delete " + what + " from the queue? The uploaded files are removed too.")) return;
      fetch("/upload/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids }),
      })
        .then(function (r) { return r.json(); })
        .then(applyQueue);
    }

    // ---- redo dialog ---------------------------------------------------------

    var redoIds = [];
    var redoEnhance = document.getElementById("redo-enhance");
    var redoRemoveBg = document.getElementById("redo-remove-bg");
    var redoMessage = document.getElementById("redo-message");
    var redoMessageField = document.getElementById("redo-message-field");
    var redoProcessed = document.getElementById("redo-source-processed");

    function syncRedoMessage() {
      redoMessageField.hidden = !redoEnhance.checked;
    }
    redoEnhance.addEventListener("change", syncRedoMessage);

    function openRedo(cards) {
      redoIds = cards.map(function (c) { return c.dataset.id; });
      document.getElementById("redo-title").textContent = cards.length === 1
        ? "Redo processing"
        : "Redo processing (" + cards.length + " photos)";

      var allProcessed = cards.every(function (c) { return c.dataset.hasProcessed === "1"; });
      redoProcessed.disabled = !allProcessed;
      // Default to the original; "processed" is opt-in and only offered
      // when every selected photo actually has a processed version.
      document.querySelector('input[name="redo-source"][value="original"]').checked = true;

      // Prefill options/message from the first selected photo's last run,
      // so "fix the helpful message and rerun" is a two-click edit.
      var first = cards[0];
      redoEnhance.checked = first.dataset.enhance === "1";
      redoRemoveBg.checked = first.dataset.removeBg === "1";
      redoMessage.value = first.dataset.message || "";
      syncRedoMessage();
      dialog.showModal();
    }

    document.getElementById("redo-cancel").addEventListener("click", function () {
      dialog.close();
    });

    document.getElementById("redo-run").addEventListener("click", function () {
      var body = {
        ids: redoIds,
        source: document.querySelector('input[name="redo-source"]:checked').value,
        enhance: redoEnhance.checked,
        remove_bg: redoRemoveBg.checked,
        message: redoMessage.value.trim(),
      };
      dialog.close();
      fetch("/upload/redo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json(); })
        .then(applyQueue);
    });
  });
})();
