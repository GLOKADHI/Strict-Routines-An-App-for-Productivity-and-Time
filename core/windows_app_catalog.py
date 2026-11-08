"""Explicit Windows target categories; no discovery-based entertainment guesses."""
import json
import hashlib

BROWSERS={'chrome.exe':'Google Chrome','msedge.exe':'Microsoft Edge','brave.exe':'Brave',
          'opera.exe':'Opera','ccleanerbrowser.exe':'CCleaner Browser','comet.exe':'Comet',
          'vivaldi.exe':'Vivaldi','arc.exe':'Arc','firefox.exe':'Mozilla Firefox'}
VIDEO_PLAYERS={'vlc.exe':'VLC Media Player','wmplayer.exe':'Windows Media Player',
               'microsoft.media.player.exe':'Windows Media Player','video.ui.exe':'Movies & TV',
               'mpv.exe':'mpv','potplayermini64.exe':'PotPlayer','potplayermini.exe':'PotPlayer'}
MUSIC_PLAYERS={'spotify.exe':'Spotify','music.ui.exe':'Groove Music','musicbee.exe':'MusicBee',
               'foobar2000.exe':'foobar2000','aimp.exe':'AIMP'}
CATALOG=dict(BROWSERS,**VIDEO_PLAYERS,**MUSIC_PLAYERS)
DAYTIME_ENTERTAINMENT=dict(VIDEO_PLAYERS,**MUSIC_PLAYERS)
WINDOWS_WEBSITES={'netflix':dict(name='Netflix',domain='netflix.com')}


def apply_production_catalog(db):
    """Append explicit targets to the existing saved lists without other edits.

    The normal ScoringEngine/RestrictionManager remain the access authority.
    Keep the existing local-music classification and its threshold unchanged.
    """
    from windows.night_shutdown import NightStore,canonical
    state=NightStore();key='catalog_night_browsers_v2_'+hashlib.sha256(str(db.db_path).encode()).hexdigest()
    with state.connect() as c:
        if c.execute('SELECT 1 FROM night_config WHERE key=?',(key,)).fetchone():return
    with db._connect() as c:
        rows=list(c.execute('SELECT user_id,settings_json FROM user_settings'))
        for row in rows:
            settings=json.loads(row['settings_json']);old=[v for v in settings.get('restricted_windows_apps',[]) if str(v).lower().strip() not in BROWSERS]
            # A media executable already classified as local music stays there.
            local={str(v).lower() for v in settings.get('local_music_windows_apps',[])}
            additions=[name for name in DAYTIME_ENTERTAINMENT if name not in local]
            settings['restricted_windows_apps']=old+[name for name in additions if name not in {str(v).lower() for v in old}]
            c.execute('UPDATE user_settings SET settings_json=? WHERE user_id=?',(json.dumps(settings,ensure_ascii=False),row['user_id']))
    with state.connect() as c:c.execute('INSERT OR IGNORE INTO night_config VALUES(?,?)',(key,canonical(dict(applied=True))))
    return dict(browsers=BROWSERS,browser_enforcement='NIGHT_LOCK_ONLY_AFTER_23_21',video_players=VIDEO_PLAYERS,music_players=MUSIC_PLAYERS,mode='PRODUCTION',policy='EXISTING_AUTHORITATIVE_ACCESS_POLICY')
