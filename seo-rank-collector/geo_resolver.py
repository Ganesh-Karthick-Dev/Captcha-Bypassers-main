"""
Dynamic Canonical Geo-Location Resolver for Google UULE and GPS Device Geolocation Emulation.
Powered by Google Ads Official GeoTargets Database (100,000+ Canonical Locations worldwide)
and Global 250+ Country/Territory & Metropolitan Coordinate Engine for Zero-Cost Localized SEO Crawling.
"""
import os
import re
import csv
import io
import base64
import sqlite3
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Tuple

try:
    from country_data import GLOBAL_COUNTRY_DATABASE, COUNTRY_NAME_TO_CODE
except ImportError:
    try:
        from .country_data import GLOBAL_COUNTRY_DATABASE, COUNTRY_NAME_TO_CODE
    except ImportError:
        GLOBAL_COUNTRY_DATABASE = {}
        COUNTRY_NAME_TO_CODE = {}

# Path to local embedded database
_this_dir = Path(__file__).resolve().parent
DATA_DIR = _this_dir / "data" if (_this_dir / "data").exists() else _this_dir / "seo-rank-collector" / "data"
if not DATA_DIR.exists():
    DATA_DIR = _this_dir.parent / "seo-rank-collector" / "data"
if not DATA_DIR.exists():
    DATA_DIR = Path(r"C:\Users\admin\Downloads\Captcha-Bypassers-main\Captcha-Bypassers-main\seo-rank-collector\data")

DB_PATH = DATA_DIR / "geotargets.db"


@dataclass
class GeoContext:
    canonical_name: str
    uule: str
    latitude: float
    longitude: float
    timezone_id: str
    locale: str = "en-US"
    country_code: str = "US"


def generate_uule(location: str) -> str:
    """Generates Google canonical UULE parameter for geographic targeting."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    loc_clean = (location or "").strip()
    if not loc_clean:
        return ""
    length_char = chars[len(loc_clean)] if len(loc_clean) < len(chars) else chars[-1]
    encoded = base64.b64encode(loc_clean.encode("utf-8")).decode("utf-8")
    return f"w+CAIQICI{length_char}{encoded}"


US_STATE_ABBR_MAP = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California",
    "co": "Colorado", "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia",
    "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
    "mt": "Montana", "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey",
    "nm": "New Mexico", "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio",
    "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah", "vt": "Vermont",
    "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
    "dc": "District of Columbia"
}


def normalize_country_code(country: Optional[str], location: Optional[str] = None) -> str:
    """Safely normalizes country codes, remapping US state codes (e.g. 'tx', 'ca', 'fl') to 'us'."""
    c = (country or "").strip().lower()
    loc = (location or "").strip().lower()
    
    # If 2-letter US state abbreviation was passed as country (e.g. TX, CA, FL, NY, VA)
    if c in US_STATE_ABBR_MAP and c != "in":
        return "us"
    if c == "in":
        if "indiana" in loc or " usa" in loc or "united states" in loc:
            return "us"
        return "in"
    if c in ("united states", "usa"):
        return "us"
    if c in ("united kingdom", "great britain"):
        return "uk"
    if len(c) == 2:
        return c
    return c or "us"


# State-level centroid GPS and Timezones
US_STATE_COORDINATES: Dict[str, Tuple[float, float, str]] = {
    "alabama": (32.806671, -86.791130, "America/Chicago"),
    "alaska": (61.370716, -152.404419, "America/Anchorage"),
    "arizona": (33.729759, -111.431221, "America/Phoenix"),
    "arkansas": (34.969704, -92.373123, "America/Chicago"),
    "california": (36.116203, -119.681564, "America/Los_Angeles"),
    "colorado": (39.059811, -105.311104, "America/Denver"),
    "connecticut": (41.597782, -72.755371, "America/New_York"),
    "delaware": (39.318523, -75.507141, "America/New_York"),
    "district of columbia": (38.897438, -77.026817, "America/New_York"),
    "florida": (27.766279, -81.686783, "America/New_York"),
    "georgia": (33.040619, -83.643074, "America/New_York"),
    "hawaii": (21.094318, -157.498337, "Pacific/Honolulu"),
    "idaho": (44.240459, -114.478828, "America/Boise"),
    "illinois": (40.349457, -88.986137, "America/Chicago"),
    "indiana": (39.849426, -86.258278, "America/Indiana/Indianapolis"),
    "iowa": (42.011539, -93.210526, "America/Chicago"),
    "kansas": (38.526600, -96.726486, "America/Chicago"),
    "kentucky": (37.668140, -84.670067, "America/New_York"),
    "louisiana": (31.169546, -91.867805, "America/Chicago"),
    "maine": (44.693947, -69.381927, "America/New_York"),
    "maryland": (39.063946, -76.802101, "America/New_York"),
    "massachusetts": (42.230171, -71.530106, "America/New_York"),
    "michigan": (43.326618, -84.536095, "America/Detroit"),
    "minnesota": (45.694454, -93.900192, "America/Chicago"),
    "mississippi": (32.741646, -89.678696, "America/Chicago"),
    "missouri": (38.456085, -92.288368, "America/Chicago"),
    "montana": (46.921925, -110.454353, "America/Denver"),
    "nebraska": (41.125370, -98.268082, "America/Chicago"),
    "nevada": (38.313515, -117.055374, "America/Los_Angeles"),
    "new hampshire": (43.452492, -71.563896, "America/New_York"),
    "new jersey": (40.298904, -74.521011, "America/New_York"),
    "new mexico": (34.840515, -106.248482, "America/Denver"),
    "new york": (42.165726, -74.948051, "America/New_York"),
    "north carolina": (35.630066, -79.806419, "America/New_York"),
    "north dakota": (47.528912, -99.784012, "America/Chicago"),
    "ohio": (40.388783, -82.764915, "America/New_York"),
    "oklahoma": (35.565342, -96.928917, "America/Chicago"),
    "oregon": (44.572021, -122.070938, "America/Los_Angeles"),
    "pennsylvania": (40.590752, -77.209755, "America/New_York"),
    "rhode island": (41.680893, -71.511780, "America/New_York"),
    "south carolina": (33.856892, -80.945007, "America/New_York"),
    "south dakota": (44.299782, -99.438828, "America/Chicago"),
    "tennessee": (35.747845, -86.692345, "America/Chicago"),
    "texas": (31.054487, -97.563461, "America/Chicago"),
    "utah": (40.150032, -111.862434, "America/Denver"),
    "vermont": (44.045876, -72.710686, "America/New_York"),
    "virginia": (37.769337, -78.169968, "America/New_York"),
    "washington": (47.400902, -121.490494, "America/Los_Angeles"),
    "west virginia": (38.491226, -80.954453, "America/New_York"),
    "wisconsin": (44.268543, -89.616508, "America/Chicago"),
    "wyoming": (42.755966, -107.302490, "America/Denver"),
}

# Major US & International City / Metro Centroid Database
CITY_COORDINATES: Dict[str, Tuple[float, float, str, str, str]] = {
    # format: (latitude, longitude, timezone_id, locale, country_code)
    # California
    "los angeles": (34.052235, -118.243683, "America/Los_Angeles", "en-US", "US"),
    "san francisco": (37.774929, -122.419416, "America/Los_Angeles", "en-US", "US"),
    "san diego": (32.715738, -117.161084, "America/Los_Angeles", "en-US", "US"),
    "san jose": (37.338208, -121.886329, "America/Los_Angeles", "en-US", "US"),
    "sacramento": (38.581572, -121.494399, "America/Los_Angeles", "en-US", "US"),
    "fresno": (36.746842, -119.772587, "America/Los_Angeles", "en-US", "US"),
    "long beach": (33.770050, -118.193739, "America/Los_Angeles", "en-US", "US"),
    "oakland": (37.804364, -122.271114, "America/Los_Angeles", "en-US", "US"),
    "bakersfield": (35.373292, -119.018712, "America/Los_Angeles", "en-US", "US"),
    "anaheim": (33.836593, -117.914301, "America/Los_Angeles", "en-US", "US"),
    "irvine": (33.684567, -117.826505, "America/Los_Angeles", "en-US", "US"),

    # Texas
    "dallas": (32.776664, -96.796988, "America/Chicago", "en-US", "US"),
    "houston": (29.760427, -95.369803, "America/Chicago", "en-US", "US"),
    "austin": (30.267153, -97.743061, "America/Chicago", "en-US", "US"),
    "san antonio": (29.424122, -98.493628, "America/Chicago", "en-US", "US"),
    "fort worth": (32.755488, -97.330766, "America/Chicago", "en-US", "US"),
    "el paso": (31.761878, -106.485022, "America/Denver", "en-US", "US"),
    "arlington": (32.735687, -97.108066, "America/Chicago", "en-US", "US"),
    "plano": (33.019843, -96.698886, "America/Chicago", "en-US", "US"),

    # New York
    "new york": (40.712776, -74.005974, "America/New_York", "en-US", "US"),
    "buffalo": (42.886447, -78.878369, "America/New_York", "en-US", "US"),
    "rochester": (43.156578, -77.608847, "America/New_York", "en-US", "US"),
    "albany": (42.652579, -73.756232, "America/New_York", "en-US", "US"),

    # Illinois
    "chicago": (41.878114, -87.629798, "America/Chicago", "en-US", "US"),

    # Florida
    "miami": (25.761680, -80.191790, "America/New_York", "en-US", "US"),
    "orlando": (28.538335, -81.379236, "America/New_York", "en-US", "US"),
    "tampa": (27.950575, -82.457178, "America/New_York", "en-US", "US"),
    "jacksonville": (30.332184, -81.655651, "America/New_York", "en-US", "US"),
    "fort lauderdale": (26.122439, -80.137317, "America/New_York", "en-US", "US"),

    # Washington / Virginia / DC Area
    "washington": (38.907192, -77.036871, "America/New_York", "en-US", "US"),
    "washington dc": (38.907192, -77.036871, "America/New_York", "en-US", "US"),
    "loudoun county": (39.083997, -77.649995, "America/New_York", "en-US", "US"),
    "loudoun": (39.083997, -77.649995, "America/New_York", "en-US", "US"),
    "leesburg": (39.115662, -77.563602, "America/New_York", "en-US", "US"),
    "richmond": (37.540725, -77.436048, "America/New_York", "en-US", "US"),
    "alexandria": (38.804836, -77.046921, "America/New_York", "en-US", "US"),
    "fairfax": (38.846224, -77.306373, "America/New_York", "en-US", "US"),
    "virginia beach": (36.852926, -75.977985, "America/New_York", "en-US", "US"),

    # Georgia
    "atlanta": (33.748995, -84.387982, "America/New_York", "en-US", "US"),

    # Washington State
    "seattle": (47.606209, -122.332071, "America/Los_Angeles", "en-US", "US"),

    # Colorado
    "denver": (39.739236, -104.990251, "America/Denver", "en-US", "US"),

    # Arizona
    "phoenix": (33.448377, -112.074037, "America/Phoenix", "en-US", "US"),
    "scottsdale": (33.494170, -111.926052, "America/Phoenix", "en-US", "US"),
    "tucson": (32.222607, -110.974711, "America/Phoenix", "en-US", "US"),

    # Nevada
    "las vegas": (36.169941, -115.139830, "America/Los_Angeles", "en-US", "US"),

    # Oregon
    "portland": (45.515232, -122.678385, "America/Los_Angeles", "en-US", "US"),

    # Pennsylvania
    "philadelphia": (39.952584, -75.165222, "America/New_York", "en-US", "US"),
    "pittsburgh": (40.440625, -79.995886, "America/New_York", "en-US", "US"),

    # Massachusetts
    "boston": (42.360082, -71.058880, "America/New_York", "en-US", "US"),

    # Michigan
    "detroit": (42.331427, -83.045754, "America/Detroit", "en-US", "US"),

    # Minnesota
    "minneapolis": (44.977753, -93.265011, "America/Chicago", "en-US", "US"),

    # North Carolina
    "charlotte": (35.227087, -80.843127, "America/New_York", "en-US", "US"),
    "raleigh": (35.779590, -78.638179, "America/New_York", "en-US", "US"),

    # Tennessee
    "nashville": (36.162664, -86.781602, "America/Chicago", "en-US", "US"),
    "memphis": (35.149534, -90.048980, "America/Chicago", "en-US", "US"),

    # Ohio
    "columbus": (39.961176, -82.998794, "America/New_York", "en-US", "US"),
    "cleveland": (41.499320, -81.694361, "America/New_York", "en-US", "US"),
    "cincinnati": (39.103118, -84.512020, "America/New_York", "en-US", "US"),

    # Missouri
    "kansas city": (39.099727, -94.578567, "America/Chicago", "en-US", "US"),
    "st. louis": (38.627003, -90.199404, "America/Chicago", "en-US", "US"),
    "saint louis": (38.627003, -90.199404, "America/Chicago", "en-US", "US"),

    # Maryland
    "baltimore": (39.290385, -76.612189, "America/New_York", "en-US", "US"),

    # Utah
    "salt lake city": (40.760779, -111.891047, "America/Denver", "en-US", "US"),

    # Hawaii
    "honolulu": (21.306944, -157.858334, "Pacific/Honolulu", "en-US", "US"),

    # Global Tier-1 Metros
    # United Kingdom
    "london": (51.507351, -0.127758, "Europe/London", "en-GB", "GB"),
    "manchester": (53.480759, -2.242631, "Europe/London", "en-GB", "GB"),
    "birmingham": (52.486243, -1.890401, "Europe/London", "en-GB", "GB"),
    "edinburgh": (55.953252, -3.188267, "Europe/London", "en-GB", "GB"),
    "glasgow": (55.864237, -4.251806, "Europe/London", "en-GB", "GB"),

    # France
    "paris": (48.856614, 2.352222, "Europe/Paris", "fr-FR", "FR"),
    "marseille": (43.296482, 5.369780, "Europe/Paris", "fr-FR", "FR"),
    "lyon": (45.764043, 4.835659, "Europe/Paris", "fr-FR", "FR"),

    # Germany
    "berlin": (52.520007, 13.404954, "Europe/Berlin", "de-DE", "DE"),
    "munich": (48.135125, 11.581981, "Europe/Berlin", "de-DE", "DE"),
    "frankfurt": (50.110922, 8.682127, "Europe/Berlin", "de-DE", "DE"),
    "hamburg": (53.551085, 9.993682, "Europe/Berlin", "de-DE", "DE"),
    "cologne": (50.937531, 6.960279, "Europe/Berlin", "de-DE", "DE"),

    # Italy
    "rome": (41.902783, 12.496366, "Europe/Rome", "it-IT", "IT"),
    "milan": (45.464204, 9.189982, "Europe/Rome", "it-IT", "IT"),

    # Spain
    "madrid": (40.416775, -3.703790, "Europe/Madrid", "es-ES", "ES"),
    "barcelona": (41.387917, 2.169919, "Europe/Madrid", "es-ES", "ES"),

    # Netherlands
    "amsterdam": (52.367573, 4.904139, "Europe/Amsterdam", "nl-NL", "NL"),
    "rotterdam": (51.924420, 4.477733, "Europe/Amsterdam", "nl-NL", "NL"),

    # Switzerland & Austria & Belgium
    "zurich": (47.376887, 8.541694, "Europe/Zurich", "de-CH", "CH"),
    "geneva": (46.204391, 6.143158, "Europe/Zurich", "fr-CH", "CH"),
    "vienna": (48.208174, 16.373819, "Europe/Vienna", "de-AT", "AT"),
    "brussels": (50.850346, 4.351710, "Europe/Brussels", "nl-BE", "BE"),
    "dublin": (53.349805, -6.260310, "Europe/Dublin", "en-IE", "IE"),

    # Nordics & Eastern Europe
    "stockholm": (59.329324, 18.068581, "Europe/Stockholm", "sv-SE", "SE"),
    "oslo": (59.913869, 10.752245, "Europe/Oslo", "nb-NO", "NO"),
    "copenhagen": (55.676097, 12.568337, "Europe/Copenhagen", "da-DK", "DK"),
    "helsinki": (60.169856, 24.938379, "Europe/Helsinki", "fi-FI", "FI"),
    "warsaw": (52.229676, 21.012229, "Europe/Warsaw", "pl-PL", "PL"),
    "prague": (50.075538, 14.437800, "Europe/Prague", "cs-CZ", "CZ"),
    "lisbon": (38.722252, -9.139337, "Europe/Lisbon", "pt-PT", "PT"),
    "athens": (37.983810, 23.727539, "Europe/Athens", "el-GR", "GR"),

    # Canada
    "toronto": (43.653226, -79.383184, "America/Toronto", "en-CA", "CA"),
    "vancouver": (49.282729, -123.120738, "America/Vancouver", "en-CA", "CA"),
    "montreal": (45.501689, -73.567256, "America/Toronto", "fr-CA", "CA"),
    "calgary": (51.044733, -114.071883, "America/Edmonton", "en-CA", "CA"),
    "ottawa": (45.421530, -75.697193, "America/Toronto", "en-CA", "CA"),

    # Latin America
    "mexico city": (19.432608, -99.133208, "America/Mexico_City", "es-MX", "MX"),
    "guadalajara": (20.659699, -103.349609, "America/Mexico_City", "es-MX", "MX"),
    "monterrey": (25.686614, -100.316113, "America/Monterrey", "es-MX", "MX"),
    "sao paulo": (-23.550520, -46.633309, "America/Sao_Paulo", "pt-BR", "BR"),
    "rio de janeiro": (-22.906847, -43.172896, "America/Sao_Paulo", "pt-BR", "BR"),
    "buenos aires": (-34.603722, -58.381593, "America/Argentina/Buenos_Aires", "es-AR", "AR"),
    "bogota": (4.710989, -74.072092, "America/Bogota", "es-CO", "CO"),
    "santiago": (-33.448890, -70.669265, "America/Santiago", "es-CL", "CL"),
    "lima": (-12.046374, -77.042793, "America/Lima", "es-PE", "PE"),

    # Australia & New Zealand
    "sydney": (-33.868820, 151.209296, "Australia/Sydney", "en-AU", "AU"),
    "melbourne": (-37.813628, 144.963058, "Australia/Melbourne", "en-AU", "AU"),
    "brisbane": (-27.469771, 153.025124, "Australia/Brisbane", "en-AU", "AU"),
    "perth": (-31.950527, 115.860457, "Australia/Perth", "en-AU", "AU"),
    "auckland": (-36.848460, 174.763332, "Pacific/Auckland", "en-NZ", "NZ"),
    "wellington": (-41.286460, 174.776236, "Pacific/Auckland", "en-NZ", "NZ"),

    # Asia
    "tokyo": (35.676192, 139.650311, "Asia/Tokyo", "ja-JP", "JP"),
    "osaka": (34.693725, 135.502254, "Asia/Tokyo", "ja-JP", "JP"),
    "seoul": (37.566535, 126.977969, "Asia/Seoul", "ko-KR", "KR"),
    "beijing": (39.904200, 116.407396, "Asia/Shanghai", "zh-CN", "CN"),
    "shanghai": (31.230416, 121.473701, "Asia/Shanghai", "zh-CN", "CN"),
    "hong kong": (22.319304, 114.169361, "Asia/Hong_Kong", "zh-HK", "HK"),
    "taipei": (25.032969, 121.565418, "Asia/Taipei", "zh-TW", "TW"),
    "singapore": (1.352083, 103.819836, "Asia/Singapore", "en-SG", "SG"),
    "kuala lumpur": (3.139003, 101.686855, "Asia/Kuala_Lumpur", "ms-MY", "MY"),
    "bangkok": (13.756331, 100.501765, "Asia/Bangkok", "th-TH", "TH"),
    "jakarta": (-6.208763, 106.845599, "Asia/Jakarta", "id-ID", "ID"),
    "manila": (14.599512, 120.984219, "Asia/Manila", "en-PH", "PH"),
    "hanoi": (21.028511, 105.854167, "Asia/Ho_Chi_Minh", "vi-VN", "VN"),
    "ho chi minh city": (10.823099, 106.629664, "Asia/Ho_Chi_Minh", "vi-VN", "VN"),

    # India
    "mumbai": (19.076090, 72.877426, "Asia/Kolkata", "en-IN", "IN"),
    "delhi": (28.704059, 77.102490, "Asia/Kolkata", "en-IN", "IN"),
    "new delhi": (28.613939, 77.209021, "Asia/Kolkata", "en-IN", "IN"),
    "bengaluru": (12.971599, 77.594563, "Asia/Kolkata", "en-IN", "IN"),
    "bangalore": (12.971599, 77.594563, "Asia/Kolkata", "en-IN", "IN"),
    "hyderabad": (17.385044, 78.486671, "Asia/Kolkata", "en-IN", "IN"),
    "chennai": (13.082680, 80.270718, "Asia/Kolkata", "en-IN", "IN"),
    "kolkata": (22.572646, 88.363895, "Asia/Kolkata", "en-IN", "IN"),
    "pune": (18.520430, 73.856744, "Asia/Kolkata", "en-IN", "IN"),
    "ahmedabad": (23.022505, 72.571362, "Asia/Kolkata", "en-IN", "IN"),
    "coimbatore": (11.016844, 76.955832, "Asia/Kolkata", "en-IN", "IN"),

    # Middle East
    "dubai": (25.204849, 55.270783, "Asia/Dubai", "en-AE", "AE"),
    "abu dhabi": (24.453884, 54.377344, "Asia/Dubai", "en-AE", "AE"),
    "riyadh": (24.713552, 46.675296, "Asia/Riyadh", "ar-SA", "SA"),
    "jeddah": (21.485811, 39.192505, "Asia/Riyadh", "ar-SA", "SA"),
    "doha": (25.285447, 51.531040, "Asia/Qatar", "ar-QA", "QA"),
    "kuwait city": (29.375859, 47.977405, "Asia/Kuwait", "ar-KW", "KW"),
    "tel aviv": (32.085300, 34.781768, "Asia/Jerusalem", "he-IL", "IL"),
    "jerusalem": (31.768319, 35.213710, "Asia/Jerusalem", "he-IL", "IL"),
    "istanbul": (41.008238, 28.978359, "Europe/Istanbul", "tr-TR", "TR"),

    # Africa
    "johannesburg": (-26.204103, 28.047305, "Africa/Johannesburg", "en-ZA", "ZA"),
    "cape town": (-33.924869, 18.424055, "Africa/Johannesburg", "en-ZA", "ZA"),
    "nairobi": (-1.292066, 36.821946, "Africa/Nairobi", "en-KE", "KE"),
    "lagos": (6.524379, 3.379206, "Africa/Lagos", "en-NG", "NG"),
    "cairo": (30.044420, 31.235712, "Africa/Cairo", "ar-EG", "EG"),
    "casablanca": (33.573110, -7.589843, "Africa/Casablanca", "fr-MA", "MA"),
}

DEFAULT_FALLBACK = (37.090240, -95.712891, "America/New_York", "en-US", "United States")


def ensure_geotargets_db() -> bool:
    """Ensures geotargets.db exists; if missing, automatically downloads from Google in ~3 seconds."""
    if DB_PATH.exists() and DB_PATH.stat().st_size > 1000000:
        return True

    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = "https://developers.google.com/static/google-ads/api/data/geo/geotargets-2026-08-12.csv.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            with zipfile.ZipFile(io.BytesIO(resp.read())) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    reader = csv.reader(io.TextIOWrapper(f, "utf-8"))
                    next(reader)  # Skip header

                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("CREATE TABLE IF NOT EXISTS geotargets (criteria_id INT, name TEXT, canonical_name TEXT, country_code TEXT, target_type TEXT)")
                    batch = []
                    for row in reader:
                        if len(row) > 6 and row[6] == "Active":
                            batch.append((int(row[0]), row[1], row[2], row[4], row[5]))
                    c.executemany("INSERT INTO geotargets VALUES (?, ?, ?, ?, ?)", batch)
                    c.execute("CREATE INDEX IF NOT EXISTS idx_name ON geotargets(name COLLATE NOCASE)")
                    c.execute("CREATE INDEX IF NOT EXISTS idx_country_name ON geotargets(country_code, name COLLATE NOCASE)")
                    c.execute("CREATE INDEX IF NOT EXISTS idx_canonical ON geotargets(canonical_name COLLATE NOCASE)")
                    conn.commit()
                    conn.close()
        return True
    except Exception as e:
        print(f"[GeoResolver] Warning: Could not initialize geotargets.db: {e}")
        return False


def resolve_canonical_location(location: Optional[str], country: str = "us") -> str:
    """
    Dynamically resolves any user-entered location (city, county, DMA, province)
    to Google's official canonical geo-target string using the embedded database.
    """
    if not location:
        return ""

    loc_str = location.strip()
    if "|" in loc_str:
        loc_str = loc_str.split("|")[-1].strip()

    loc_str = re.sub(r"^[0-9_\-\s]+", "", loc_str).strip()
    if not loc_str:
        return ""

    # Clean country code
    country_code = (country or "us").upper().strip()

    # 1. Handle Nielsen Broadcast DMA patterns like "Washington DC (Hagerstown MD)"
    if "(" in loc_str and ")" in loc_str:
        m_trailing_state = re.search(r",\s*([a-zA-Z\s]+)$", loc_str)
        if m_trailing_state:
            return f"{loc_str}"

        if "washington" in loc_str.lower() and "hagerstown" in loc_str.lower():
            return f"{loc_str}, Virginia"

    # Ensure database is available
    if not ensure_geotargets_db():
        return loc_str

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Strategy 1: Check if input ends with a 2-letter state abbreviation (e.g. "Austin, TX", "Richmond, VA")
        m_state = re.search(r",\s*([a-zA-Z]{2})\s*$", loc_str)
        if m_state and country_code == "US":
            abbr = m_state.group(1).lower()
            if abbr in US_STATE_ABBR_MAP:
                full_state = US_STATE_ABBR_MAP[abbr]
                base_city = loc_str[:m_state.start()].strip()

                query = "SELECT canonical_name FROM geotargets WHERE canonical_name LIKE ? AND country_code = 'US' ORDER BY CASE target_type WHEN 'City' THEN 1 WHEN 'County' THEN 2 ELSE 3 END LIMIT 1"
                c.execute(query, (f"{base_city},%{full_state}%",))
                row = c.fetchone()
                if row:
                    conn.close()
                    return row[0]

        # Strategy 2: If input already contains comma (e.g. "Loudoun County, Virginia", "Coimbatore, Tamil Nadu")
        if "," in loc_str:
            first_part = loc_str.split(",")[0].strip()
            second_part = loc_str.split(",")[1].strip()
            query = "SELECT canonical_name FROM geotargets WHERE canonical_name LIKE ? AND country_code = ? ORDER BY CASE target_type WHEN 'City' THEN 1 WHEN 'County' THEN 2 ELSE 3 END LIMIT 1"
            c.execute(query, (f"{first_part},%{second_part}%", country_code))
            row = c.fetchone()
            if row:
                conn.close()
                return row[0]

        # Strategy 3: Single name match
        if loc_str.lower() in ("washington", "washington dc", "washington d.c.", "dc"):
            c.execute("SELECT canonical_name FROM geotargets WHERE canonical_name LIKE 'Washington,%District of Columbia%' AND country_code = 'US' LIMIT 1")
            row = c.fetchone()
            if row:
                conn.close()
                return row[0]

        query = """
            SELECT canonical_name 
            FROM geotargets 
            WHERE country_code = ? AND name = ? COLLATE NOCASE
            ORDER BY CASE target_type WHEN 'City' THEN 1 WHEN 'County' THEN 2 ELSE 3 END
            LIMIT 1
        """
        c.execute(query, (country_code, loc_str))
        row = c.fetchone()
        if row:
            conn.close()
            return row[0]

        # Strategy 4: Fuzzy Prefix match
        query = """
            SELECT canonical_name 
            FROM geotargets 
            WHERE country_code = ? AND name LIKE ? 
            ORDER BY CASE target_type WHEN 'City' THEN 1 WHEN 'County' THEN 2 ELSE 3 END
            LIMIT 1
        """
        c.execute(query, (country_code, f"{loc_str}%"))
        row = c.fetchone()
        if row:
            conn.close()
            return row[0]

        conn.close()
    except Exception as e:
        print(f"[GeoResolver] Error querying geotargets.db: {e}")

    # Fallback to original string
    return loc_str


def resolve_geo_context(location: Optional[str], country: str = "us") -> GeoContext:
    """
    Fully resolves an input location to:
    1. Official Google Ads Canonical GeoTarget name
    2. Google UULE Base64 string
    3. Hardware GPS Coordinates (Latitude & Longitude)
    4. IANA Timezone ID (e.g. America/Los_Angeles, Europe/Berlin, Asia/Tokyo)
    5. Regional Locale (e.g. en-US, de-DE, ja-JP)
    6. Country Code (all 250+ ISO countries supported)
    """
    clean_country = (country or "us").upper().strip()

    # Determine country defaults from the 250-country database
    if clean_country in GLOBAL_COUNTRY_DATABASE:
        def_lat, def_lon, def_tz, def_locale, def_cname = GLOBAL_COUNTRY_DATABASE[clean_country]
    else:
        def_lat, def_lon, def_tz, def_locale, def_cname = DEFAULT_FALLBACK

    if not location or not location.strip():
        return GeoContext(
            canonical_name="",
            uule="",
            latitude=def_lat,
            longitude=def_lon,
            timezone_id=def_tz,
            locale=def_locale,
            country_code=clean_country,
        )

    # 1. Resolve canonical name and UULE
    canonical_name = resolve_canonical_location(location, country=clean_country)
    uule_val = generate_uule(canonical_name)

    # 2. Extract city/metro token for GPS lookup
    import unicodedata
    def _strip_accents(s: str) -> str:
        return unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")

    raw_tokens = [t.strip().lower() for t in location.split(",") if t.strip()]
    canonical_tokens = [t.strip().lower() for t in canonical_name.split(",") if t.strip()]
    
    # Include accent-normalized tokens (e.g. São Paulo -> sao paulo, Zürich -> zurich)
    all_tokens = raw_tokens + canonical_tokens
    normalized_tokens = [_strip_accents(t) for t in all_tokens]
    all_tokens.extend([t for t in normalized_tokens if t not in all_tokens])

    # Priority 1: Check known city database
    for token in all_tokens:
        clean_tok = re.sub(r"\b(city|county|township|borough|district|prefecture|province)\b", "", token).strip()
        if clean_tok in CITY_COORDINATES:
            lat, lon, tz, loc, c_code = CITY_COORDINATES[clean_tok]
            return GeoContext(
                canonical_name=canonical_name,
                uule=uule_val,
                latitude=lat,
                longitude=lon,
                timezone_id=tz,
                locale=loc,
                country_code=c_code,
            )
        if token in CITY_COORDINATES:
            lat, lon, tz, loc, c_code = CITY_COORDINATES[token]
            return GeoContext(
                canonical_name=canonical_name,
                uule=uule_val,
                latitude=lat,
                longitude=lon,
                timezone_id=tz,
                locale=loc,
                country_code=c_code,
            )

    # Priority 2: Check US States
    for token in all_tokens:
        clean_st = token.strip()
        if clean_st in US_STATE_ABBR_MAP:
            clean_st = US_STATE_ABBR_MAP[clean_st].lower()
        if clean_st in US_STATE_COORDINATES:
            lat, lon, tz = US_STATE_COORDINATES[clean_st]
            return GeoContext(
                canonical_name=canonical_name,
                uule=uule_val,
                latitude=lat,
                longitude=lon,
                timezone_id=tz,
                locale="en-US",
                country_code="US",
            )

    # Priority 3: Match Country by Name across tokens (e.g. "Germany", "France", "Japan", "Brazil")
    for token in all_tokens:
        clean_tok = token.strip().lower()
        if clean_tok in COUNTRY_NAME_TO_CODE:
            matched_code = COUNTRY_NAME_TO_CODE[clean_tok]
            if matched_code in GLOBAL_COUNTRY_DATABASE:
                lat, lon, tz, loc, _ = GLOBAL_COUNTRY_DATABASE[matched_code]
                return GeoContext(
                    canonical_name=canonical_name,
                    uule=uule_val,
                    latitude=lat,
                    longitude=lon,
                    timezone_id=tz,
                    locale=loc,
                    country_code=matched_code,
                )

    # Priority 4: Direct Country Code match in GLOBAL_COUNTRY_DATABASE
    if clean_country in GLOBAL_COUNTRY_DATABASE:
        lat, lon, tz, loc, _ = GLOBAL_COUNTRY_DATABASE[clean_country]
        return GeoContext(
            canonical_name=canonical_name,
            uule=uule_val,
            latitude=lat,
            longitude=lon,
            timezone_id=tz,
            locale=loc,
            country_code=clean_country,
        )

    # Priority 5: Fall back to default
    return GeoContext(
        canonical_name=canonical_name,
        uule=uule_val,
        latitude=def_lat,
        longitude=def_lon,
        timezone_id=def_tz,
        locale=def_locale,
        country_code=clean_country,
    )
