(function () {
  "use strict";

  // The name field is a <textarea> (so long names wrap instead of
  // clipping) styled to look like a single-line input — grow it to fit
  // its content and block Enter from inserting a literal newline.
  function initNameField() {
    var el = document.querySelector(".name-input");
    if (!el) return;

    function resize() {
      el.style.height = "auto";
      el.style.height = el.scrollHeight + "px";
    }

    el.addEventListener("input", resize);
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter") e.preventDefault();
    });
    resize();
    // Chivo loads async (font-display: swap); it can swap in after this
    // runs with different metrics than the fallback font, leaving the
    // box sized for the old line count. Re-measure once it's settled.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(resize);
    }
  }

  // Strict label pickers. Each .tag-input is a combobox over the known
  // vocabulary (the server-rendered <datalist> named in data-suggestions):
  // focusing opens the full dropdown, typing filters it, and the only way
  // to mint a NEW label is the explicit "Make this a new label" row pinned
  // to the bottom — typed text is never committed on Enter/blur, so a typo
  // like "Uban Outfitters" can't quietly enter the vocabulary forever.
  // Fields marked data-single (Category, Brand, Size) hold at most one
  // value; picking another replaces it.
  function initTagInputs() {
    document.querySelectorAll(".tag-input").forEach(function (wrap) {
      var single = wrap.hasAttribute("data-single");
      var tagsEl = wrap.querySelector(".tags");
      var entry = wrap.querySelector(".tag-entry");
      var hidden = wrap.querySelector(".tag-value");
      var basePlaceholder = entry.placeholder;
      var values = hidden.value
        ? hidden.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean)
        : [];

      var suggestionsId = entry.dataset.suggestions;
      var datalist = suggestionsId ? document.getElementById(suggestionsId) : null;
      var allSuggestions = datalist
        ? Array.prototype.map.call(datalist.options, function (o) { return o.value; })
        : [];

      var menu = document.createElement("div");
      menu.className = "tag-suggestions";
      menu.hidden = true;
      wrap.style.position = "relative";
      wrap.appendChild(menu);

      var rows = [];    // rendered menu rows (options, then maybe the create row)
      var actions = []; // what activating each row does
      var activeIndex = -1;

      function closeMenu() {
        menu.hidden = true;
        menu.innerHTML = "";
        rows = [];
        actions = [];
        activeIndex = -1;
      }

      function setActive(i) {
        if (!rows.length) return;
        i = (i + rows.length) % rows.length;
        if (activeIndex >= 0) rows[activeIndex].classList.remove("active");
        activeIndex = i;
        rows[i].classList.add("active");
        rows[i].scrollIntoView({ block: "nearest" });
      }

      function openMenu() {
        var raw = entry.value.trim();
        var query = raw.toLowerCase();
        var used = {};
        values.forEach(function (v) { used[v.toLowerCase()] = true; });

        var starts = [];
        var contains = [];
        allSuggestions.forEach(function (s) {
          var lower = s.toLowerCase();
          if (used[lower]) return;
          if (!query) {
            starts.push(s);
            return;
          }
          var at = lower.indexOf(query);
          if (at === 0) starts.push(s);
          else if (at > 0) contains.push(s);
        });
        var matches = starts.concat(contains);

        menu.innerHTML = "";
        rows = [];
        actions = [];
        activeIndex = -1;

        matches.forEach(function (m) {
          var opt = document.createElement("div");
          opt.className = "tag-suggestion";
          opt.textContent = m;
          opt.addEventListener("mousedown", function (e) {
            e.preventDefault(); // keep focus on entry so blur doesn't fire first
            addValue(m);
          });
          menu.appendChild(opt);
          rows.push(opt);
          actions.push(function () { addValue(m); });
        });

        // Offer to mint the typed text as a new label — unless it already
        // exists (then the matching option row above is the way to pick it).
        var isKnown = allSuggestions.some(function (s) { return s.toLowerCase() === query; })
          || values.some(function (v) { return v.toLowerCase() === query; });
        if (raw && !isKnown) {
          var create = document.createElement("div");
          create.className = "tag-create";
          create.textContent = '＋ Make "' + raw + '" a new label';
          create.addEventListener("mousedown", function (e) {
            e.preventDefault();
            createLabel(raw);
          });
          menu.appendChild(create);
          rows.push(create);
          actions.push(function () { createLabel(raw); });
        }

        if (!rows.length) {
          closeMenu();
          return;
        }
        menu.hidden = false;
        // Enter never lands on the create row by default — creating a label
        // must be deliberate (arrow down to it, or click it).
        if (matches.length) setActive(0);
      }

      function render() {
        tagsEl.innerHTML = "";
        values.forEach(function (v, i) {
          var chip = document.createElement("span");
          chip.className = "tag";
          chip.textContent = v;
          var rm = document.createElement("button");
          rm.type = "button";
          rm.textContent = "×";
          rm.addEventListener("click", function () {
            values.splice(i, 1);
            render();
          });
          chip.appendChild(rm);
          tagsEl.appendChild(chip);
        });
        hidden.value = values.join(",");
        // Once there's a value, the "Select a …" hint is just clutter
        // next to the existing chips.
        entry.placeholder = values.length ? "" : basePlaceholder;
        // Programmatic value changes don't fire native events — nudge the
        // autosave listener (which listens for "input" on the form) awake.
        hidden.dispatchEvent(new Event("input", { bubbles: true }));
      }

      function addValue(v) {
        v = (v || "").trim();
        if (!v) return;
        if (single) {
          values = [v];
        } else if (!values.some(function (x) { return x.toLowerCase() === v.toLowerCase(); })) {
          values.push(v);
        }
        entry.value = "";
        closeMenu();
        render();
      }

      function createLabel(label) {
        // Commas would collide with the hidden field's join/split format.
        label = label.replace(/,/g, " ").replace(/\s+/g, " ").trim();
        if (!label) return;
        if (allSuggestions.indexOf(label) === -1) allSuggestions.push(label);
        addValue(label);
      }

      entry.addEventListener("input", openMenu);
      entry.addEventListener("focus", openMenu);
      entry.addEventListener("click", function () {
        if (menu.hidden) openMenu();
      });

      entry.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          if (!menu.hidden && activeIndex >= 0) actions[activeIndex]();
        } else if (e.key === "ArrowDown") {
          e.preventDefault();
          if (menu.hidden) openMenu();
          else setActive(activeIndex + 1);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (!menu.hidden) setActive(activeIndex - 1);
        } else if (e.key === "Escape") {
          closeMenu();
        } else if (e.key === "Backspace" && !entry.value && values.length) {
          values.pop();
          render();
        }
      });
      entry.addEventListener("blur", function () {
        // Uncommitted typed text is a filter query, not a value — drop it.
        entry.value = "";
        closeMenu();
      });

      render();
    });
  }

  // Wires the notes contenteditable (mentions, linking, serialization).
  // Returns { syncToRaw } so the autosave loop can flush pending edits
  // into the hidden textarea before every save, or null if there's no
  // notes editor on this page.
  function initNotesEditor(saveNow) {
    var editor = document.querySelector(".notes-editor");
    if (!editor) return null;
    var raw = document.querySelector(".notes-raw");
    var dataEl = document.getElementById("mention-data");
    var mentions = dataEl ? JSON.parse(dataEl.textContent) : [];

    var menu = document.createElement("div");
    menu.className = "mention-menu";
    menu.hidden = true;
    editor.parentElement.style.position = "relative";
    editor.parentElement.appendChild(menu);

    var mentionRange = null; // Range covering "@query" being typed

    function closeMenu() {
      menu.hidden = true;
      menu.innerHTML = "";
      mentionRange = null;
    }

    function openMenuFor(range, query) {
      var matches = mentions.filter(function (m) {
        return m.name.toLowerCase().indexOf(query.toLowerCase()) !== -1;
      }).slice(0, 8);

      if (!matches.length) {
        closeMenu();
        return;
      }

      menu.innerHTML = "";
      matches.forEach(function (m) {
        var opt = document.createElement("div");
        opt.className = "mention-option";
        opt.textContent = m.name;
        opt.addEventListener("mousedown", function (e) {
          e.preventDefault(); // keep selection/range alive
          insertMention(m);
        });
        menu.appendChild(opt);
      });

      var rect = range.getBoundingClientRect();
      var hostRect = editor.parentElement.getBoundingClientRect();
      menu.style.left = (rect.left - hostRect.left) + "px";
      menu.style.top = (rect.bottom - hostRect.top + 4) + "px";
      menu.hidden = false;
    }

    function insertMention(m) {
      if (!mentionRange) return;
      var span = document.createElement("span");
      span.className = "mention";
      span.contentEditable = "false";
      span.dataset.file = m.file;
      span.dataset.name = m.name;
      span.textContent = "@" + m.name;

      mentionRange.deleteContents();
      mentionRange.insertNode(span);

      var spaceNode = document.createTextNode(" ");
      span.after(spaceNode);

      var sel = window.getSelection();
      var newRange = document.createRange();
      newRange.setStartAfter(spaceNode);
      newRange.collapse(true);
      sel.removeAllRanges();
      sel.addRange(newRange);

      closeMenu();
      editor.focus();
      // insertNode() is a plain DOM mutation, unlike execCommand — it
      // doesn't fire "input" on its own, so autosave wouldn't notice.
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function currentMentionQuery() {
      var sel = window.getSelection();
      if (!sel.rangeCount || !sel.isCollapsed) return null;
      var range = sel.getRangeAt(0);
      if (!editor.contains(range.startContainer)) return null;
      var node = range.startContainer;
      if (node.nodeType !== Node.TEXT_NODE) return null;

      var textBefore = node.textContent.slice(0, range.startOffset);
      var at = textBefore.lastIndexOf("@");
      if (at === -1) return null;
      var query = textBefore.slice(at + 1);
      if (/\s/.test(query)) return null; // "@" was part of an earlier word

      var atRange = document.createRange();
      atRange.setStart(node, at);
      atRange.setEnd(node, range.startOffset);
      mentionRange = atRange;
      return query;
    }

    editor.addEventListener("keyup", function (e) {
      if (e.key === "Escape") {
        closeMenu();
        return;
      }
      if (["ArrowUp", "ArrowDown", "Enter"].indexOf(e.key) !== -1 && !menu.hidden) {
        return; // leave menu open; simple click-to-select model
      }
      var query = currentMentionQuery();
      if (query === null) {
        closeMenu();
        return;
      }
      openMenuFor(mentionRange, query);
    });

    function insertLink() {
      var sel = window.getSelection();
      if (!sel.rangeCount || sel.isCollapsed || !editor.contains(sel.anchorNode)) {
        alert("Select some text in the notes field first.");
        return;
      }
      var url = prompt("Link URL:");
      if (!url) return;
      if (!/^https?:\/\//i.test(url)) url = "https://" + url;
      editor.focus();
      document.execCommand("createLink", false, url);
      editor.querySelectorAll("a:not([target])").forEach(function (a) {
        a.target = "_blank";
        a.rel = "noopener";
      });
    }

    editor.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        document.execCommand("insertText", false, "\n");
      } else if (e.key === "Escape") {
        closeMenu();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        insertLink();
      }
    });

    editor.addEventListener("blur", function () {
      // Delay so a mousedown on a menu option still fires first.
      setTimeout(closeMenu, 150);
    });

    editor.addEventListener("paste", function (e) {
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, text);
    });

    // Clicking a mention takes you straight to that item; clicking a link
    // opens it in a new tab. Both are otherwise inert inside a
    // contenteditable region, so they need explicit handling.
    editor.addEventListener("click", function (e) {
      var mention = e.target.closest(".mention");
      if (mention) {
        e.preventDefault();
        var file = mention.dataset.file;
        Promise.resolve(saveNow()).then(function () {
          window.location.href = "/edit/" + encodeURIComponent(file);
        });
        return;
      }
      var link = e.target.closest("a");
      if (link) {
        e.preventDefault();
        // Open synchronously (before any await) so browsers don't treat it
        // as a blocked popup; save in parallel rather than gating on it.
        window.open(link.href, "_blank", "noopener");
        saveNow();
      }
    });

    function serialize(node) {
      var out = "";
      node.childNodes.forEach(function (child) {
        if (child.nodeType === Node.TEXT_NODE) {
          out += child.textContent;
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          if (child.classList && child.classList.contains("mention")) {
            out += "@[" + child.dataset.name + "](" + child.dataset.file + ")";
          } else if (child.tagName === "A") {
            out += "[" + child.textContent + "](" + child.getAttribute("href") + ")";
          } else if (child.tagName === "BR") {
            out += "\n";
          } else if (child.tagName === "DIV" || child.tagName === "P") {
            out += "\n" + serialize(child);
          } else {
            out += serialize(child);
          }
        }
      });
      return out;
    }

    function syncToRaw() {
      raw.value = serialize(editor).replace(/ /g, " ");
    }

    return { syncToRaw: syncToRaw };
  }

  // Debounced autosave: POSTs the whole form on every change so nothing is
  // lost when a mention/link click navigates away or opens a new tab.
  function initAutosave(syncNotesToRaw) {
    var form = document.querySelector(".edit-form");
    if (!form) return null;
    // The label page (data-no-autosave) creates the item only on explicit
    // submit — autosaving would try to save an item that doesn't exist yet.
    if (form.hasAttribute("data-no-autosave")) return null;
    var status = document.querySelector(".save-status");
    var timer = null;

    function setStatus(text) {
      if (status) status.textContent = text;
    }

    function saveNow() {
      clearTimeout(timer);
      if (syncNotesToRaw) syncNotesToRaw();
      var body = new URLSearchParams(new FormData(form));
      setStatus("Saving…");
      return fetch(form.action, {
        method: "POST",
        body: body,
        headers: { "X-Requested-With": "fetch" },
      }).then(function () {
        setStatus("Saved");
      }).catch(function (err) {
        console.error("Autosave failed:", err);
        setStatus("Save failed");
      });
    }

    function schedule(delay) {
      clearTimeout(timer);
      setStatus("Editing…");
      timer = setTimeout(saveNow, delay);
    }

    form.addEventListener("input", function () { schedule(600); });
    form.addEventListener("change", function () { schedule(200); });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      saveNow();
    });

    // Best-effort flush if the tab is closed/navigated mid-debounce.
    window.addEventListener("beforeunload", function () {
      if (timer) {
        clearTimeout(timer);
        if (syncNotesToRaw) syncNotesToRaw();
        navigator.sendBeacon(form.action, new URLSearchParams(new FormData(form)));
      }
    });

    return { saveNow: saveNow };
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNameField();
    initTagInputs();

    var notes = null;
    var autosave = initAutosave(function () {
      if (notes) notes.syncToRaw();
    });
    var saveNow = autosave ? autosave.saveNow : function () { return Promise.resolve(); };
    notes = initNotesEditor(saveNow);

    // No autosave (label page): the form submits natively, so flush the
    // notes editor into its hidden textarea right before submission.
    if (!autosave) {
      var form = document.querySelector(".edit-form");
      if (form) {
        form.addEventListener("submit", function () {
          if (notes) notes.syncToRaw();
        });
      }
    }
  });
})();
