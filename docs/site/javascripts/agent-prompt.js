function initCopyAgentPromptButton() {
  const disclosure = document.getElementById("agent-prompt");
  const code = disclosure?.querySelector("pre code");
  if (!code || document.getElementById("copy-agent-prompt-btn")) return;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.id = "copy-agent-prompt-btn";
  btn.className = "md-button";
  btn.textContent = "Get your agents on board";
  btn.setAttribute("aria-live", "polite");
  disclosure.before(btn);
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code.textContent);
      btn.textContent = "Copied!";
    } catch (err) {
      btn.textContent = "Copy failed. Open the prompt to copy it manually.";
      console.error("Failed to copy agent prompt to clipboard:", err);
    }
    setTimeout(() => { btn.textContent = "Get your agents on board"; }, 2000);
  });
}

// Support Material instant navigation and ordinary page loads.
if (typeof document$ !== "undefined") {
  document$.subscribe(initCopyAgentPromptButton);
} else {
  document.addEventListener("DOMContentLoaded", initCopyAgentPromptButton);
}
