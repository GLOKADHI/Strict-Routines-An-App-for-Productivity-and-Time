"""Session-local, debounced observations. No scoring or reward operations."""
from datetime import datetime, timezone
import threading


class IntegrityEpisodes:
    def __init__(self,enabled=True,session_id=None):
        self.lock=threading.RLock();self.sequence=0;self.events=[];self.episodes={};self.enabled=enabled
        self.session_id=session_id

    def set_session_id(self,session_id):
        with self.lock:self.session_id=session_id

    def reset(self):
        with self.lock:self.sequence=0;self.events=[];self.episodes={}

    def record(self,category,message,*,source='WINDOWS',confidence=None,**extra):
        with self.lock:
            if not self.enabled:return None
            self.sequence+=1
            now_dt = datetime.now(timezone.utc)
            local_time_str = datetime.now().strftime('%I:%M:%S %p')
            is_resolved = bool(extra.get('resolved', False))
            event=dict(id=f'{source.lower()}-{self.sequence}',sequence=self.sequence,
                session_id=self.session_id,
                timestampIso=now_dt.isoformat(),
                timestamp_formatted=local_time_str,
                category=category,
                severity='LOW' if is_resolved else 'MEDIUM',
                description=message,
                resolved=is_resolved,
                status_badge='RESOLVED' if is_resolved else 'ACTIVE',
                source=source)
            if confidence is not None:event['confidence']=round(float(confidence),3)
            for k,v in extra.items():
                if v is not None:event[k]=v
            self.events.append(event);self.events=self.events[-200:]
            return event

    def observe(self,category,present,now,*,hold=5.,max_gap=2.,message='',confidence=None,**extra):
        """Unknown evidence breaks pending time but cannot re-arm an active event."""
        with self.lock:
            if not self.enabled:return None
            row=self.episodes.setdefault(category,dict(start=None,last=None,clear=None,latched=False))
            if present is None:
                row.update(start=None,last=None,clear=None);return None
            if row['last'] is not None and (now<row['last'] or now-row['last']>max_gap):
                row.update(start=None,clear=None)
            row['last']=now
            if present:
                row['clear']=None
                if row['start'] is None:row['start']=now
                if not row['latched'] and now-row['start']>=hold:
                    row['latched']=True
                    return self.record(category,message,source='CAMERA',confidence=confidence,**extra)
            else:
                row['start']=None
                if row['clear'] is None:row['clear']=now
                elif now-row['clear']>=1.:
                    was_latched=row['latched']
                    row['latched']=False
                    now_iso = datetime.now(timezone.utc).isoformat()
                    now_time = datetime.now().strftime('%I:%M:%S %p')
                    if category == 'FACE_MISSING':
                        for ev in self.events:
                            if ev.get('category') == 'FACE_MISSING' and not ev.get('resolved'):
                                ev['resolved'] = True
                                ev['resolvedAtIso'] = now_iso
                                ev['resolved_time'] = now_time
                                ev['status_badge'] = 'RESOLVED'
                    elif was_latched and category == 'MOBILE_PHONE_DETECTED':
                        for ev in self.events:
                            if ev.get('category') == 'MOBILE_PHONE_DETECTED' and not ev.get('resolved'):
                                ev['resolved'] = True
                                ev['resolvedAtIso'] = now_iso
                                ev['resolved_time'] = now_time
                                ev['status_badge'] = 'RESOLVED'
                        self.record('MOBILE_PHONE_DETECTION_CLEARED', 'Mobile phone detection cleared.', source='CAMERA')
            return None

    def snapshot(self):
        with self.lock:return [dict(event) for event in self.events]
