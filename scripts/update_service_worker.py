import sys

path = 'browser_extension/service_worker.js'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '  if (message?.type === "STRICTBOT_CONNECTION_STATUS") {'

handlers = '''  if (message?.type === "STRICTBOT_GOOGLE_DRIVE_STATUS") {
    fetch("http://127.0.0.1:8765/api/cloud_sync/status", { cache: "no-store" })
      .then(r => r.json())
      .then(data => sendResponse({
        success: true,
        account_status: data.account_status || (data.connected ? "Connected" : "Not connected"),
        user_email: data.user_email || data.email || "",
        drive_status: data.drive_status || (data.drive_authorized ? "Connected" : "Authorization required"),
        folder_name: "StrictRecords",
        folder_id: data.folder_id || "",
        sync_status: data.sync_status || "Ready"
      }))
      .catch(() => {
        chrome.storage.local.get(["googleDriveStatus", "googleUserEmail"], res => {
          sendResponse({
            success: true,
            account_status: res.googleUserEmail ? "Connected" : "Not connected",
            user_email: res.googleUserEmail || "",
            drive_status: res.googleDriveStatus || "Not authorized",
            folder_name: "StrictRecords",
            sync_status: "Offline"
          });
        });
      });
    return true;
  }
  if (message?.type === "STRICTBOT_GOOGLE_DRIVE_SYNC_NOW") {
    fetch("http://127.0.0.1:8765/api/cloud_sync/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    })
      .then(r => r.json())
      .then(data => sendResponse(data))
      .catch(err => sendResponse({ success: false, error: err.message, sync_status: "Sync failed" }));
    return true;
  }
'''

assert target in content, "target not found in service_worker.js"
content = content.replace(target, handlers + '\n' + target, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated browser_extension/service_worker.js with Google Drive sync handlers")
