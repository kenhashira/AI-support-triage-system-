// Small progressive enhancements. The app works without JavaScript.
(function () {
  var textarea = document.querySelector("textarea[data-counter]");
  if (textarea) {
    var counter = document.getElementById(textarea.dataset.counter);
    var update = function () { counter.textContent = textarea.value.length; };
    textarea.addEventListener("input", update);
    update();
  }

  // Forms that call the AI opt in with data-busy="<label>": show progress and
  // prevent double-submits while the (few-second) analysis runs.
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    var button = form.querySelector("button[type=submit]");
    var idleLabel = button.textContent;
    form.addEventListener("submit", function () {
      button.disabled = true;
      button.textContent = form.dataset.busy;
    });
    // Restore the button if the user comes back via the browser's back button.
    window.addEventListener("pageshow", function () {
      button.disabled = false;
      button.textContent = idleLabel;
    });
  });
})();
