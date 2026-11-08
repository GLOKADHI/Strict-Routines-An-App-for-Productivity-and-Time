"""Presentation regressions; fixtures never submit attendance or edit the ledger."""
import tkinter as tk

import pytest

from windows.notification_style import (
    RestrictionCard, ToastClock, category_presentation, mascot_path,
    popup_geometry, score_presentation, stacked_layout, toast_geometry,
)


@pytest.mark.parametrize("scale",[1,1.25,1.5,2,2.5])
@pytest.mark.parametrize("bounds",[(0,0,1920,1040),(0,0,800,560),(-1280,0,0,680),(1920,0,3000,1800)])
def test_notification_geometry_stays_in_monitor_work_area(bounds,scale):
    for geometry in (popup_geometry(bounds,scale),toast_geometry(bounds,scale,900)):
        width,height,x,y=geometry
        assert width>0 and height>0
        assert bounds[0] <= x < x+width <= bounds[2]
        assert bounds[1] <= y < y+height <= bounds[3]


def test_hover_pauses_actual_dismissal_and_progress():
    clock=ToastClock(5,100)
    assert clock.tick(102)==pytest.approx(.6)
    assert clock.tick(120,paused=True)==pytest.approx(.6)
    assert clock.tick(122)==pytest.approx(.2)
    assert clock.tick(123)==0
    assert clock.tick(90)==0  # A backwards input cannot restore elapsed time.


def test_negative_and_unknown_scores_stay_truthful():
    assert score_presentation(-20,80)==('-20','Unlocks at 80 points',0)
    assert score_presentation(None,60)==('Not synced','60 points to unlock',0)
    assert score_presentation(60,80)[2]==.75
    assert mascot_path().is_file()


@pytest.mark.parametrize("category",['TASKS','PROOF','POINTS','PRIVILEGES','FOCUS','ANDROID','SYNC','SYSTEM'])
def test_every_category_uses_shared_visual_language(category):
    text,color=category_presentation(category)
    assert text and color.startswith('#')
    assert 'STRICTRECORDS' not in text


@pytest.mark.parametrize("scale,width,height",[(1,900,510),(1,480,560),(1.5,1200,800),(2,760,900)])
def test_responsive_card_keeps_action_visible_and_content_scrollable(scale,width,height):
    root=tk.Tk();root.withdraw()
    window=tk.Toplevel(root)
    root.tk.call('tk','scaling',scale*96/72)
    window.geometry(f'{width}x{height}')
    calls=[]
    card=RestrictionCard(window,lambda: calls.append('home'),scale)
    card.update({'score':-20,'required_score':80,'target_app':'YouTube','reason':'A detailed restriction reason. '*12})
    try:
        root.update()
        root.update_idletasks()
        assert card._stacked is stacked_layout(width,scale)
        assert card.button.winfo_ismapped()
        assert card.button.winfo_rooty()+card.button.winfo_height() <= window.winfo_rooty()+height
        assert card.copy.winfo_reqheight()>0
        assert card.image is not None
        assert card.score.cget('text')=='-20 / 100'
        card.button.invoke()
        assert calls==['home']
    finally:
        window.destroy();root.destroy()
