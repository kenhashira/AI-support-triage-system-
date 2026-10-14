// Small progressive enhancements. The app works without JavaScript.
(function () {
  var textarea = document.querySelector("textarea[data-counter]");
  if (textarea) {
    var counter = document.getElementById(textarea.dataset.counter);
    var update = function () { counter.textContent = textarea.value.length; };
    textarea.addEventListener("input", update);
    update();
  }

  // Analysis can take a few seconds: show progress and prevent double-submits.
  var form = document.querySelector("form");
  if (form) {
    var button = form.querySelector("button[type=submit]");
    form.addEventListener("submit", function () {
      button.disabled = true;
      button.textContent = "Analyzing…";
    });
    // Restore the button if the user comes back via the browser's back button.
    window.addEventListener("pageshow", function () {
      button.disabled = false;
      button.textContent = "Submit ticket";
    });
  }
})();
