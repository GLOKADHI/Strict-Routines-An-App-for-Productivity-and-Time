const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('core/ui_dashboard.py', 'utf8');

test('dashboard never duplicates a website extension popup, even with stale visibility flags',()=>{
  for (const state of ['LOCK_SCREEN_ACTIVE','WAITING_FOR_RELEASE']) {
    const overlay={style:{display:'flex'}},banner={style:{display:'block'}};
    const context=vm.createContext({document:{getElementById:id=>id==='youtubeLockScreenOverlay'?overlay:id==='lockReleaseBanner'?banner:null},
      updateRestrictionSummary(){},lockDismissedByUser:false});
    vm.runInContext(declaration('updateRestrictionUI'),context);
    context.updateRestrictionUI({state,popup_owner:'BROWSER_EXTENSION',popup_visible:true});
    assert.equal(overlay.style.display,'none');assert.equal(banner.style.display,'none');
    context.updateRestrictionUI({state:'LOCK_SCREEN_ACTIVE',popup_owner:'NATIVE_APP',popup_visible:true});
    assert.equal(overlay.style.display,'flex');
  }
});
function declaration(name) {
  const match = source.match(new RegExp('^function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
  assert.ok(match, name + ' declaration exists');
  return match[0];
}
function element() {
  return {textContent: '', style: {}, dataset: {}, children: [], attributes: {},
    classList: {add() {}, remove() {}, toggle() {}},
    setAttribute(key, value) { this.attributes[key] = value; },
    querySelector() { return null; },
    append(...children) { this.children.push(...children); },
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; }};
}
function setup() {
  const elements = {deficitBadge: element(), restrictionTargetList: element(), restrictionTargetCount: element(), strictModeBadge: element()};
  const context = vm.createContext({document: {getElementById: id => elements[id] || null, createElement: element},
    lastConfirmedRestrictionState: {}, currentScore: 0});
  for (const name of ['restrictionNumber', 'restrictionPointsNeeded', 'restrictionNativeLabel',
    'compactRestrictionDecision', 'restrictionTargetAccess', 'renderRestrictionTargets', 'updateRestrictionSummary', 'renderCockpitState']) {
    vm.runInContext(declaration(name), context);
  }
  return {context, elements};
}
test('daily score refresh cannot overwrite the restriction points message or color', () => {
  const {context, elements} = setup();
  context.updateRestrictionSummary({current_score: -10, required_score: 80, remaining_points: 90, allowed: false});
  assert.equal(elements.deficitBadge.textContent, 'Need 90 more points');
  assert.equal(elements.deficitBadge.style.color, 'var(--warning)');
  context.renderCockpitState({daily_record: {final_score: 100}});
  assert.equal(elements.deficitBadge.textContent, 'Need 90 more points');
  assert.equal(elements.deficitBadge.style.color, 'var(--warning)');
});
test('music display uses configured thresholds, including custom settings', () => {
  const {context} = setup();
  const target = {id: 'spotify', defaultThreshold: 60, configuredThreshold: 95};
  assert.equal(context.restrictionTargetAccess(target, 70, {}).label, 'Need 25 more points');
  assert.equal(context.restrictionTargetAccess(target, 95, {}).className, 'is-allowed');
  assert.equal(context.restrictionTargetAccess({...target, configuredThreshold: null}, 70, {}).className, 'is-unknown');
});
test('browser card retains confirmed policy decisions even above score threshold', () => {
  const {context} = setup();
  assert.equal(context.restrictionTargetAccess({id: 'youtube', configuredThreshold: 80}, 100,
    {youtube: {effectiveAccessAllowed: false, enforcementStatus: 'APPLIED'}}).className, 'is-locked');
});
test('monitoring diagnostics remain available inside a collapsed details section', () => {
  const {context, elements} = setup();
  context.renderRestrictionTargets({current_score: -10, configured_targets: [
    {id: 'masstamilan', name: 'masstamilan.dev', category: 'Music/Website Target', configuredThreshold: 60,
      domains: ['masstamilan.dev'], websiteMonitoring: 'NATIVE_BROWSER_CONTROLS'}]});
  const card = elements.restrictionTargetList.children[0];
  assert.equal(card.children[1].children[1].textContent, 'Unlock threshold: 60 points');
  const details = card.children[2];
  assert.equal(details.className, 'restriction-target-details');
  assert.equal(details.children[0].textContent, 'Monitoring details');
  assert.equal(details.attributes.open, undefined);
  assert.match(details.children[2].textContent, /native desktop controls/);
});
test('unavailable score and score met with another restriction keep truthful labels', () => {
  const {context, elements} = setup();
  context.updateRestrictionSummary({current_score: null, required_score: 80, score_available: false});
  assert.equal(elements.deficitBadge.textContent, 'Points required cannot be confirmed.');
  context.updateRestrictionSummary({current_score: 90, required_score: 80, remaining_points: 0, allowed: false});
  assert.equal(elements.deficitBadge.textContent, 'Score threshold met · restriction remains active');
  assert.equal(elements.deficitBadge.style.color, 'var(--warning)');
});

test('summary uses live browser access instead of saved current-target allowance', () => {
  const {context, elements} = setup();
  context.lastConfirmedRestrictionState = {
    youtube:{effectiveAccessAllowed:false,enforcementStatus:'APPLIED'},
    instagram:{effectiveAccessAllowed:false,enforcementStatus:'APPLIED'}
  };
  const data = {current_score:-3,required_score:80,allowed:true,configured_targets:[{id:'youtube'},{id:'instagram'},{id:'spotify',configuredThreshold:60}]};
  context.updateRestrictionSummary(data);
  assert.equal(elements.strictModeBadge.textContent, '● TOTAL LOCKED');
  context.lastConfirmedRestrictionState.youtube.effectiveAccessAllowed=true;
  context.updateRestrictionSummary(data);
  assert.equal(elements.strictModeBadge.textContent, '● PARTIAL ACCESS');
  context.lastConfirmedRestrictionState.youtube.enforcementStatus='FAILED';
  context.updateRestrictionSummary(data);
  assert.equal(elements.strictModeBadge.textContent, '● STATUS UNKNOWN');
});
