# Public product showcase

Live URL: <https://glokadhi.github.io/Strict-Routines-An-App-for-Productivity-and-Time/>.

The showcase uses the existing repository and a small, dependency-free static
build. No second application checkout or duplicate frontend project is created.
The Windows, Android, extension, database and session workflows are unchanged.

## Source and maintained status

- `index.html`: semantic page sections, platform boundaries and source links.
- `assets/site/style.css`: responsive dark visual system and reduced-motion rules.
- `assets/site/app.js`: keyboard tabs, navigation, native detail dialogs, diagnostic
  model, architecture modes, and read-only public GitHub metadata.
- `assets/site/project.json`: maintained features, explanations, FAQ, source
  references, and roadmap status. Update this with a real source/verification
  reference. “Completed” describes a source milestone, not live rollout acceptance.
- `assets/site/*.svg`: original code-native illustrations and icons. Device views
  are explicitly illustrative; no connected device, private camera, points total,
  session or uptime is fabricated.

Public consent-cleared demonstration recordings are not present. The eight
walkthrough cards therefore open implementation explanations and actual source
links. Do not add a fake play button, invented video URL, face image, personal
fitness/evidence screenshot, or a fictional duration. Future video additions need
real media, captions, consent and a text alternative.

## Local production build

From the existing project root:

```powershell
python scripts/build_pages.py
python -m http.server 8128 --bind 127.0.0.1 --directory .pages-build
```

Only the generated `.pages-build` folder is served. The build validates fragment
targets, unique IDs, SVG symbols, local assets, source references and common
private-file/credential signatures. It never imports the application or opens a
user database, credential store, evidence file or backend endpoint.

## GitHub Actions deployment

`.github/workflows/pages.yml` builds on main pushes and supports a manual trigger.
Repository Settings → Pages must use **GitHub Actions**. The workflow deploys only
the allowlisted website artifact, with Pages/OIDC permissions. `_config.yml` is
retained for the prior README site but is not used by this static build.

Project URLs work through relative asset paths. A custom domain can be configured
in Pages settings; set the repository Actions variable `SITE_URL` to its canonical
HTTPS URL if an explicit override is needed. The builder generates canonical and
Open Graph URLs, sitemap, robots rules and a not-found page from that value.
No provider key, private hostname, device credential or backend URL belongs here.

## Live metadata and privacy

The website optionally fetches public GitHub commit, release and workflow metadata
without authorization, cookies or analytics trackers. A short sessionStorage cache
reduces repeated public API requests. Rate limits and failures show unavailable;
the frontend never invents a successful provider or application connection.
Backend status remains unavailable because no safe public backend status endpoint
is configured. GitHub's own hosting/privacy policies still apply.

The security section describes source safeguards and their limits. It is not a
claim of independent certification or an independently audited legal privacy
policy. The native iOS files are reference code; a signed distributed native app
and physical-device acceptance are not inferred from them.

## Contributing

Use focused changes in this checkout. Keep public claims attached to actual source
and tests, preserve existing app logic, and leave personal evidence/secrets out of
commits. Validate the production build and responsive interactions before pushing.
Inspect the actual GitHub Actions outcome and deployed URL before claiming success.

## Modern Web Guidance applied

- `responsive-design`: bounded fluid typography and reserved media ratios follow
  “Use `aspect-ratio` for media elements”; device artwork cannot shift loaded text.
- `css-layout`: flexible grid tracks and minimum inline sizes follow “Do NOT use
  `overflow: hidden` to mask layout issues”; small widths must actually fit.
- `scrollspy`: native current-target styling has a semantic state and an observer
  fallback; “the visual state must be mirrored with `aria-current`.”
- `motion`: short transform/opacity entry is opt-in through the OS motion query;
  “Default to static views.” No permanent auto-running animation needs stopping.
- `accessibility`: tabs use the full keyboard pattern and dialogs use the browser's
  modal primitive: “Don't implement focus traps for native modal dialogs.”
