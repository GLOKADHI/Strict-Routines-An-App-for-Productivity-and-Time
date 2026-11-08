const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function playbackFixture({restricted = false, denyNative = false} = {}) {
  const classes = new Set(restricted ? ['strictrecords-yt-restricted'] : []);
  const events = {};
  const classList = {contains: name => classes.has(name), remove: (...names) => names.forEach(n => classes.delete(n))};
  class Media {
    constructor() {this.tagName = 'VIDEO';this.paused = true;this.calls = 0;this.playbackRate = 1;}
    pause() {this.paused = true;}
    play() {
      this.calls++;
      return denyNative ? Promise.reject(new DOMException('User interaction required', 'NotAllowedError')) : Promise.resolve();
    }
  }
  const media = new Media();
  const document = {documentElement: {classList}, body: {classList}, getElementById: () => null, querySelectorAll: () => [media]};
  const window = {location: new URL('https://www.youtube.com/watch?v=abcdefghijk'), addEventListener: (name, fn) => events[name] = fn};
  window.top = window;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../browser_extension/yt_main_world.js'), 'utf8'), {
    window, document, HTMLMediaElement: Media, DOMException, Promise,
    MutationObserver: class {observe() {}}, setInterval: () => 1, clearInterval() {}
  });
  return {media, events, window};
}

test('browser autoplay rejection is handled but still rejects for its caller', async () => {
  const f = playbackFixture({denyNative: true});
  // Leave the first returned rejection unobserved by the caller: our wrapper
  // must prevent an unhandledRejection without treating it as successful play.
  f.media.play();
  await new Promise(resolve => setImmediate(resolve));
  await assert.rejects(f.media.play(), {name: 'NotAllowedError'});
  assert.equal(f.media.calls, 2);
});

test('restricted playback remains rejected and never calls native play', async () => {
  const f = playbackFixture({restricted: true});
  f.media.play();
  await new Promise(resolve => setImmediate(resolve));
  await assert.rejects(f.media.play(), {name: 'NotAllowedError'});
  assert.equal(f.media.calls, 0);
  assert.equal(f.media.paused, true);
});

test('educational unlock removes restrictions without forcing autoplay', async () => {
  const f = playbackFixture({restricted: true});
  f.events.message({source: f.window, data: {type: 'STRICTRECORDS_UNLOCK_PLAYBACK'}});
  assert.equal(f.media.calls, 0);
  await f.media.play();
  assert.equal(f.media.calls, 1);
});

test('manifest does not request optional hosts already granted by required hosts', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../browser_extension/manifest.json')));
  assert.equal(manifest.manifest_version, 3);
  if (manifest.host_permissions.includes('<all_urls>')) {
    assert.equal((manifest.optional_host_permissions || []).some(p => ['https://*/*', 'http://*/*'].includes(p)), false);
  }
});
