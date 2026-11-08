with open('core/ui_dashboard.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'modal' in line.lower() and ('class=' in line or 'style=' in line):
            print(f'{i+1}: {line.strip()[:100]}')
            if i > 5000: break
