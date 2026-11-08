"""No UI, camera, browser automation or session state required."""
from windows.strict_focus_layout import StudyPaneLayout


def test_default_split_and_swap_preserve_two_page_identities():
    layout=StudyPaneLayout();bounds=layout.geometry(1280,700)
    assert bounds['chatgpt']==(0,0,636,700) and bounds['leetcode']==(644,0,636,700)
    assert layout.drop('chatgpt',1000,100,1280,700)
    assert layout.order==['leetcode','chatgpt'] and set(layout.geometry(1280,700))=={'chatgpt','leetcode','divider'}


def test_drop_outside_the_window_or_on_same_side_cannot_detach_or_create_tabs():
    layout=StudyPaneLayout()
    for key,x,y in [('chatgpt',100,20),('chatgpt',-1,20),('chatgpt',1500,20),('chatgpt',1100,900),('third',1000,20)]:
        assert not layout.drop(key,x,y,1280,700)
    assert layout.order==['chatgpt','leetcode']


def test_divider_clamps_both_panes_and_survives_window_resize():
    layout=StudyPaneLayout()
    for x in (-900,0,100,1000,99999):
        assert layout.resize(x,1280)
        bounds=layout.geometry(1280,700)
        assert bounds['chatgpt'][2]>=320 and bounds['leetcode'][2]>=320
        assert sum(bounds[key][2] for key in ('chatgpt','leetcode','divider'))==1280
    assert layout.geometry(900,500)['chatgpt'][2]>=320


def test_small_windows_use_one_tab_and_expand_back_without_losing_order():
    layout=StudyPaneLayout();layout.swap();assert layout.select('leetcode')
    assert layout.geometry(600,400)=={'leetcode':(0,0,600,400),'divider':None}
    assert not layout.resize(100,600) and not layout.drop('leetcode',100,20,600,400)
    assert len(layout.geometry(1200,500))==3 and layout.order==['leetcode','chatgpt']
    layout.side_by_side=False
    assert layout.geometry(1200,500)=={'leetcode':(0,0,1200,500),'divider':None}
    assert not layout.select('outside') and layout.active=='leetcode'
