(() => {
  const button = document.getElementById("connect");
  const status = document.getElementById("status");
  const details = document.getElementById("connection-details");
  let connecting = false;
  function display(message, kind = "") { status.className = kind; status.textContent = message; }
  button.addEventListener("click", async () => {
    if (connecting) return;
    connecting = true;
    button.disabled = true;
    display("Connecting to StrictRoutines…");
    if (details) details.textContent = "";
    const controller = new AbortController();
    let timeout;
    try {
      const request = async () => {
        const response = await fetch("http://127.0.0.1:8765/api/restriction/desktop_connection?connect=1", {
          cache: "no-store", credentials: "omit", signal: controller.signal
        });
        if (!response.ok) throw new Error("StrictRoutines returned " + response.status + ". Restart the updated desktop app.");
        const result = await response.json();
        if (result.connected !== true) throw new Error("The desktop app did not confirm the connection.");
        return result;
      };
      const result = await Promise.race([request(), new Promise((_, reject) => {
        timeout = setTimeout(() => { controller.abort(); reject(new Error("Connection timed out.")); }, 6000);
      })]);
      if (result.monitor_running && result.native_browser?.connected) {
        display("Connected to StrictRoutines. Compatible browsers are monitored by the desktop app—no extension installation is needed.", "ok");
      } else {
        display("Connected to StrictRoutines. Browser monitoring is not ready; restart the updated desktop app.", "warning");
      }
      if (details) {
        const browsers = result.native_browser?.browsers || [];
        details.textContent = browsers.length ? "Last observed browser capabilities:\n" + browsers.map(browser => {
          const name = browser.browser.replace(/\.exe$/, "");
          if (browser.exact_tab_close) return name + ": website detection and exact tab control available.";
          if (browser.url_detection) return name + ": website detection available; exact tab closure is unavailable.";
          return name + ": browser controls are not available to StrictRoutines.";
        }).join("\n") : "Browser capabilities are checked when a browser becomes active. The connection alone does not confirm that a browser allows exact tab closure."; 
        if (result.native_browser?.error) details.textContent += "\n" + result.native_browser.error;
      }
    } catch (error) {
      const message = String(error?.message || "");
      display(message.startsWith("StrictRoutines returned") || message === "The desktop app did not confirm the connection." ? message :
        "Could not reach StrictRoutines. Start the native desktop app, allow local network access if your browser asks, and try again.", "error");
    } finally {
      clearTimeout(timeout); connecting = false; button.disabled = false;
    }
  });
})();
