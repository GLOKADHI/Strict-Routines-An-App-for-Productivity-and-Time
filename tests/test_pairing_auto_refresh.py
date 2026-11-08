"""Clock-controlled real JavaScript rendering; APIs and device names are fixtures."""
import pytest
from core.ui_dashboard import DASHBOARD_HTML


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        instance = p.chromium.launch(headless=True, channel='msedge')
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html' if route.request.url == 'http://fixture.test/' else 'application/json',
        body=DASHBOARD_HTML if route.request.url == 'http://fixture.test/' else '{}'))
    page.goto('http://fixture.test/')
    page.clock.install()
    page.evaluate('''() => {
      window.pairCalls=0; window.pairFailure=false; window.pairDelay=false;
      window.resolvePair=null;
      apiPost=async()=>{
        pairCalls++;
        if(pairFailure) throw Error('Fixture disconnected');
        if(pairDelay) await new Promise(resolve=>{resolvePair=resolve;});
        return {session_key:'SR-FIXTURE-'+pairCalls,manual_pin:'123456',expires_in_seconds:3,
          qr_code_image:'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>'};
      };
      loadRegisteredDevicesList=()=>{}; loadAutomaticBluetoothStatus=()=>{};
      document.getElementById('page-dashboard').classList.remove('active');
      document.getElementById('page-devices').classList.add('active');
    }''')
    yield page
    page.close()


def test_expired_card_refreshes_all_surfaces_automatically(page):
    page.evaluate('regeneratePairingSession()')
    assert page.evaluate('pairCalls') == 1
    page.clock.fast_forward(4000)
    page.wait_for_function('pairCalls === 2 && activePairingSessionKey === "SR-FIXTURE-2"')
    assert 'SR-FIXTURE-2' in page.locator('#inlinePairingKeyDisplay').text_content()
    assert 'SR-FIXTURE-2' in page.locator('#enlargedKeyDisplay').text_content()
    assert 'refresh automatically' in page.locator('#inlinePairingStatus').text_content()


def test_dialog_close_keeps_inline_expiry_alive(page):
    page.evaluate('regeneratePairingSession()')
    page.evaluate("document.getElementById('connectAndroidModal').style.display='flex'; closeConnectAndroidModal()")
    page.clock.fast_forward(4000)
    page.wait_for_function('pairCalls === 2')


def test_only_one_creation_request_when_refreshes_overlap(page):
    page.evaluate('''() => { pairDelay=true; regeneratePairingSession(); regeneratePairingSession(); ensurePairingSession(); }''')
    assert page.evaluate('pairCalls') == 1
    page.evaluate('resolvePair()')
    page.wait_for_function('activePairingSessionKey !== null')


def test_disconnected_server_clears_stale_qr_and_retries(page):
    page.evaluate('regeneratePairingSession()')
    page.evaluate('pairFailure=true')
    page.clock.fast_forward(4000)
    page.wait_for_function('pairCalls === 2 && pairingCreatePromise === null')
    assert page.locator('#inlinePairingQrImg').get_attribute('src') is None
    assert 'retrying automatically' in page.locator('#inlinePairingStatus').text_content()
    page.evaluate('pairFailure=false')
    page.clock.fast_forward(6000)
    page.wait_for_function('pairCalls === 3 && activePairingSessionKey !== null')


def test_hidden_page_does_not_create_sessions_and_resume_uses_real_expiry(page):
    page.evaluate('regeneratePairingSession()')
    page.evaluate("document.getElementById('page-devices').classList.remove('active')")
    page.clock.fast_forward(60000)
    assert page.evaluate('pairCalls') == 1
    page.evaluate("document.getElementById('page-devices').classList.add('active'); ensurePairingSession()")
    page.wait_for_function('pairCalls === 2')


def test_pairing_used_once_does_not_keep_rotating_and_name_is_text(page):
    page.route('**/api/device/pairing/status?*', lambda route: route.fulfill(json={'status':'USED','device_name':'<img src=x onerror=alert(1)>'}))
    page.evaluate('regeneratePairingSession()')
    page.clock.fast_forward(2000)
    page.wait_for_function('pairingCompleted === true')
    assert '<img' in page.locator('#inlinePairingStatus').text_content()
    assert page.locator('#inlinePairingStatus img').count() == 0
    page.clock.fast_forward(60000)
    assert page.evaluate('pairCalls') == 1
