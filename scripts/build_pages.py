"""Build only the public product showcase. No app imports, data, keys or services."""
import argparse
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote, urlsplit, unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://github.com/GLOKADHI/Strict-Routines-An-App-for-Productivity-and-Time'
DEFAULT_URL = 'https://glokadhi.github.io/Strict-Routines-An-App-for-Productivity-and-Time/'
ASSETS = ['brand.svg', 'icons.svg', 'ecosystem.svg', 'social.svg', 'social.png',
          'style.css', 'app.js', 'project.json']


def icon(name):
    return f'<svg class="icon" aria-hidden="true"><use href="assets/site/icons.svg#{escape(name)}"/></svg>'


def source_link(path):
    file = ROOT / path
    if not file.exists() or not file.resolve().is_relative_to(ROOT) or '..' in Path(path).parts:
        raise ValueError('Missing/invalid public source reference: '+path)
    return REPOSITORY + ('/tree/main/' if file.is_dir() else '/blob/main/') + quote(path, safe='/')


def render(data):
    statuses = {'Completed':'green', 'In Progress':'blue', 'Planned':'purple', 'Blocked':'amber'}
    for item in data['details'].values():
        for path in item['sources']:
            source_link(path)
    cards = []
    for feature in data['features']:
        assert feature['id'] in data['details']
        cards.append(f'<article class="feature-card">{icon(feature["icon"])}<h3>{escape(feature["title"])}</h3>'
                     f'<p>{escape(feature["summary"])}</p><button type="button" data-detail="{feature["id"]}" '
                     f'aria-label="Explore {escape(feature["title"])}">Explore <span aria-hidden="true">↗</span></button></article>')
    demos = []
    for demo in data['demos']:
        assert demo['detail'] in data['details']
        title = escape(demo['title'])
        demos.append(f'<article class="demo-card"><button class="demo-thumbnail" type="button" data-detail="{demo["detail"]}" '
                     f'aria-label="Open walkthrough: {title}">{icon(demo["icon"])}<span>ILLUSTRATIVE / SOURCE WALKTHROUGH</span><b aria-hidden="true">↗</b></button>'
                     f'<h3>{title}</h3><p>{escape(demo["description"])}</p><button type="button" data-detail="{demo["detail"]}" '
                     f'aria-label="Open walkthrough: {title}">Open walkthrough ↗</button></article>')
    roadmap = []
    for i, item in enumerate(data['roadmap'], 1):
        assert item['status'] in statuses
        roadmap.append(f'<article class="roadmap-item"><span class="roadmap-index">{i:02d}</span>'
                       f'<h3><a href="{source_link(item["source"])}">{escape(item["title"])}</a></h3>'
                       f'<p>{escape(item["note"])}</p><span class="status-pill {statuses[item["status"]]}">{item["status"]}</span></article>')
    docs = []
    for item in data['docs']:
        docs.append(f'<a class="doc-card" href="{source_link(item["path"])}">{icon(item["icon"])}<span>'
                    f'<strong>{escape(item["title"])}</strong><p>{escape(item["description"])}</p></span><span aria-hidden="true">↗</span></a>')
    faq = [f'<details><summary>{escape(item["question"])}</summary><p>{escape(item["answer"])}</p></details>' for item in data['faq']]
    return {'__FEATURE_CARDS__':'\n'.join(cards), '__DEMO_CARDS__':'\n'.join(demos),
            '__ROADMAP_ITEMS__':'\n'.join(roadmap), '__DOC_CARDS__':'\n'.join(docs), '__FAQ_ITEMS__':'\n'.join(faq)}


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set(); self.links = []; self.assets = []; self.errors = []; self.h1 = 0
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            if a['id'] in self.ids: self.errors.append('Duplicate id: '+a['id'])
            self.ids.add(a['id'])
        if tag == 'h1': self.h1 += 1
        if tag == 'img' and 'alt' not in a: self.errors.append('Image without alt')
        if tag == 'a' and 'href' in a: self.links.append(a['href'])
        if tag in ('img','script') and a.get('src'): self.assets.append(a['src'])
        if tag == 'link' and a.get('rel') in ('stylesheet','icon'): self.assets.append(a['href'])
        if tag == 'use' and a.get('href'): self.assets.append(a['href'])


def validate(output):
    html = (output/'index.html').read_text(encoding='utf-8')
    document = Document(); document.feed(html)
    assert not document.errors, document.errors
    assert document.h1 == 1, 'Exactly one main heading is required'
    assert not re.search(r'__[A-Z_]+__', html), 'Unexpanded build placeholders'
    for link in document.links:
        if link.startswith('#'): assert link[1:] in document.ids, 'Broken fragment: '+link
        elif not urlsplit(link).scheme:
            assert (output/unquote(urlsplit(link).path)).is_file(), 'Broken local link: '+link
        elif link.startswith(REPOSITORY+'/blob/main/') or link.startswith(REPOSITORY+'/tree/main/'):
            marker = '/blob/main/' if '/blob/main/' in link else '/tree/main/'
            source_link(unquote(link.split(marker,1)[1]))
    for link in document.assets:
        parsed = urlsplit(link)
        if parsed.scheme: raise ValueError('Public assets must be local: '+link)
        target = output/parsed.path
        assert target.is_file(), 'Missing public asset: '+link
        if parsed.fragment and target.suffix == '.svg':
            identifiers = {element.attrib.get('id') for element in ET.parse(target).iter()}
            assert parsed.fragment in identifiers, 'Missing SVG symbol: '+link
    for path in output.rglob('*'):
        if not path.is_file(): continue
        assert path.suffix not in {'.db','.sqlite','.enc','.exe','.apk','.pem','.key','.log'}, 'Private/runtime file in website artifact'
        if path.suffix in {'.html','.json','.js','.svg','.css','.txt','.xml'}:
            raw = path.read_text(encoding='utf-8')
            assert not re.search(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{45,}|GOCSPX-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', raw), 'Secret signature in artifact'
            assert not re.search(r'https?://(?:127\.0\.0\.1|localhost|100\.93\.|192\.168\.)', raw), 'Private endpoint in artifact'
    print(json.dumps({'validation':'PASS','unique_ids':len(document.ids),'internal_fragments':sum(link.startswith('#') for link in document.links),
                      'asset_references':len(document.assets),'source_references_checked':True,'private_files_packaged':False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.pages-build')
    parser.add_argument('--site-url', default=DEFAULT_URL)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    output = (ROOT/args.output).resolve()
    if output == ROOT or not output.is_relative_to(ROOT): raise ValueError('Build output must be a dedicated directory inside the workspace')
    if args.check_only: validate(output); return
    parsed = urlsplit(args.site_url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use the canonical HTTPS project URL or owned custom domain, without credentials or query parameters')
    site_url = args.site_url.rstrip('/')+'/'
    data = json.loads((ROOT/'assets/site/project.json').read_text(encoding='utf-8'))
    content = (ROOT/'index.html').read_text(encoding='utf-8')
    for key,value in render(data).items(): content = content.replace(key,value)
    content = content.replace('__SITE_URL__', escape(site_url, quote=True))
    output.mkdir(parents=True,exist_ok=True)
    (output/'index.html').write_text(content,encoding='utf-8')
    asset_output=output/'assets/site'; asset_output.mkdir(parents=True,exist_ok=True)
    for name in ASSETS:
        source=ROOT/'assets/site'/name
        if not source.is_file(): raise ValueError('Required website asset missing: '+str(source.relative_to(ROOT)))
        shutil.copyfile(source,asset_output/name)
    try: commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError): commit=None
    (output/'build-info.json').write_text(json.dumps({'schema_version':1,'commit':commit,'built_at_utc':datetime.now(timezone.utc).isoformat(),
        'site_url':site_url,'backend_status':'UNAVAILABLE','source':'Static public repository build'},indent=2),encoding='utf-8')
    (output/'robots.txt').write_text('User-agent: *\nAllow: /\nSitemap: '+site_url+'sitemap.xml\n',encoding='utf-8')
    (output/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>'+escape(site_url)+'</loc></url></urlset>\n',encoding='utf-8')
    (output/'.nojekyll').write_text('',encoding='utf-8')
    (output/'404.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Page not found — StrictRecords</title><style>body{background:#090c12;color:#e1e9fb;font:18px/1.7 system-ui;max-width:600px;margin:15vh auto;padding:24px}a{color:#9abaff}</style><h1>This page is not here.</h1><p>The source and documentation are still available.</p><a href="'+escape(site_url)+'">Return to StrictRecords</a></html>',encoding='utf-8')
    validate(output)
    print('Built public showcase: '+str(output))


if __name__ == '__main__': main()
