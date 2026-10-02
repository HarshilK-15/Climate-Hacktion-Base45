import json

def validate_json(filepath):
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            print(f"✅ {filepath} is valid JSON ({len(data)} top-level keys/items)")
            return True
    except Exception as e:
        print(f"❌ {filepath} validation failed: {e}")
        return False

validate_json('data/mock_portfolio.json')
validate_json('data/mock_alerts.json')