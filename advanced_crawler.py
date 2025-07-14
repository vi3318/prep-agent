import requests
from bs4 import BeautifulSoup
import tldextract
import fitz  # PyMuPDF
import io
import re
from urllib.parse import urljoin, urlparse
import os
import feedparser
import urllib.parse
from graphviz import Digraph
import asyncio
from crawl4ai import AsyncWebCrawler
import matplotlib.pyplot as plt
import collections
import matplotlib
matplotlib.use('Agg')

# --- CONFIG ---
MAX_INTERNAL_PAGES = 100  # Safety limit for full crawl
PDF_MAX_PAGES = 10
GNEWS_API_KEY = os.getenv('GNEWS_API_KEY')

# --- 1. Company Name to Website (DuckDuckGo, no API key) ---
def resolve_company_website_duckduckgo(company_name):
    import urllib.parse
    q = f"{company_name} official site"
    resp = requests.get(f"https://duckduckgo.com/html/?q={urllib.parse.quote(q)}")
    soup = BeautifulSoup(resp.text, "html.parser")
    results = soup.find_all('a', {'class': 'result__a'}, href=True)
    if results:
        href = results[0]['href']
        # DuckDuckGo sometimes returns a redirect link like /l/?uddg=...
        if 'duckduckgo.com/l/?uddg=' in href:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            real_url = qs.get('uddg', [None])[0]
            if real_url:
                return real_url
        # If it's a direct URL, return as is
        if href.startswith('http'):
            return href
    return None

# --- 2. Internal Multi-Page Crawler ---
# Use crawl4ai for robust, AI-friendly, full-site crawling

def crawl_internal_pages(base_url):
    async def crawl():
        async with AsyncWebCrawler() as crawler:
            # Crawl all internal pages up to max_pages, no subpage filtering
            results = await crawler.arun(url=base_url, max_pages=MAX_INTERNAL_PAGES, follow_external_links=False)
            texts = []
            pdf_texts = []
            pages = getattr(results, 'content', None) or getattr(results, 'data', None)
            if pages:
                for page in pages:
                    url = getattr(page, 'url', None)
                    text = getattr(page, 'text', '') or ""
                    if text:
                        texts.append({'url': url, 'text': text})
                    if url and url.lower().endswith('.pdf'):
                        pdf_texts.append(text)
            else:
                text = getattr(results, 'markdown', '')
                if text:
                    texts.append({'url': base_url, 'text': text})
            print(f"[crawl4ai] Crawled {len(texts)} pages. Example URLs:")
            for t in texts[:5]:
                print(f"  {t['url']} (chars: {len(t['text'])})")
            return texts, pdf_texts
    return asyncio.run(crawl())

def is_internal_link(link, domain):
    parsed = urlparse(link)
    return tldextract.extract(parsed.netloc).registered_domain == domain

def is_relevant_subpage(url):
    return any(x in url.lower() for x in ["/about", "/leadership", "/investor", "/product", "/news", "/team", "/management"])

def extract_main_text(soup):
    # Remove nav, footer, scripts, styles
    for tag in soup(["nav", "footer", "script", "style", "aside", "form"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Clean up excessive whitespace
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text[:5000]  # Limit per page

def extract_pdf_text(pdf_bytes):
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for i, page in enumerate(doc):
            if i >= PDF_MAX_PAGES:
                break
            text += page.get_text()
        return text[:5000]
    except Exception:
        return ""

# --- 3. News Fetching (GNews API, fallback to Google News RSS) ---
def fetch_gnews(company_name):
    url = f'https://gnews.io/api/v4/search?q={urllib.parse.quote(company_name)}&lang=en&token={GNEWS_API_KEY}'
    resp = requests.get(url)
    data = resp.json()
    news = []
    for article in data.get('articles', [])[:5]:
        news.append({
            'title': article['title'],
            'description': article.get('description', ''),
            'url': article['url'],
            'source': article.get('source', {}).get('name', 'GNews')
        })
    return news

def fetch_google_news(company_name):
    q = urllib.parse.quote(company_name)
    url = f'https://news.google.com/rss/search?q={q}'
    feed = feedparser.parse(url)
    news = []
    for entry in feed.entries[:5]:
        news.append({
            'title': entry.title,
            'description': entry.summary,
            'url': entry.link,
            'source': 'Google News'
        })
    return news

# --- 4. LinkedIn Leadership Info (Stub) ---
def fetch_linkedin_leadership(company_name):
    # TODO: Integrate with SerpAPI, BrightData, or LinkedIn scraper
    # For now, return a stub
    return [
        {"name": "Natarajan Chandrasekaran", "role": "Chairman"},
        {"name": "T. V. Narendran", "role": "CEO & MD"},
        # ... more roles
    ]

def extract_leadership_from_website(website_url):
    """Crawl About/Leadership/Management/IR pages and extract names/roles."""
    import re
    from urllib.parse import urljoin
    import requests
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(website_url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        # Find links to likely leadership pages
        leadership_links = []
        for a in soup.find_all('a', href=True):
            href = a['href'].lower()
            if any(x in href for x in ["leadership", "management", "team", "about", "executive", "board", "ir", "investor"]):
                full_url = href if href.startswith('http') else urljoin(website_url, href)
                leadership_links.append(full_url)
        # Deduplicate
        leadership_links = list(set(leadership_links))
        # Try each page for names/roles
        leadership = []
        for link in leadership_links[:3]:  # Limit to 3 pages
            try:
                page = requests.get(link, timeout=10)
                psoup = BeautifulSoup(page.text, "html.parser")
                # Look for patterns: Name (Role), or Role: Name, or cards with both
                for tag in psoup.find_all(['p', 'li', 'div', 'span', 'h2', 'h3', 'h4']):
                    text = tag.get_text(" ", strip=True)
                    # Simple regex: Name, Role
                    m = re.match(r"([A-Z][a-zA-Z .'-]+)[,\-–]+\s*([A-Z][a-zA-Z &]+)", text)
                    if m:
                        name, role = m.group(1).strip(), m.group(2).strip()
                        if len(name.split()) >= 2 and len(role) > 2:
                            leadership.append({"name": name, "role": role})
                    # Role: Name
                    m2 = re.match(r"([A-Z][a-zA-Z &]+)[:\-]+\s*([A-Z][a-zA-Z .'-]+)", text)
                    if m2:
                        role, name = m2.group(1).strip(), m2.group(2).strip()
                        if len(name.split()) >= 2 and len(role) > 2:
                            leadership.append({"name": name, "role": role})
            except Exception:
                continue
        # Deduplicate by name+role
        seen = set()
        unique_leadership = []
        for l in leadership:
            key = (l['name'].lower(), l['role'].lower())
            if key not in seen:
                unique_leadership.append(l)
                seen.add(key)
        return unique_leadership
    except Exception:
        return []

def fetch_wikipedia_leadership(company_name):
    import requests
    # Step 1: Search for the company page
    search_url = f"https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": company_name,
        "format": "json"
    }
    resp = requests.get(search_url, params=params)
    data = resp.json()
    if not data["query"]["search"]:
        return []
    page_title = data["query"]["search"][0]["title"]
    # Step 2: Get the infobox from the page HTML
    page_url = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
    resp = requests.get(page_url)
    if resp.status_code != 200:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    infobox = soup.find('table', {'class': 'infobox'})
    leadership = []
    if infobox:
        for row in infobox.find_all('tr'):
            header = row.find('th')
            if header and any(x in header.text.lower() for x in ['key people', 'ceo', 'chairman', 'founder', 'president', 'cfo', 'cto', 'coo']):
                cell = row.find('td')
                if cell:
                    # Split by <br> or ; or ,
                    for part in cell.stripped_strings:
                        # Try to split role and name
                        if ':' in part:
                            role, name = part.split(':', 1)
                            leadership.append({"name": name.strip(), "role": role.strip()})
                        elif '-' in part:
                            role, name = part.split('-', 1)
                            leadership.append({"name": name.strip(), "role": role.strip()})
                        else:
                            leadership.append({"name": part.strip(), "role": header.text.strip()})
    return leadership

def extract_leadership_with_gemini(content, company_name):
    """
    Use Gemini to extract the executive leadership team from the provided content.
    The prompt instructs Gemini to:
    - Use all available context (website, Wikipedia, news, etc.)
    - Extract CEO, CFO, CTO, Chairman, President, Founder, and other C-level roles
    - Return a JSON list of {"name": ..., "role": ...} objects
    - If not found, return an empty list
    """
    from summarizer import init_gemini
    import json
    prompt = f"""
You are an expert business analyst. Your task is to extract the names and roles of the executive leadership team for the company below, using all available context (website, Wikipedia, news, etc.).

Instructions:
- Focus on C-level executives (CEO, CFO, CTO, COO, CMO, CIO, President, Chairman, Founder, Managing Director, etc.) and other key leadership roles.
- If the content contains a table, list, or paragraph with names and roles, extract them.
- If the information is scattered, infer and aggregate it.
- If multiple people share a role (e.g., co-founders), include all.
- If the content does not specify a role but mentions a person as a leader, include them with the best inferred role.
- If no leadership info is found, return an empty list.

Output format:
Return a JSON array of objects, each with 'name' and 'role' fields. Example:
[
  {"name": "Jane Doe", "role": "CEO"},
  {"name": "John Smith", "role": "CFO"}
]

Company: {company_name}

Content:
{content}
"""
    model = init_gemini()  # or your Gemini integration
    response = model.generate_content(prompt)
    try:
        leadership = json.loads(response.text)
        if isinstance(leadership, list):
            return leadership
    except Exception:
        pass
    return []

# Update fetch_leadership_info to use SerpAPI as the final fallback

def fetch_leadership_info(company_name, website):
    # No SerpAPI fallback, just return empty or use other methods if needed
    return []

def fetch_wikipedia_summary(company_name):
    import requests
    # Step 1: Search for the company page
    search_url = f"https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": company_name,
        "format": "json"
    }
    resp = requests.get(search_url, params=params)
    data = resp.json()
    if not data["query"]["search"]:
        return ""
    page_title = data["query"]["search"][0]["title"]
    # Step 2: Get the summary and first section
    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{page_title.replace(' ', '_')}"
    resp = requests.get(summary_url)
    if resp.status_code != 200:
        return ""
    data = resp.json()
    extract = data.get("extract", "")
    return extract

def fetch_yahoo_finance_summary(company_name):
    # Try to resolve ticker using Yahoo Finance search
    search_url = f'https://query2.finance.yahoo.com/v1/finance/search?q={company_name}'
    try:
        resp = requests.get(search_url, timeout=10)
        data = resp.json()
        if not data.get('quotes'):
            return ""
        ticker = data['quotes'][0]['symbol']
        # Fetch summary page
        summary_url = f'https://finance.yahoo.com/quote/{ticker}/profile'
        page = requests.get(summary_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        soup = BeautifulSoup(page.text, 'html.parser')
        # Business summary
        summary = ""
        try:
            summary = soup.find('section', {'data-test': 'qsp-profile'}).find('p').get_text()
        except Exception:
            pass
        # Industry/sector
        try:
            sector = soup.find('span', string='Sector').find_next('span').get_text()
            industry = soup.find('span', string='Industry').find_next('span').get_text()
        except Exception:
            sector = industry = ""
        # Market cap (from summary page)
        stats_url = f'https://finance.yahoo.com/quote/{ticker}/key-statistics'
        stats_page = requests.get(stats_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        stats_soup = BeautifulSoup(stats_page.text, 'html.parser')
        market_cap = ""
        try:
            mc_row = stats_soup.find('span', string='Market Cap (intraday)').find_parent('tr')
            market_cap = mc_row.find_all('td')[-1].get_text()
        except Exception:
            pass
        result = ""
        if summary:
            result += f"Summary: {summary}\n"
        if sector or industry:
            result += f"Sector: {sector}\nIndustry: {industry}\n"
        if market_cap:
            result += f"Market Cap: {market_cap}\n"
        return result.strip()
    except Exception as e:
        return ""

def generate_financial_charts_slack(ticker):
    import yfinance as yf
    yf_ticker = yf.Ticker(ticker)
    fin = yf_ticker.financials
    if fin.empty:
        return []
    # Revenue and Net Income for last 3 years
    charts = []
    years = fin.columns[:3][::-1]  # Most recent 3 years, in order
    # Revenue
    if 'Total Revenue' in fin.index:
        rev = fin.loc['Total Revenue', years] / 1e9  # Billions
        plt.figure(figsize=(4,3))
        plt.bar(years, rev, color='#4682B4')
        plt.title('Revenue (USD Billions)')
        plt.ylabel('USD Billions')
        plt.tight_layout()
        rev_path = f'downloads/{ticker}_revenue.png'
        plt.savefig(rev_path)
        plt.close()
        charts.append(rev_path)
    # Net Income
    if 'Net Income' in fin.index:
        ni = fin.loc['Net Income', years] / 1e9
        plt.figure(figsize=(4,3))
        plt.bar(years, ni, color='#2E8B57')
        plt.title('Net Income (USD Billions)')
        plt.ylabel('USD Billions')
        plt.tight_layout()
        ni_path = f'downloads/{ticker}_netincome.png'
        plt.savefig(ni_path)
        plt.close()
        charts.append(ni_path)
    return charts

# Refactor fetch_yahoo_finance_trends to return chart paths as well

def fetch_yahoo_finance_trends(company_name, website_url=None, internal_texts=None, pdf_texts=None):
    from company_ticker_map import get_ticker
    import os
    ticker = get_ticker(company_name)
    if ticker is None:
        # fallback to yfinance logic as before
        try:
            import yfinance as yf
            search_url = f'https://query2.finance.yahoo.com/v1/finance/search?q={company_name}'
            resp = requests.get(search_url, timeout=10)
            data = resp.json()
            if not data.get('quotes'):
                raise Exception('No ticker found')
            ticker = data['quotes'][0]['symbol']
        except Exception:
            ticker = None
    # Detect if US ticker (no .NS, .BO, .L, .TO, etc.)
    is_us = ticker and ('.' not in ticker or ticker.endswith('.N') or ticker.endswith('.O') or ticker.endswith('.A') or ticker.endswith('.K') or ticker.endswith('.M') or ticker.endswith('.P') or ticker.endswith('.Q') or ticker.endswith('.V') or ticker.endswith('.X') or ticker.endswith('.Y') or ticker.endswith('.Z') or ticker.endswith('.B') or ticker.endswith('.C') or ticker.endswith('.D') or ticker.endswith('.E') or ticker.endswith('.F') or ticker.endswith('.G') or ticker.endswith('.H') or ticker.endswith('.I') or ticker.endswith('.J') or ticker.endswith('.L') or ticker.endswith('.R') or ticker.endswith('.S') or ticker.endswith('.T') or ticker.endswith('.U') or ticker.endswith('.W'))
    is_india = ticker and (ticker.endswith('.NS') or ticker.endswith('.BO'))
    if is_us and ticker:
        try:
            from polygon import RESTClient
            import datetime
            api_key = os.getenv('POLYGON_API_KEY')
            client = RESTClient(api_key)
            details = client.get_ticker_details(ticker)
            trends = []
            if details:
                if hasattr(details, 'market_cap'):
                    trends.append(f"Market Cap: ${details.market_cap:,}")
                if hasattr(details, 'total_employees'):
                    trends.append(f"Employees: {details.total_employees}")
                if hasattr(details, 'description'):
                    trends.append(f"Description: {details.description}")
            fundamentals = client.get_stock_financials(ticker, limit=3)
            years = []
            revenues = []
            net_incomes = []
            if hasattr(fundamentals, 'results'):
                for f in fundamentals.results:
                    if hasattr(f, 'fiscal_year') and hasattr(f, 'income_statement'):
                        years.append(str(f.fiscal_year))
                        rev = f.income_statement.get('revenues')
                        ni = f.income_statement.get('net_income')
                        revenues.append(rev)
                        net_incomes.append(ni)
                        trends.append(f"Revenue {f.fiscal_year}: ${rev:,}" if rev else f"Revenue {f.fiscal_year}: N/A")
                        trends.append(f"Net Income {f.fiscal_year}: ${ni:,}" if ni else f"Net Income {f.fiscal_year}: N/A")
            chart_data = []
            if years and revenues:
                chart = generate_revenue_chart(years, revenues)
                if chart:
                    chart_data.append(chart)
            if years and net_incomes:
                chart = generate_netincome_chart(years, net_incomes)
                if chart:
                    chart_data.append(chart)
            today = datetime.date.today()
            start = today.replace(year=today.year-3)
            aggs = list(client.list_aggs(ticker, 1, "month", start.isoformat(), today.isoformat(), limit=36))
            if aggs:
                months = [datetime.datetime.fromtimestamp(a.timestamp/1000).strftime('%Y-%m') for a in aggs]
                closes = [a.close for a in aggs]
                chart = generate_price_trend_chart(months, closes)
                if chart:
                    chart_data.append(chart)
            return trends, chart_data
        except Exception as e:
            pass
    elif is_india and ticker:
        try:
            from alpha_vantage.timeseries import TimeSeries
            import datetime
            api_key = os.getenv('ALPHA_VANTAGE_API_KEY')
            ts = TimeSeries(key=api_key, output_format='pandas')
            symbol = ticker.replace('.NS', '') if ticker.endswith('.NS') else ticker.replace('.BO', '')
            av_ticker = f'NSE:{symbol}' if ticker.endswith('.NS') else f'BSE:{symbol}'
            data, meta = ts.get_daily(symbol=av_ticker, outputsize='compact')
            data = data.sort_index()
            trends = []
            chart_data = []
            if not data.empty:
                data_last3y = data.tail(750)
                chart = generate_price_trend_chart(data_last3y.index.strftime('%Y-%m-%d').tolist(), data_last3y['4. close'].tolist())
                if chart:
                    chart_data.append(chart)
                years = sorted(set([d.year for d in data_last3y.index]), reverse=True)[:3]
                for y in years:
                    closes = data_last3y[data_last3y.index.year == y]['4. close']
                    if not closes.empty:
                        trends.append(f'Close Price {y}: ₹{closes.iloc[-1]:,.2f}')
            return trends, chart_data
        except Exception as e:
            pass
    try:
        import yfinance as yf
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info
        trends = []
        def fmt(val):
            if val is None:
                return 'N/A'
            if isinstance(val, (int, float)):
                if abs(val) > 1e9:
                    return f"${val/1e9:.2f}B"
                elif abs(val) > 1e6:
                    return f"${val/1e6:.2f}M"
                else:
                    return f"${val:,.0f}"
            return str(val)
        trends.append(f"Market Cap: {fmt(info.get('marketCap'))}")
        trends.append(f"Revenue (ttm): {fmt(info.get('totalRevenue'))}")
        trends.append(f"Net Income (ttm): {fmt(info.get('netIncomeToCommon'))}")
        trends.append(f"Trailing P/E: {fmt(info.get('trailingPE'))}")
        trends.append(f"Forward P/E: {fmt(info.get('forwardPE'))}")
        trends.append(f"Price/Sales: {fmt(info.get('priceToSalesTrailing12Months'))}")
        trends.append(f"Price/Book: {fmt(info.get('priceToBook'))}")
        trends.append(f"EV/EBITDA: {fmt(info.get('enterpriseToEbitda'))}")
        trends.append(f"EPS (TTM): {fmt(info.get('trailingEps'))}")
        trends.append(f"Revenue Growth (YoY): {fmt(info.get('revenueGrowth'))}")
        if info.get('recommendationKey'):
            trends.append(f"Analyst Recommendation: {info['recommendationKey'].capitalize()}")
        trends = [t for t in trends if 'N/A' not in t]
        chart_data = []
        # Try to get 3 years of revenue/net income from yfinance financials
        fin = yf_ticker.financials
        if not fin.empty:
            years = fin.columns[:3][::-1]
            if 'Total Revenue' in fin.index:
                rev = fin.loc['Total Revenue', years].tolist()
                chart = generate_revenue_chart(years.tolist(), rev)
                if chart:
                    chart_data.append(chart)
            if 'Net Income' in fin.index:
                ni = fin.loc['Net Income', years].tolist()
                chart = generate_netincome_chart(years.tolist(), ni)
                if chart:
                    chart_data.append(chart)
        # Price trend (last 3 years)
        hist = yf_ticker.history(period='3y')
        if not hist.empty:
            chart = generate_price_trend_chart(hist.index.strftime('%Y-%m-%d').tolist(), hist['Close'].tolist())
            if chart:
                chart_data.append(chart)
        return trends, chart_data
    except Exception:
        if internal_texts is None or pdf_texts is None:
            return [], []
        all_texts = [t['text'] for t in internal_texts] + pdf_texts
        fin_dict, years = extract_financials_from_texts(all_texts)
        if not fin_dict:
            return [], []
        import matplotlib.pyplot as plt
        charts = []
        for metric in ['Revenue', 'Net Profit']:
            vals = [(y, fin_dict.get(f"{metric} {y}")) for y in years if fin_dict.get(f"{metric} {y}")]
            if vals:
                y_labels, v_labels = zip(*vals)
                def parse_val(v):
                    v = v.replace(',', '').replace('INR', '').replace('₹', '').replace('$', '').strip()
                    if 'billion' in v.lower():
                        return float(re.findall(r'[\d.]+', v)[0]) * 1e9
                    if 'million' in v.lower():
                        return float(re.findall(r'[\d.]+', v)[0]) * 1e6
                    if 'crore' in v.lower():
                        return float(re.findall(r'[\d.]+', v)[0]) * 1e7
                    if 'lakh' in v.lower():
                        return float(re.findall(r'[\d.]+', v)[0]) * 1e5
                    try:
                        return float(re.findall(r'[\d.]+', v)[0])
                    except Exception:
                        return None
                values = [parse_val(v) for v in v_labels]
                if any(values):
                    chart = generate_revenue_chart(y_labels, values) if metric == 'Revenue' else generate_netincome_chart(y_labels, values)
                    if chart:
                        charts.append(chart)
        trends = [f"{k}: {v}" for k, v in fin_dict.items()]
        return trends, charts

def extract_financials_from_texts(texts):
    # Try to extract revenue, net profit, and growth for up to 3 years from a list of texts
    import re
    import collections
    # Patterns for INR, USD, EUR, etc.
    revenue_pat = re.compile(r'(?:Revenue|Total Revenue|Turnover)[^\d$€₹]*([\$€₹]?[\d,.]+\s*(?:million|billion|crore|lakh|mn|bn)?)(?:[^\d\w]|$)', re.IGNORECASE)
    profit_pat = re.compile(r'(?:Net Profit|Net Income|Profit after Tax)[^\d$€₹]*([\$€₹]?[\d,.]+\s*(?:million|billion|crore|lakh|mn|bn)?)(?:[^\d\w]|$)', re.IGNORECASE)
    growth_pat = re.compile(r'(?:YoY Growth|Growth|Increase)[^\d-]*([\d.]+%)', re.IGNORECASE)
    year_pat = re.compile(r'(20\d{2})')
    # Store by year if possible
    revenue_by_year = collections.defaultdict(str)
    profit_by_year = collections.defaultdict(str)
    growth_by_year = collections.defaultdict(str)
    for text in texts:
        for m in revenue_pat.finditer(text):
            # Try to find year nearby
            span = m.span()
            before = text[max(0, span[0]-20):span[0]]
            after = text[span[1]:span[1]+20]
            year = None
            for y in year_pat.findall(before+after):
                year = y
                break
            val = m.group(1).strip()
            if year:
                revenue_by_year[year] = val
            else:
                revenue_by_year['latest'] = val
        for m in profit_pat.finditer(text):
            span = m.span()
            before = text[max(0, span[0]-20):span[0]]
            after = text[span[1]:span[1]+20]
            year = None
            for y in year_pat.findall(before+after):
                year = y
                break
            val = m.group(1).strip()
            if year:
                profit_by_year[year] = val
            else:
                profit_by_year['latest'] = val
        for m in growth_pat.finditer(text):
            span = m.span()
            before = text[max(0, span[0]-20):span[0]]
            after = text[span[1]:span[1]+20]
            year = None
            for y in year_pat.findall(before+after):
                year = y
                break
            val = m.group(1).strip()
            if year:
                growth_by_year[year] = val
            else:
                growth_by_year['latest'] = val
    # Pick up to 3 most recent years
    years = sorted(set(list(revenue_by_year.keys()) + list(profit_by_year.keys()) + list(growth_by_year.keys())), reverse=True)[:3]
    if not years:
        return {}, []
    result = {}
    for y in years:
        if revenue_by_year.get(y):
            result[f"Revenue {y}"] = revenue_by_year[y]
        if profit_by_year.get(y):
            result[f"Net Profit {y}"] = profit_by_year[y]
        if growth_by_year.get(y):
            result[f"Growth {y}"] = growth_by_year[y]
    return result, years

def generate_revenue_chart(years, revenues):
    if len(years) < 2:
        return None
    fig, ax = plt.subplots(figsize=(4,3))
    ax.bar(years, [r/1e9 for r in revenues], color='#4682B4')
    ax.set_title('Revenue (USD Billions)')
    ax.set_ylabel('USD Billions')
    ax.set_xlabel('Year')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf

def generate_netincome_chart(years, net_incomes):
    if len(years) < 2:
        return None
    fig, ax = plt.subplots(figsize=(4,3))
    ax.bar(years, [n/1e9 for n in net_incomes], color='#2E8B57')
    ax.set_title('Net Income (USD Billions)')
    ax.set_ylabel('USD Billions')
    ax.set_xlabel('Year')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf

def generate_price_trend_chart(dates, closes):
    if len(dates) < 2:
        return None
    fig, ax = plt.subplots(figsize=(6,3))
    ax.plot(dates, closes, marker='o', linewidth=1)
    ax.set_title('Price Trend (3Y)')
    ax.set_xlabel('Date')
    ax.set_ylabel('Close Price')
    fig.autofmt_xdate(rotation=45)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf

# --- 5. Aggregation & Summarization ---
def aggregate_company_content(company_name, website, internal_texts, pdf_texts, news, leadership):
    # Concatenate all text sources
    content = f"Company: {company_name}\nWebsite: {website}\n\n"
    content += "=== Internal Pages ===\n"
    for page in internal_texts:
        content += f"\n--- {page['url']} ---\n{page['text']}\n"
    content += "\n=== PDF Documents ===\n"
    for pdf in pdf_texts:
        content += f"\n{pdf}\n"
    content += "\n=== Latest News ===\n"
    for n in news:
        content += f"\n- {n['title']} ({n['source']}): {n['description']} [{n['url']}]\n"
    content += "\n=== Leadership ===\n"
    for leader in leadership:
        content += f"- {leader['name']}: {leader['role']}\n"
    return content[:12000]  # Truncate for LLM

# --- 6. Main Orchestrator ---
def run_advanced_crawler(company_name):
    website = resolve_company_website_duckduckgo(company_name)
    if not website:
        return None, f"Could not resolve website for {company_name}."
    internal_texts, pdf_texts = crawl_internal_pages(website)
    news = fetch_gnews(company_name)
    if not news:
        news = fetch_google_news(company_name)
    leadership = fetch_leadership_info(company_name, website)
    wikipedia_summary = fetch_wikipedia_summary(company_name)
    yahoo_summary = fetch_yahoo_finance_summary(company_name)
    yahoo_trends, chart_data = fetch_yahoo_finance_trends(company_name, website, internal_texts, pdf_texts)
    content = f"Company: {company_name}\nWebsite: {website}\n\n"
    if wikipedia_summary:
        content += "=== Wikipedia Overview ===\n" + wikipedia_summary + "\n\n"
    if yahoo_summary:
        content += "=== Yahoo Finance Overview ===\n" + yahoo_summary + "\n\n"
    if yahoo_trends:
        content += "=== Yahoo Finance Latest Trends ===\n" + '\n'.join(f"- {t}" for t in yahoo_trends) + "\n\n"
    content += "=== Internal Pages ===\n"
    for page in internal_texts:
        content += f"\n--- {page['url']} ---\n{page['text']}\n"
    content += "\n=== PDF Documents ===\n"
    for pdf in pdf_texts:
        content += f"\n{pdf}\n"
    content += "\n=== Latest News ===\n"
    for n in news:
        content += f"\n- {n['title']} ({n['source']}): {n['description']} [{n['url']}]\n"
    content += "\n=== Leadership ===\n"
    for leader in leadership:
        content += f"- {leader['name']}: {leader['role']}\n"
    return content[:12000], None

def generate_org_chart_png(leadership, company_name):
    from graphviz import Digraph
    import os
    if not leadership:
        return None
    dot = Digraph(comment=f'{company_name} Leadership', format='png')
    dot.attr(rankdir='TB')
    ceo = None
    for leader in leadership:
        if 'ceo' in leader['role'].lower() or 'chairman' in leader['role'].lower() or 'founder' in leader['role'].lower():
            ceo = f"{leader['name']}\n{leader['role']}"
            break
    if ceo:
        for leader in leadership:
            label = f"{leader['name']}\n{leader['role']}"
            if label != ceo:
                dot.edge(ceo, label)
    else:
        for leader in leadership:
            label = f"{leader['name']}\n{leader['role']}"
            dot.node(label, label)
    os.makedirs('downloads', exist_ok=True)
    out_path = f"downloads/{company_name}_org_chart.png"
    try:
        dot.render(out_path, cleanup=True)
        if not os.path.exists(out_path):
            print(f"Graphviz failed to create {out_path}")
            return None
        return out_path
    except Exception as e:
        print(f"Graphviz error: {e}")
        return None 