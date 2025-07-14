import json
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import time
from advanced_crawler import run_advanced_crawler
from summarizer import summarize_chunks
from pdf_exporter import export_summary_to_pdf
from ppt_exporter import export_summary_to_ppt
import os
import requests
from PyPDF2 import PdfMerger
from dotenv import load_dotenv
import feedparser
from bs4 import BeautifulSoup
import re
from slack_sdk import WebClient
from app import fetch_and_summarize_investor_docs

load_dotenv()

# Ensure Gemini is initialized with GEMINI_API_KEY
from summarizer import init_gemini
init_gemini()

COMPANIES_FILE = os.path.join(os.path.dirname(__file__), 'companies.json')
DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), '../downloads')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL_ID = os.getenv('SLACK_CHANNEL_ID')
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY')

# Expanded list of Indian and global news RSS feeds for better coverage
INDIAN_NEWS_SITES = [
    {"name": "Times of India", "rss": "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"},
    {"name": "The Hindu", "rss": "https://www.thehindu.com/news/national/feeder/default.rss"},
    {"name": "Mint", "rss": "https://www.livemint.com/rss/news"},
    {"name": "Business Standard", "rss": "https://www.business-standard.com/rss/latest.rss"},
    {"name": "Hindu BusinessLine", "rss": "https://www.thehindubusinessline.com/news/national/feeder/default.rss"},
    {"name": "Economic Times", "rss": "https://economictimes.indiatimes.com/rssfeedstopstories.cms"},
    {"name": "Financial Express", "rss": "https://www.financialexpress.com/feed/"},
    {"name": "NDTV Business", "rss": "https://feeds.feedburner.com/ndtvprofit-latest"},
    {"name": "Reuters India", "rss": "https://www.reuters.com/rssFeed/businessNews"},
    {"name": "Moneycontrol", "rss": "https://www.moneycontrol.com/rss/latestnews.xml"},
    {"name": "CNBC TV18", "rss": "https://www.cnbctv18.com/rss/business-news.xml"},
    {"name": "Zee Business", "rss": "https://zeenews.india.com/rss/business-news.xml"},
    {"name": "Bloomberg Quint", "rss": "https://www.bqprime.com/feed"}
]

# You can add more global RSS feeds here if desired

logging.basicConfig(level=logging.INFO)

def load_companies():
    with open(COMPANIES_FILE) as f:
        return json.load(f)

def upload_to_slack(filepath, title=None):
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logging.warning("[Scheduler] Slack token or channel ID not set. Skipping upload.")
        return
    client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        response = client.files_upload_v2(
            channel=SLACK_CHANNEL_ID,
            file=filepath,
            initial_comment=title or os.path.basename(filepath)
        )
        if response["ok"]:
            logging.info(f"[Scheduler] Uploaded to Slack: {filepath}")
        else:
            logging.error(f"[Scheduler] Slack upload failed: {response}")
    except Exception as e:
        logging.error(f"[Scheduler] Slack upload exception: {e}")

def merge_pdfs(pdf_paths, output_path):
    merger = PdfMerger()
    for pdf in pdf_paths:
        merger.append(pdf)
    merger.write(output_path)
    merger.close()

def fetch_company_news(company_name):
    """
    Fetch latest news articles for the company from global and Indian/global news sources.
    Returns a list of dicts: [{"title": ..., "summary": ..., "url": ..., "source": ...}, ...]
    """
    news_items = []
    # 1. NewsAPI (global + Indian)
    try:
        url = f'https://newsapi.org/v2/everything?q={company_name}&language=en&sortBy=publishedAt&pageSize=20&apiKey={NEWSAPI_KEY}'
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            all_articles = data.get('articles', [])
            logging.info(f"[NewsAPI] {company_name}: {len(all_articles)} articles fetched.")
            for article in all_articles:
                title = article['title']
                desc = article.get('description') or ''
                content = article.get('content') or ''
                # Log all fetched titles
                logging.info(f"[NewsAPI] {company_name} Article: {title}")
                # Loosened filter: company name anywhere in title, description, or content
                if (company_name.lower() in title.lower() or
                    company_name.lower() in desc.lower() or
                    company_name.lower() in content.lower()):
                    news_items.append({
                        "title": title,
                        "summary": desc,
                        "url": article['url'],
                        "source": article['source']['name']
                    })
    except Exception as e:
        logging.error(f"[NewsAPI] Error for {company_name}: {e}")
    # 2. RSS feeds (Indian/global)
    for site in INDIAN_NEWS_SITES:
        try:
            feed = feedparser.parse(site['rss'])
            logging.info(f"[RSS] {company_name}: {len(feed.entries)} entries from {site['name']}")
            for entry in feed.entries[:20]:
                title = entry.title
                summary = entry.get('summary', '')
                # Log all fetched titles
                logging.info(f"[RSS] {company_name} Article: {title}")
                # Loosened filter: company name anywhere in title or summary
                if (company_name.lower() in title.lower() or
                    company_name.lower() in summary.lower()):
                    news_items.append({
                        "title": title,
                        "summary": summary,
                        "url": entry.link,
                        "source": site['name']
                    })
        except Exception as e:
            logging.error(f"[RSS] Error for {site['name']} and {company_name}: {e}")
    # Deduplicate by title
    seen_titles = set()
    deduped = []
    for item in news_items:
        if item['title'] not in seen_titles:
            deduped.append(item)
            seen_titles.add(item['title'])
    if not deduped:
        logging.warning(f"[News] No news found for {company_name} after filtering!")
    return deduped

from summarizer import summarize_chunks

def group_news_by_section(news_items, company_name):
    """
    Use Gemini to group news items under dynamic, news-driven subheadings for the week.
    Returns a dict: {subheading: [news_item, ...], ...}
    """
    if not news_items:
        return {}
    # Limit to top 20 news items to avoid token issues
    limited_news = news_items[:20]
    news_text = "\n".join([
        f"Title: {item['title']}\nSource: {item['source']}\nSummary: {item['summary']}\nURL: {item['url']}"
        for item in limited_news
    ])
    prompt = f"""
You are an expert business analyst. Given the following news items about {company_name}, identify the most relevant themes or topics for this week (e.g., "Financial Results", "Leadership Changes", "Product Launches", "Regulatory News", "Market Expansion", etc.).

For each theme, group the news items that fit best under that heading. If a news item fits more than one theme, pick the most relevant.

Format as:

Subheading: <theme>
- Headline: ...
  Summary: ...
  Source: ...
  URL: ...

News Items:
{news_text}
"""
    try:
        summary = summarize_chunks(prompt)
        logging.info(f"[Gemini] Raw output for {company_name}:\n{summary}")
        grouped = {}
        current = None
        for line in summary.split('\n'):
            l = line.strip()
            if l.startswith('Subheading:'):
                subheading = l.replace('Subheading:', '').strip()
                current = subheading
                grouped[current] = []
            elif current and l.startswith('- Headline:'):
                item = {'title': l.replace('- Headline:', '').strip()}
                grouped[current].append(item)
            elif current and l.startswith('Summary:') and grouped[current]:
                grouped[current][-1]['summary'] = l.replace('Summary:', '').strip()
            elif current and l.startswith('Source:') and grouped[current]:
                grouped[current][-1]['source'] = l.replace('Source:', '').strip()
            elif current and l.startswith('URL:') and grouped[current]:
                grouped[current][-1]['url'] = l.replace('URL:', '').strip()
        # Remove empty subheadings
        grouped = {k: v for k, v in grouped.items() if v}
        return grouped
    except Exception as e:
        logging.error(f"[GroupNews] Error for {company_name}: {e}")
        return {}

def summarize_news(news_items, company_name):
    """
    Summarize a list of news items for a company using Gemini for a concise summary, and group news by dynamic subheadings.
    Returns a string summary.
    """
    if not news_items:
        return f"No news found for {company_name} this week."
    grouped = group_news_by_section(news_items, company_name)
    if not grouped:
        # Fallback: simple listing if Gemini returns nothing
        summary = f"News summary for {company_name}:\n"
        for item in news_items:
            summary += f"- {item.get('title','')} ({item.get('source','')})\n  {item.get('summary','')}\n  {item.get('url','')}\n"
        return summary
    summary = f"News summary for {company_name}:\n"
    for section, items in grouped.items():
        summary += f"\n**{section}:**\n"
        for item in items:
            summary += f"- {item.get('title','')} ({item.get('source','')})\n  {item.get('summary','')}\n  {item.get('url','')}\n"
    return summary

def weekly_job():
    companies = load_companies()
    newsletter_summaries = []
    for company in companies:
        name = company['name']
        url = company.get('url')
        logging.info(f"[Scheduler] Processing {name} ({url})")
        try:
            news_items = fetch_company_news(name)
            news_summary = summarize_news(news_items, name)
            # Fetch IR document summaries
            ir_docs = fetch_and_summarize_investor_docs(name)
            ir_section = ""
            if ir_docs:
                for doc in ir_docs:
                    if doc['financials']:
                        ir_section += f"\n[IR] {doc['file']} ({doc['link']}):\n" + '\n'.join([f"{k}: {v}" for k, v in doc['financials'].items()]) + "\n"
            full_summary = news_summary + ("\n" + ir_section if ir_section else "")
            newsletter_summaries.append({"company": name, "summary": full_summary})
        except Exception as e:
            logging.error(f"[Scheduler] Exception for {name}: {e}")
            newsletter_summaries.append({"company": name, "summary": f"No news found for {name} this week."})
    # Compile all summaries into a single newsletter
    if newsletter_summaries:
        newsletter_text = "\n\n".join([
            f"==============================\n{item['company'].upper()}\n==============================\n{item['summary']}"
            for item in newsletter_summaries
        ])
        newsletter_path = os.path.join(DOWNLOADS_DIR, f"Weekly_Newsletter_{datetime.now().strftime('%Y-%m-%d')}.pdf")
        export_summary_to_pdf(newsletter_text, newsletter_path)
        upload_to_slack(newsletter_path, title="Weekly Business News Newsletter (PDF)")
        logging.info(f"[Scheduler] Uploaded newsletter: {newsletter_path}")

if __name__ == "__main__":
    # Run the weekly job immediately for testing
    weekly_job()
    scheduler = BackgroundScheduler()
    # Schedule to run every Monday at 8am
    scheduler.add_job(weekly_job, 'cron', day_of_week='mon', hour=8, minute=0)
    scheduler.start()
    logging.info("[Scheduler] Started. Waiting for jobs...")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown() 