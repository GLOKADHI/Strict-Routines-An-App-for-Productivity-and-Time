import math
import pytest
from windows.strict_zip_frontend import scaled_rect,trusted_ui_source


@pytest.mark.parametrize('url',[
    'https://strict-session.local/','https://strict-session.local/index.html?host=1',
])
def test_only_local_chrome_origin_can_send_host_commands(url):
    assert trusted_ui_source(url)


@pytest.mark.parametrize('url',[
    'http://strict-session.local/','https://strict-session.local.attacker.example/',
    'https://chatgpt.com/','https://strict-session.local:8765/',
    'https://user:password@strict-session.local/', 'file:///index.html',
])
def test_website_documents_cannot_send_native_commands(url):
    assert not trusted_ui_source(url)


def test_css_coordinates_scale_and_clip_to_actual_native_client():
    assert scaled_rect(dict(x=20,y=60,width=600,height=400),dict(width=1280,height=720),1920,1080)==(30,90,900,600)
    assert scaled_rect(dict(x=-10,y=700,width=2000,height=200),dict(width=1280,height=720),1280,720)==(0,700,1280,20)


@pytest.mark.parametrize('invalid',[math.nan,math.inf,-math.inf])
def test_nonfinite_layout_is_rejected(invalid):
    with pytest.raises(ValueError):
        scaled_rect(dict(x=invalid,y=0,width=100,height=100),dict(width=1280,height=720),1280,720)


def test_zero_viewport_is_rejected():
    with pytest.raises(ValueError):
        scaled_rect({},dict(width=0,height=720),1280,720)
