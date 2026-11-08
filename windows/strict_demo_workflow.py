"""Memory-only ten-minute practice timer. Never writes a score or session ledger."""
import threading
import time
import uuid
from core.time_window_engine import now_ist
from core.strict_ipad_connection import ipad_binding,ipad_connection


class DemoStudyWorkflow:
    mode='DEMO'
    def __init__(self,db,*,clock=now_ist,monotonic=time.monotonic):
        self.db=db;self.clock=clock;self.monotonic=monotonic;self.lock=threading.RLock();self.s=None
        self._last_mono=monotonic()

    def begin(self,session_id,account_id,android_device_id=None,*,activate=False):
        now=self.clock().isoformat()
        self.s=dict(session_id=session_id,account_id=account_id,mode='DEMO',state='PREPARING',started_at=now,
            elapsed_seconds=0.,focus_target_seconds=600,focus_completed_at=None,monitoring_resumed=True,
            monitoring_ok=False,window={},sync_verified=False,manually_completed=False,reward_status='NOT_APPLICABLE',
            leftward_violations=0,max_leftward_violations=15,headphone_violations=0,bluetooth_violations=0,
            message='10-minute demo. No proof window, points, deductions or reward.',cloud_registered=False)
        if activate:self.activate()

    def bind_ipad(self,pid):
        binding=ipad_binding(self.db,pid,self.s['account_id'])
        state=ipad_connection(self.db,pid,self.s['account_id'],binding,self.clock().timestamp())
        if not state['connected']:raise ValueError(state['reason'])
        self.s.update(ios_required=True,ios_preparation_id=pid,ios_binding_hash=binding)

    def _ipad_status(self):
        if not self.s or not self.s.get('ios_required'):return dict(connected=False,reason='Join the iPad timer.')
        return ipad_connection(self.db,self.s.get('ios_preparation_id'),self.s['account_id'],self.s['ios_binding_hash'],self.clock().timestamp())

    def activate(self):
        if not self._ipad_status()['connected']:raise ValueError('Reconnect the iPad timer before starting the demo.')
        self.s.update(state='ACTIVE_FOCUS',started_at=self.clock().isoformat());self._last_mono=self.monotonic()

    def tick(self,*,monitoring_ok=True,refresh=False):
        with self.lock:
            if not self.s or self.s['state']!='ACTIVE_FOCUS':return self.view()
            now=self.monotonic();delta=max(0,now-self._last_mono);self._last_mono=now
            ipad_stat = self._ipad_status()
            ipad_conn = bool(ipad_stat.get('connected'))
            self.s['ios_connected'] = ipad_conn
            if not ipad_conn:
                self.s['ipad_control_state'] = 'IOS_STATUS_UNKNOWN'
                self.s['ipad_connection_message'] = "iOS status temporarily unavailable — session continues. Automatically checking connection."
            else:
                self.s['ipad_control_state'] = 'CONNECTED'
                self.s['ipad_connection_message'] = ipad_stat.get('reason', '')
            valid = bool(monitoring_ok and delta <= 15)
            self.s['monitoring_ok']=valid
            if valid:self.s['elapsed_seconds']=min(600,self.s['elapsed_seconds']+delta)
            if self.s['elapsed_seconds']>=600:
                self.s.update(state='SESSION_FINALIZED',focus_completed_at=self.clock().isoformat(),message='10-minute demo completed. No points or reward were created.')
            return self.view()

    def view(self):
        if not self.s:return dict(state='IDLE',mode='DEMO')
        result={k:v for k,v in self.s.items() if k!='ios_binding_hash'}
        ipad=self._ipad_status()
        ipad_msg = ipad['reason'] if ipad['connected'] else "iOS status temporarily unavailable — session continues. Automatically checking connection."
        result.update(ios_connected=ipad['connected'],ipad_connection_message=ipad_msg,
            ios_screen_sharing=ipad.get('screen_sharing', False),ios_screen_preview=ipad.get('screen_preview'),
            focus_remaining_seconds=max(0,600-self.s['elapsed_seconds']),adaptive_remaining_seconds=0,window_remaining_seconds=0,
            end_enabled=self.s['state']=='ACTIVE_FOCUS',reward_eligible=False,can_end_demo=True)
        return result

    def complete(self,session_id):
        if not self.s or self.s['session_id']!=session_id:raise ValueError('Demo identity does not match.')
        self.s.update(state='SESSION_FINALIZED',manually_completed=True,message='Demo ended. Windows restored; no points or reward were created.')
        return self.view()

    def abort(self,reason,*,failed=False):
        if self.s:self.s.update(state='FAILED' if failed else 'ABANDONED',message=reason+' No demo points or deductions.',failed_reason=reason)

    def record_leftward_violation(self,count,message=''):
        self.s['leftward_violations']=count
        if count>15:self.abort(message,failed=True)
        return dict(failed=count>15,violations=count)

    def record_headphone_violation(self,name,count):
        self.s['headphone_violations']=count
        if count>=2:self.abort('Second headphone connection ended the demo.',failed=True)
        return dict(failed=count>=2,violations=count)

    def _save(self,*_,**__):pass

    def record_integrity_violation(self,event):
        with self.lock:
            if not self.s or self.s['state']!='ACTIVE_FOCUS' or not isinstance(event,dict):return False
            if event.get('id') in self.s.get('integrity_event_ids',[]):return False
            self.s.setdefault('integrity_event_ids',[]).append(event.get('id'))
            self.s.setdefault('integrity_events',[]).append(dict(event))
            self.s['integrity_events']=self.s['integrity_events'][-200:]
            counts=self.s.setdefault('integrity_counts',{});category=event.get('category','UNKNOWN')
            counts[category]=counts.get(category,0)+1
            self.s['message']=event.get('description','Integrity warning.')
            return True
