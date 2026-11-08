"""Isolated Edge rendering with every API mocked; no user profile or app writes."""
import pytest
from core.ui_dashboard import DASHBOARD_HTML


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,channel='msedge')
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page=browser.new_page(viewport={'width':1280,'height':800})
    def route(request):
        request.fulfill(status=200,content_type='text/html' if request.request.url=='http://dashboard.test/' else 'application/json',
                        body=DASHBOARD_HTML if request.request.url=='http://dashboard.test/' else '{}')
    page.route('**/*',route)
    page.goto('http://dashboard.test/',wait_until='load')
    page.evaluate('''() => {
      window.transferTestCalls={toast:0,state:0,history:0,devices:0,hourly:0};
      showToast=()=>transferTestCalls.toast++;
      loadState=()=>transferTestCalls.state++;
      loadTransactionsHistory=()=>transferTestCalls.history++;
      loadRegisteredDevicesList=()=>transferTestCalls.devices++;
      loadHourlyAdminSubmissions=()=>transferTestCalls.hourly++;
    }''')
    yield page
    page.close()


def status(**changes):
    value=dict(available=True,active_session=False,paired_devices=[dict(address='AA:BB:CC:DD:EE:01',name='Fixture phone')])
    value.update(changes);return value


def receipt():
    return dict(success=True,status='SUCCESS',package_id='fixture-package',session_id='current',synced_at='2026-10-18T10:00:00Z',
        transferred_count=1,already_present_count=2,rejected_count=0,conflict_count=0,confirmed_score=4,ledger_receipt={'accepted':True})


def test_dashboard_sections_are_present_even_when_services_return_no_data(page):
    assert page.locator('#page-dashboard').is_visible()
    assert page.locator('.app-main').inner_text().strip()
    page.evaluate("navAppPage('history')")
    assert page.locator('#page-history').is_visible()
    page.evaluate("navAppPage('dashboard')")
    assert page.locator('#page-dashboard').is_visible()


def test_historical_receipt_never_completes_a_new_active_session(page):
    page.evaluate('data=>renderBluetoothStatus(data)',status(active_session=True,session_id='current',session_code='123456',
        peer_name='Fixture phone',state='TRANSFERRING',received_bytes=10,total_bytes=100,expires_in=60,last_receipt=receipt()))
    assert '10 / 100 bytes' in page.locator('#btSessionStatus').text_content()
    assert page.evaluate('transferTestCalls.toast')==0


def test_committed_receipt_refreshes_history_and_dashboard_once(page):
    page.evaluate("btCurrentSessionId='current'")
    page.evaluate('data=>renderBluetoothStatus(data)',status(last_receipt=receipt()))
    page.evaluate('data=>renderBluetoothStatus(data)',status(last_receipt=receipt()))
    calls=page.evaluate('transferTestCalls')
    assert calls==dict(toast=1,state=1,history=1,devices=1,hourly=1)
    assert 'Already present 2' in page.locator('#btSessionStatus').text_content()
    assert 'Confirmed balance 4' in page.locator('#btSessionStatus').text_content()


def test_peer_names_are_text_and_receiver_unavailability_disables_start(page):
    data=status(available=False,message='Bluetooth disabled',paired_devices=[dict(address='AA:BB:CC:DD:EE:01',name='<script>window.injected=true</script>')])
    page.evaluate('data=>renderBluetoothStatus(data)',data)
    assert page.locator('#btnBtStartSession').is_disabled()
    assert page.locator('#btPeerSelect').input_value()==''
    assert page.evaluate('window.injected===undefined')
    assert 'Bluetooth disabled' in page.locator('#btSessionStatus').text_content()


def test_pending_metadata_is_explicit_and_does_not_look_fully_applied(page):
    page.evaluate("document.getElementById('bluetoothTransferModal').style.display='flex'; btCurrentSessionId='current'")
    data = receipt()
    data['pending_metadata_count'] = 1
    page.evaluate('data=>renderBluetoothStatus(data)', status(last_receipt=data))
    assert 'Pending reconciliation 1' in page.locator('#btSessionStatus').text_content()
    assert 'Confirmed balance 4' in page.locator('#btSessionStatus').text_content()
    assert not page.locator('#btQueueMetadata').is_checked()
    page.evaluate('data=>renderBluetoothStatus(data)', status(active_session=True, metadata_policy='QUEUE_RECONCILIATION',
        state='WAITING_FOR_CONNECTION', peer_name='Fixture phone', session_code='123456', expires_in=180))
    assert page.locator('#btQueueMetadata').is_checked() and page.locator('#btQueueMetadata').is_disabled()
