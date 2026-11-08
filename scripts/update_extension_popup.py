import sys

# 1. Update extension_popup.html
html_path = 'browser_extension/extension_popup.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

target_html = '<section id="view-settings" class="panel" role="tabpanel" aria-labelledby="tab-settings" hidden>'
gdrive_card = '''<div class="card-box"><div class="card-header"><h2>Google Drive Cloud Sync</h2><span id="gdriveBadge" class="badge neutral">Checking</span></div><p id="gdriveDetail" class="card-detail">Connecting to StrictRecords folder…</p><div class="balance"><strong id="gdriveAccount">—</strong><span>Google Account</span></div><p id="gdriveSyncStatus" class="note">Folder: StrictRecords/ • Sync: Initializing</p><div class="row" style="margin-top:8px;"><button id="btnGdriveSync" type="button" class="outline">Sync Now</button></div></div>'''

if target_html in html_content:
    html_content = html_content.replace(target_html, target_html + gdrive_card)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print("Updated extension_popup.html with Google Drive card")

# 2. Update extension_popup.js
js_path = 'browser_extension/extension_popup.js'
with open(js_path, 'r', encoding='utf-8') as f:
    js_content = f.read()

target_js = 'async function refresh() {'
gdrive_js = '''  async function refreshGDrive() {
    try {
      const res = await send('STRICTBOT_GOOGLE_DRIVE_STATUS');
      if (res && res.success) {
        const isConn = res.account_status === 'Connected';
        const isDriveConn = res.drive_status === 'Connected';
        badge('gdriveBadge', isDriveConn ? 'Connected' : isConn ? 'Auth Required' : 'Not Connected', isDriveConn ? 'ok' : isConn ? 'warn' : 'neutral');
        text('gdriveDetail', isDriveConn ? 'Connected to Google Drive folder: StrictRecords/' : (isConn ? 'Google account connected — Drive authorization required' : 'Google account not connected. Connect in desktop app.'));
        text('gdriveAccount', res.user_email || '—');
        text('gdriveSyncStatus', `Folder: StrictRecords/ • Status: ${res.sync_status}`);
      }
    } catch (_) {}
  }
'''

assert target_js in js_content, "target_js not found in extension_popup.js"
js_content = js_content.replace(target_js, gdrive_js + '\n  ' + target_js)

# Call refreshGDrive inside refresh()
target_call = 'if(policy.status===\'fulfilled\')renderPolicy(policy.value);'
js_content = js_content.replace(target_call, target_call + ' refreshGDrive();')

# Add click handler for btnGdriveSync
target_handlers = 'get(\'btnConnect\').addEventListener'
sync_handler = '''    get('btnGdriveSync')?.addEventListener('click', async () => {
      text('gdriveSyncStatus', 'Syncing with Google Drive (StrictRecords/)…');
      try {
        const res = await send('STRICTBOT_GOOGLE_DRIVE_SYNC_NOW');
        text('gdriveSyncStatus', res.success ? 'Sync completed successfully.' : 'Sync failed: ' + (res.error || 'Unknown error'));
        refreshGDrive();
      } catch (err) {
        text('gdriveSyncStatus', 'Sync failed: ' + err.message);
      }
    });
'''
if target_handlers in js_content:
    js_content = js_content.replace(target_handlers, sync_handler + '    ' + target_handlers)
else:
    # Append at bottom before closing
    js_content = js_content.replace('})();', sync_handler + '\n})();')

with open(js_path, 'w', encoding='utf-8') as f:
    f.write(js_content)
print("Updated extension_popup.js with refreshGDrive")
