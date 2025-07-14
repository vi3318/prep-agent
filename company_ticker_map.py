# Mapping of company names and websites to ticker symbols
COMPANY_TICKER_MAP = {
    # US Companies
    "microsoft": "MSFT",
    "apple": "AAPL",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "nike": "NKE",
    "coca-cola": "KO",
    "coca cola": "KO",
    "walmart": "WMT",
    "procter & gamble": "PG",
    "procter and gamble": "PG",
    "p&g": "PG",
    "accenture": "ACN",
    "siemens": "SIEGY",
    "toyota": "TM",
    "hsbc": "HSBC",
    # Indian Companies (NSE)
    "infosys": "INFY.NS",
    "tcs": "TCS.NS",
    "wipro": "WIPRO.NS",
    "hdfc": "HDFCBANK.NS",
    "reliance": "RELIANCE.NS",
    "hdfc bank": "HDFCBANK.NS",
    "reliance industries": "RELIANCE.NS",
    # Add more as needed
    # Website mappings
    "infosys.com": "INFY.NS",
    "tcs.com": "TCS.NS",
    "wipro.com": "WIPRO.NS",
    "hdfcbank.com": "HDFCBANK.NS",
    "relianceindustries.com": "RELIANCE.NS",
    "microsoft.com": "MSFT",
    "apple.com": "AAPL",
    "amazon.com": "AMZN",
    "nike.com": "NKE",
    "coca-cola.com": "KO",
    "walmart.com": "WMT",
    "pg.com": "PG",
    "accenture.com": "ACN",
    "siemens.com": "SIEGY",
    "toyota.com": "TM",
    "hsbc.com": "HSBC",
    # Midsize Indian Companies (NSE)
    "ltimindtree": "LTIM.NS",
    "ltimindtree.com": "LTIM.NS",
    "persistent": "PERSISTENT.NS",
    "persistent.com": "PERSISTENT.NS",
    "mindtree": "MINDTREE.NS",
    "mindtree.com": "MINDTREE.NS",
    "bajaj finance": "BAJFINANCE.NS",
    "bajajfinserv": "BAJAJFINSV.NS",
    "bajajfinserv.in": "BAJAJFINSV.NS",
    "godrej": "GODREJCP.NS",
    "godrej.com": "GODREJCP.NS",
    "titan": "TITAN.NS",
    "titan.co.in": "TITAN.NS",
    "asian paints": "ASIANPAINT.NS",
    "asianpaints.com": "ASIANPAINT.NS",
    "berger paints": "BERGEPAINT.NS",
    "bergerpaints.com": "BERGEPAINT.NS",
    "apollo hospitals": "APOLLOHOSP.NS",
    "apollohospitals.com": "APOLLOHOSP.NS",
    "page industries": "PAGEIND.NS",
    "pageind.com": "PAGEIND.NS",
    # Midsize US Companies
    "zoom": "ZM",
    "zoom.us": "ZM",
    "datadog": "DDOG",
    "datadoghq.com": "DDOG",
    "snowflake": "SNOW",
    "snowflake.com": "SNOW",
    "palantir": "PLTR",
    "palantir.com": "PLTR",
    "crowdstrike": "CRWD",
    "crowdstrike.com": "CRWD",
    "atlassian": "TEAM",
    "atlassian.com": "TEAM",
    "twilio": "TWLO",
    "twilio.com": "TWLO",
    "zendesk": "ZEN",
    "zendesk.com": "ZEN",
    "okta": "OKTA",
    "okta.com": "OKTA",
    "hubspot": "HUBS",
    "hubspot.com": "HUBS",
    "service now": "NOW",
    "servicenow.com": "NOW",
    "workday": "WDAY",
    "workday.com": "WDAY",
    "splunk": "SPLK",
    "splunk.com": "SPLK",
    "dropbox": "DBX",
    "dropbox.com": "DBX",
    "box": "BOX",
    "box.com": "BOX",
}

def get_ticker(company_name_or_url):
    key = company_name_or_url.strip().lower()
    # Try direct match
    if key in COMPANY_TICKER_MAP:
        return COMPANY_TICKER_MAP[key]
    # Try extracting domain from URL
    if key.startswith("http"):
        from urllib.parse import urlparse
        domain = urlparse(key).netloc.replace("www.", "")
        if domain in COMPANY_TICKER_MAP:
            return COMPANY_TICKER_MAP[domain]
    # Try partial match
    for k, v in COMPANY_TICKER_MAP.items():
        if k in key:
            return v
    return None 