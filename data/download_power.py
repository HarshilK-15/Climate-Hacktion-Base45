import os
import time
import requests
import pandas as pd

# Task 4.1: Target Pacific Island Sites
ISLANDS = [
    {"name": "funafuti", "lat": -8.52, "lon": 179.20},
    {"name": "suva", "lat": -18.14, "lon": 178.44},
    {"name": "nukualofa", "lat": -21.14, "lon": -175.20},
    {"name": "port_vila", "lat": -17.73, "lon": 168.32},
    {"name": "tarawa", "lat": 1.45, "lon": 173.03},
    {"name": "apia", "lat": -13.85, "lon": -171.75}
]

# Ensure output directory exists
OUTPUT_DIR = os.path.join("data", "out")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_nasa_data(island):
    """
    Fetches solar irradiance data from NASA POWER API for a given island location.
    """
    name = island["name"]
    lat = island["lat"]
    lon = island["lon"]
    
    print(f"\n📡 Fetching NASA solar data for {name.capitalize()} (Lat: {lat}, Lon: {lon})...")
    
    # NASA POWER API endpoint (hourly point data)
    # Fetching 2023-2024 recent hourly dataset for fast download
    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN",  # All Sky Surface Shortwave Downward Irradiance (Wh/m^2)
        "community": "RE",                     # Renewable Energy community
        "longitude": lon,
        "latitude": lat,
        "start": "20230101",                  # YYYYMMDD
        "end": "20241231",                    # YYYYMMDD
        "format": "JSON",
        "time-standard": "LST",               # local solar time, so noon is noon (matches island load)
    }

    retries = 3
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                hourly_dict = data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
                
                # Convert JSON dict to pandas DataFrame
                df = pd.DataFrame(list(hourly_dict.items()), columns=["timestamp", "ghi_wm2"])
                
                # Replace NASA's missing value code (-999) with 0.0
                df["ghi_wm2"] = df["ghi_wm2"].apply(lambda x: 0.0 if x == -999 else float(x))
                
                # Format timestamp nicely (YYYYMMDDHH -> YYYY-MM-DDTHH:00:00). No "Z": this is
                # local solar time, not UTC
                df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y%m%d%H").dt.strftime("%Y-%m-%dT%H:%M:%S")
                
                output_filepath = os.path.join(OUTPUT_DIR, f"{name}_ghi.csv")
                df.to_csv(output_filepath, index=False)
                
                print(f"✅ Successfully saved {len(df)} rows to {output_filepath}")
                return True
            else:
                print(f"⚠️ NASA API returned status code {response.status_code}. Attempt {attempt}/{retries}...")
        except Exception as e:
            print(f"❌ Error fetching data for {name}: {e}. Attempt {attempt}/{retries}...")
        
        if attempt < retries:
            print("⏳ Retrying in 5 seconds...")
            time.sleep(5)
            
    print(f"❌ Failed to download data for {name} after {retries} attempts.")
    return False

def main():
    print("=" * 60)
    print("🚀 Starting NASA POWER Satellite Weather Downloader")
    print("=" * 60)
    
    # Install pandas if missing prompt helper
    try:
        import pandas
    except ImportError:
        print("❌ 'pandas' library is required. Install it using 'pip install pandas'")
        return

    success_count = 0
    for island in ISLANDS:
        if fetch_nasa_data(island):
            success_count += 1
        time.sleep(1)  # Respectful pause between requests
        
    print("\n" + "=" * 60)
    print(f"🎉 Completed! Downloaded weather data for {success_count}/{len(ISLANDS)} islands.")
    print("=" * 60)

if __name__ == "__main__":
    main()