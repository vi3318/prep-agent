import requests
from bs4 import BeautifulSoup
import re

def fetch_text_from_url(url):
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(response.text, 'html.parser')

    # Remove unwanted tags
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    text = ' '.join(p.get_text().strip() for p in soup.find_all(['p', 'li', 'h1', 'h2', 'h3']))
    return re.sub(r'\s+', ' ', text)

def extract_ir_links(base_url):
    """Extract PDF, Excel, and investor relations links from the given website."""
    try:
        response = requests.get(base_url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if any(ext in href.lower() for ext in ['pdf', 'xls', 'xlsx', 'investor', 'ir', 'presentation', 'results', 'earnings']):
                full_url = href if href.startswith('http') else base_url.rstrip('/') + '/' + href.lstrip('/')
                links.append(full_url)
        return list(set(links))
    except Exception as e:
        print(f"Failed to extract links: {e}")
        return []
