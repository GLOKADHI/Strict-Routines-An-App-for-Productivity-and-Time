import sys

path = 'core/daily_college_decision.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target1 = """            if not values['window_start'][2]<=values['planned_time'][2] or values['planned_time'][2]+BATH['reservation_minutes']>values['window_end'][2]:
                raise GymDecisionError('INVALID_BATH_PLAN','The planned Bath time must be inside its submission window')"""

replacement1 = """            dur = 30 if (attends and occurrence == 1) else BATH['reservation_minutes']
            if not values['window_start'][2]<=values['planned_time'][2] or values['planned_time'][2]+dur>values['window_end'][2]:
                raise GymDecisionError('INVALID_BATH_PLAN','The planned Bath time must be inside its submission window')"""

assert target1 in content
content = content.replace(target1, replacement1)

target2 = """            planned,start,end=map(at,(plan['planned_time'],plan['window_start'],plan['window_end']))
            occurrence=plan['occurrence'];activity_id=f"ACT-{day.replace('-','')}-{plan['planned_time'].replace(':','')}-BATH-{occurrence}"
            details=dict(plan,is_college=record['attends_college'],reserved_start=planned.isoformat(),
                         reserved_end=(planned+timedelta(minutes=BATH['reservation_minutes'])).isoformat(),college_plan_revision=record['revision'])"""

replacement2 = """            planned,start,end=map(at,(plan['planned_time'],plan['window_start'],plan['window_end']))
            occurrence=plan['occurrence'];activity_id=f"ACT-{day.replace('-','')}-{plan['planned_time'].replace(':','')}-BATH-{occurrence}"
            if record.get('attends_college') and occurrence == 1:
                res_start = planned
                res_end = planned + timedelta(minutes=30)
                proof_start = datetime.fromisoformat(day+'T08:00:00').replace(tzinfo=zone)
                proof_end = datetime.fromisoformat(day+'T08:30:00').replace(tzinfo=zone)
                start = proof_start
                end = proof_end
            else:
                res_start = planned
                res_end = planned + timedelta(minutes=BATH['reservation_minutes'])
            details=dict(plan,is_college=record['attends_college'],reserved_start=res_start.isoformat(),
                         reserved_end=res_end.isoformat(),college_plan_revision=record['revision'],
                         proof_window_start=start.isoformat(),proof_window_end=end.isoformat())"""

assert target2 in content
content = content.replace(target2, replacement2)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated core/daily_college_decision.py")
