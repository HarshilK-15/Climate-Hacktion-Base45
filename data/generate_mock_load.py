import csv
from datetime import datetime, timedelta

# Starting timestamp
start_time = datetime(2026, 10, 2, 0, 0, 0)

# Hourly patterns (24 hours) - relative multiplier for typical daily load
# Low overnight, morning bump, mid-day lull, sharp evening peak
hourly_pattern = [
    0.4, 0.35, 0.3, 0.3, 0.35, 0.5,   # 00:00 - 05:00
    0.8, 1.2,  1.1, 0.9, 0.85, 0.8,   # 06:00 - 11:00
    0.85, 0.9, 0.95, 1.0, 1.3,  1.8,   # 12:00 - 17:00
    2.1, 1.9,  1.5, 1.1, 0.7,  0.5    # 18:00 - 23:00
]

base_load_kw = 10.0  # Base load in kW

with open('data/mock_load_profile.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['timestamp', 'load_kw'])
    
    for hour in range(168):  # 7 days * 24 hours
        current_time = start_time + timedelta(hours=hour)
        pattern_val = hourly_pattern[hour % 24]
        
        # Add slight variation (+/- 5%)
        variation = 1.0 + (((hour * 7) % 11) - 5) / 100.0
        load_kw = round(base_load_kw * pattern_val * variation, 2)
        
        writer.writerow([current_time.strftime('%Y-%m-%dT%H:%M:%SZ'), load_kw])

print("Created data/mock_load_profile.csv with 168 rows.")