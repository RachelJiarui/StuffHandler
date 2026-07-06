(function () {
  "use strict";

  // Progressive enhancement over the plain <form method="get"> in the
  // header: fetches results in the background and swaps them in, so the
  // grid updates as you type/filter without a full page navigation —
  // no scroll jump, no lost focus mid-keystroke. The form works with zero
  // JS too (Enter in the search box, or the Search button, submit
  // natively and get the same result server-rendered).
  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("search-form");
    if (!form) return; // no images at all -> no search bar rendered

    var input = form.querySelector(".search-input");
    var results = document.getElementById("results");
    var count = document.querySelector("header .count");
    var filterBadge = form.querySelector(".filter-count");
    var clearLink = form.querySelector(".clear-filters");
    var timer = null;
    var inFlight = null;

    function syncChrome() {
      var activeCount = form.querySelectorAll('input[type="checkbox"]:checked').length;
      filterBadge.textContent = "(" + activeCount + ")";
      filterBadge.hidden = activeCount === 0;
      clearLink.hidden = !(input.value.trim() || activeCount);
    }

    function runSearch(pushUrl) {
      clearTimeout(timer);
      if (inFlight) inFlight.abort();

      var params = new URLSearchParams(new FormData(form));
      var url = "/?" + params.toString();
      var controller = new AbortController();
      inFlight = controller;

      fetch(url, {
        headers: { "X-Requested-With": "fetch" },
        signal: controller.signal,
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          inFlight = null;
          results.innerHTML = data.results_html;
          count.textContent = data.count_text;
          if (pushUrl) history.pushState({ search: true }, "", url);
        })
        .catch(function (err) {
          if (err.name !== "AbortError") form.submit(); // fall back to a real navigation
        });

      syncChrome();
    }

    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { runSearch(true); }, 500);
    });

    form.addEventListener("change", function (e) {
      if (e.target.matches('input[type="checkbox"]')) {
        runSearch(true);
      }
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      runSearch(true);
    });

    window.addEventListener("popstate", function () {
      location.reload();
    });
  });
})();
