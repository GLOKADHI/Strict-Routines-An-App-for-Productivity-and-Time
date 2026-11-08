const REPOSITORY = 'GLOKADHI/Strict-Routines-An-App-for-Productivity-and-Time';
const GITHUB = `https://github.com/${REPOSITORY}`;
const API = `https://api.github.com/repos/${REPOSITORY}`;
const menu = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#primary-nav');
const dialog = document.querySelector('#detail-dialog');
let project;

function closeMenu(restore = false) {
  menu.setAttribute('aria-expanded', 'false');
  menu.setAttribute('aria-label', 'Open navigation');
  navigation.classList.remove('open');
  if (restore) menu.focus();
}
menu.addEventListener('click', () => {
  const opened = menu.getAttribute('aria-expanded') !== 'true';
  menu.setAttribute('aria-expanded', String(opened));
  menu.setAttribute('aria-label', opened ? 'Close navigation' : 'Open navigation');
  navigation.classList.toggle('open', opened);
});
navigation.addEventListener('click', event => { if (event.target.closest('a')) closeMenu(); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') closeMenu(true);
});
document.addEventListener('click', event => {
  if (!event.target.closest('.nav-shell')) closeMenu();
});
matchMedia('(min-width: 921px)').addEventListener('change', event => { if (event.matches) closeMenu(); });

// Native tab controls with roving tabindex and the complete keyboard pattern.
const tabs = [...document.querySelectorAll('[role="tab"]')];
function activateTab(tab, focus = false) {
  for (const item of tabs) {
    const active = item === tab;
    item.setAttribute('aria-selected', String(active));
    item.tabIndex = active ? 0 : -1;
    document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
  }
  if (focus) tab.focus();
}
tabs.forEach(tab => {
  tab.addEventListener('click', () => activateTab(tab));
  tab.addEventListener('keydown', event => {
    let index = tabs.indexOf(tab);
    if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') index = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else return;
    event.preventDefault();
    activateTab(tabs[index], true);
  });
});

// Native scrollspy when available; IntersectionObserver in other browsers.
const navLinks = [...navigation.querySelectorAll('a[href^="#"]')];
const navSections = navLinks.map(link => document.querySelector(link.getAttribute('href')));
function markCurrent(link) {
  navLinks.forEach(item => {
    item.setAttribute('aria-current', item === link ? 'true' : 'false');
    item.classList.toggle('target-current', item === link);
  });
}
if (CSS.supports('scroll-target-group: auto') && CSS.supports('selector(a:target-current)')) {
  const sync = () => {
    const selected = navigation.querySelector('a:target-current');
    if (selected) markCurrent(selected);
  };
  document.addEventListener('scrollend', sync);
  requestAnimationFrame(sync);
} else if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (visible.length) markCurrent(navLinks.find(link => link.hash === `#${visible[0].target.id}`));
  }, { rootMargin: '-18% 0px -55% 0px', threshold: 0 });
  navSections.forEach(section => { if (section) observer.observe(section); });
}

document.querySelectorAll('[data-architecture]').forEach(button => button.addEventListener('click', () => {
  const mode = button.dataset.architecture;
  document.querySelector('.architecture-grid').dataset.mode = mode;
  document.querySelectorAll('[data-architecture]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  document.querySelector('#architecture-description').textContent = mode === 'media'
    ? 'Media view: camera and screen tracks travel over WebRTC, directly or through configured authenticated TURN. HTTPS/WSS carries signaling and control. Cloudflare Tunnel does not itself relay the visual media.'
    : 'Control view: authenticated HTTPS/WSS carries identity, policy, events, and heartbeat signals. The Windows session controller owns the session lifecycle.';
}));

const scenarios = {
  healthy: ['Independent checks agree', 'In this model, the companion report and the receiver frame counters both indicate progress.', []],
  capture: ['iOS capture interrupted', 'Local capture has stopped. The model distinguishes the capture interruption from a decoder failure and retains the Windows session identity.', ['capture']],
  receiver: ['Sender active, receiver not progressing', 'The companion reports transmission, while Windows has no fresh arriving frames. Transport and receiver diagnostics are needed; a heartbeat does not prove video delivery.', ['transport']],
  decoder: ['Windows decoder stalled', 'Frames arrive, but decoded-frame progress is stale. This model identifies the receiver pipeline stage rather than assuming the user stopped sharing.', ['decode']],
  channel: ['Control channel disconnected', 'Control reports are stale. Existing media progress is a separate signal; unknown control state must not automatically become a claim that sharing stopped.', ['transport']],
  unknown: ['Status unknown', 'The model has insufficient fresh evidence. It does not invent connectivity, frame reception, compliance, or a successful verification.', ['capture','transport','decode','process']]
};
document.querySelector('#diagnostic-scenario').addEventListener('change', event => {
  const [title, copy, failed] = scenarios[event.target.value];
  document.querySelector('#diagnostic-title').textContent = title;
  document.querySelector('#diagnostic-copy').textContent = copy;
  document.querySelectorAll('[data-stage]').forEach(stage => stage.classList.toggle('failure', failed.includes(stage.dataset.stage)));
});

const projectReady = fetch(new URL('./project.json', import.meta.url), { credentials: 'omit' })
  .then(response => { if (!response.ok) throw Error('Project reference unavailable'); return response.json(); })
  .then(data => { project = data; return data; });
// Retain ordinary documentation links if optional interactive data cannot load.
projectReady.catch(() => {});
document.addEventListener('click', async event => {
  const trigger = event.target.closest('[data-detail]');
  if (!trigger) return;
  try {
    const data = await projectReady;
    const detail = data.details[trigger.dataset.detail];
    if (!detail) return;
    document.querySelector('#detail-title').textContent = detail.title;
    const body = document.querySelector('#detail-content');
    body.replaceChildren();
    const description = document.createElement('p'); description.textContent = detail.body; body.append(description);
    const list = document.createElement('ul');
    for (const line of detail.bullets) { const item = document.createElement('li'); item.textContent = line; list.append(item); }
    body.append(list);
    const links = document.querySelector('#detail-links'); links.replaceChildren();
    for (const path of detail.sources) {
      const link = document.createElement('a'); link.textContent = path.split('/').at(-1);
      link.href = `${GITHUB}/blob/main/${path}`; links.append(link);
    }
    dialog.showModal();
  } catch {
    document.querySelector('#detail-title').textContent = 'Walkthrough temporarily unavailable';
    document.querySelector('#detail-content').textContent = 'The public project reference could not load. The repository and documentation links remain available.';
    document.querySelector('#detail-links').replaceChildren();
    dialog.showModal();
  }
});
dialog.addEventListener('click', event => {
  const box = dialog.getBoundingClientRect();
  if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) dialog.close();
});

// Read only public repository metadata. No tokens, cookies, local endpoints,
// evidence, session identifiers, or private application telemetry are requested.
async function publicJSON(path) {
  const response = await fetch(API + path, { credentials: 'omit', signal: AbortSignal.timeout(7000), headers: { Accept: 'application/vnd.github+json' } });
  if (response.status === 404) return null;
  if (!response.ok) throw Error('Public GitHub API unavailable');
  return response.json();
}
async function repositoryStatus() {
  const key = 'strictrecords-public-repo-v1';
  let values;
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (saved && Date.now() - saved.time < 600000) values = saved.values;
  } catch { /* Storage is optional. */ }
  if (!values) {
    const results = await Promise.allSettled([publicJSON('/commits/main'), publicJSON('/releases/latest'), publicJSON('/actions/workflows/pages.yml/runs?per_page=1')]);
    values = results.map(result => result.status === 'fulfilled' ? { ok: true, value: result.value } : { ok: false });
    if (values.every(value => value.ok)) { try { sessionStorage.setItem(key, JSON.stringify({ time: Date.now(), values })); } catch {} }
  }
  const [commit, release, workflow] = values;
  const commitLink = document.querySelector('#repo-commit');
  if (commit.ok && /^[a-f0-9]{40}$/.test(commit.value?.sha || '')) { commitLink.textContent = commit.value.sha.slice(0, 7); commitLink.href = `${GITHUB}/commit/${commit.value.sha}`; }
  else commitLink.textContent = 'Status unavailable';
  document.querySelector('#repo-release').textContent = release.ok ? release.value?.tag_name || 'No published release' : 'Status unavailable';
  const run = workflow.ok ? workflow.value?.workflow_runs?.[0] : null;
  document.querySelector('#repo-workflow').textContent = !workflow.ok ? 'Status unavailable' : run ? `${run.conclusion || run.status}` : 'No workflow run found';
  document.querySelector('#repo-status-note').textContent = values.every(value => value.ok)
    ? 'Source: public GitHub API. Repository metadata does not describe a live application backend.'
    : 'Some public GitHub metadata is unavailable or rate-limited. No application connection status is inferred.';
}
repositoryStatus().catch(() => {
  for (const id of ['repo-commit','repo-release','repo-workflow']) document.getElementById(id).textContent = 'Status unavailable';
});
fetch(new URL('../../build-info.json', import.meta.url), { credentials: 'omit' })
  .then(response => response.ok ? response.json() : null)
  .then(data => { if (data && /^[a-f0-9]{40}$/.test(data.commit || '')) document.querySelector('#site-build').textContent = `Site build ${data.commit.slice(0,7)} · Static GitHub Pages showcase`; })
  .catch(() => {});
