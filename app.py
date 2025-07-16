from flask import Flask, request, redirect, session, jsonify, send_from_directory, render_template, url_for
import os
import re
import requests
import threading
from urllib.parse import urlparse
from dotenv import load_dotenv
from my_crawler import fetch_text_from_url, extract_ir_links
from pdf_parser import extract_text_from_pdf
from summarizer import init_gemini, summarize_chunks, extract_financials, generate_swot_analysis, compare_companies_summary, extract_business_segments, answer_question, detect_trends, detect_red_flags_and_opportunities, extract_timeline_events, analyze_company
from pdf_exporter import export_summary_to_pdf
from ppt_exporter import export_summary_to_ppt, add_title_slide, add_financials_slide, add_swot_slide, add_comparison_slide, add_financials_bar_chart_slide, add_business_segments_pie_chart_slide, add_trends_slide, add_red_flags_opportunities_slide, add_timeline_slide
from tldextract import extract
import json
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches
import logging
from bs4 import BeautifulSoup
from advanced_crawler import run_advanced_crawler, resolve_company_website_duckduckgo
import io
import pandas as pd
import matplotlib.pyplot as plt
import pdfplumber
from googlesearch import search
import datetime
try:
    import markdown2
    render_markdown = True
except ImportError:
    render_markdown = False

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecret")

# === OAuth Setup ===
CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")
CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/oauth/callback")
AUTH_URL = "https://login.salesforce.com/services/oauth2/authorize"
TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"

# === Slack ===
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
NGROK_DOMAIN = os.getenv("NGROK_DOMAIN", "http://localhost:5000")

# === Gemini ===
init_gemini(os.getenv("GEMINI_API_KEY"))

# In-memory conversational state (per channel/thread)
conversation_state = {}

# Track processed Slack event IDs to prevent double replies
processed_event_ids = set()

SLACK_BOT_USER_ID = os.getenv("SLACK_BOT_USER_ID")  # Set this in your .env

def get_full_company_context(channel_id, thread_ts, company_name=None):
    key = f"{channel_id}:{thread_ts or channel_id}"
    # 1. Use stored full_context if available
    if key in conversation_state and "data1" in conversation_state[key]:
        ctx = conversation_state[key]["data1"].get("full_context") or conversation_state[key]["data1"].get("summary")
        if ctx and len(ctx.strip()) > 100:  # ensure it's not empty or trivial
            return ctx
    # 2. Fallback: run advanced crawler (broad web search)
    if company_name:
        content, err = run_advanced_crawler(company_name)
        if content and len(content.strip()) > 100:
            return content
    # 3. If all else fails, return None (handled in button actions)
    return None

# Helper to reset state
def reset_state(key):
    if key in conversation_state:
        del conversation_state[key]

# Helper to clean up old conversation state (keep only last 10 conversations)
def cleanup_conversation_state():
    if len(conversation_state) > 10:
        # Remove oldest entries
        keys_to_remove = list(conversation_state.keys())[:-10]
        for key in keys_to_remove:
            del conversation_state[key]
        logging.info(f"[cleanup_conversation_state] Removed {len(keys_to_remove)} old conversation states")


@app.route("/login")
def login():
    return redirect(f"{AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}")


@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "No code received", 400

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(TOKEN_URL, data=data)
    if response.status_code != 200:
        return f"Failed to get token: {response.text}", 400

    token_data = response.json()
    session["access_token"] = token_data["access_token"]
    session["instance_url"] = token_data["instance_url"]

    return '''
        <h3>✅ Connected to Salesforce!</h3>
        <a href="/profile">➡️ View Salesforce Profile</a>
    '''


@app.route("/profile")
def profile():
    access_token = session.get("access_token")
    instance_url = session.get("instance_url")
    if not access_token:
        return redirect("/login")

    headers = {"Authorization": f"Bearer {access_token}"}
    user_info = requests.get(f"{instance_url}/services/oauth2/userinfo", headers=headers)
    return jsonify(user_info.json())


@app.route("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory("downloads", filename)


# Remove the dashboard route and all dashboard.html references
# (Delete the entire dashboard() function and any url_for('dashboard') or render_template('dashboard.html', ...) calls)


def is_url(text):
    return text.startswith("http://") or text.startswith("https://")


@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json(force=True)
    # Slack URL verification
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})
    # Handle event callbacks
    if data.get("type") == "event_callback":
        event_id = data.get("event_id")
        if event_id in processed_event_ids:
            return "", 200
        processed_event_ids.add(event_id)
        event = data["event"]
        # Ignore messages from the bot itself
        if event.get("user") == SLACK_BOT_USER_ID or event.get("bot_id"):
            return "", 200
        print("Received Slack event:", event)
        # Only handle new user messages (not bot messages, not thread replies)
        if event.get("type") == "message":
            text = event.get("text", "")
            if event.get("subtype") == "file_share":
                handle_file_share_message_event(event)
            elif not event.get("subtype") and not event.get("thread_ts"):
                # Ignore bot prompt messages
                if text.strip().startswith("Please type your question as a new message"):
                    return "", 200
                user_id = event.get("user")
                channel_id = event.get("channel")
                key = f"{channel_id}:{channel_id}"
                if conversation_state.get(key, {}).get("qa_enabled"):
                    with app.test_request_context(
                        "/slack/ask",
                        method="POST",
                        data={
                            "text": text,
                            "channel_id": channel_id,
                            "user_id": user_id,
                            "thread_ts": event.get("ts"),
                        },
                    ):
                        slack_ask()
        # Handle file_shared events (for logging/debug only)
        if event.get("type") == "file_shared":
            handle_file_shared_event(event)
    return "", 200


@app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    payload = request.form.get("payload")
    if payload:
        data = json.loads(payload)
        action = data.get("actions", [])[0]
        url = action.get("value")
        channel_id = data["channel"]["id"]
        thread_ts = data.get("message", {}).get("ts")
        action_id = action["action_id"]
        ext = extract(url)
        company_name = ext.domain.capitalize()

        if action_id == "regenerate_summary":
            send_slack(channel_id, "🔁 Regenerating summary...", thread_ts=thread_ts)
            threading.Thread(target=process_summary_task, args=(url, channel_id, thread_ts)).start()
        elif action_id == "competitor_comparison":
            send_slack(channel_id, text=f"Please reply with the competitor's company URL.", thread_ts=thread_ts)
        elif action_id == "financial_trends":
            send_slack(channel_id, text=f"⏳ Generating financial trends for {company_name}...")
            from advanced_crawler import fetch_yahoo_finance_trends, generate_revenue_chart, generate_netincome_chart, generate_price_trend_chart
            website = resolve_company_website_duckduckgo(company_name)
            from advanced_crawler import crawl_internal_pages
            internal_texts, pdf_texts = crawl_internal_pages(website) if website else ([], [])
            yahoo_trends, chart_data = fetch_yahoo_finance_trends(company_name, website, internal_texts, pdf_texts)
            debug_source = ""
            if yahoo_trends:
                trends = yahoo_trends
                debug_source = "[DEBUG] Used yfinance or fallback extraction."
            else:
                context = get_full_company_context(channel_id, thread_ts, company_name)
                if context:
                    trends = detect_trends(context)
                    chart_data = []
                    debug_source = "[DEBUG] Used Gemini LLM for trends."
                else:
                    # Fallback: Ask Gemini for generic financial trends for the company
                    trends = detect_trends(f"{company_name} is a company. Please provide general financial trends, even if only based on industry knowledge.")
                    chart_data = []
                    debug_source = "[DEBUG] Used Gemini LLM for generic fallback."
            trends = [t.replace("**", "").replace("*", "") for t in trends]
            logging.info(f"[Financial Trends] {debug_source} Trends: {trends}")
            if not trends and not chart_data:
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*No reliable financial data was found for {company_name} from public sources or documents. Only qualitative trends are shown. You may upload a financial statement (PDF, Excel, or CSV) for better results.*"
                        }
                    },
                ]
                send_slack(channel_id, blocks=blocks)
                return "", 200
            if debug_source == "[DEBUG] Used Gemini LLM for trends." and company_name.lower() in ["infosys", "microsoft", "tcs", "apple", "amazon", "google", "alphabet", "wipro", "hdfc", "reliance"]:
                send_slack(channel_id, text=f"⚠️ Could not fetch financials for {company_name} from Yahoo Finance. Please try again later or upload a financial statement.")
            blocks = build_trends_blocks(company_name, trends, url)
            send_slack(channel_id, blocks=blocks)
            SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
            for chart in chart_data:
                if hasattr(chart, 'read'):
                    chart.seek(0)
                    files = {'file': (f"{company_name}_chart.png", chart, 'image/png')}
                    payload = {
                        "channels": channel_id,
                        "title": f"{company_name} Financial Chart"
                    }
                    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                    requests.post("https://slack.com/api/files.upload", params=payload, files=files, headers=headers)
        elif action_id == "risks_opps":
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f":hourglass_flowing_sand: Generating risks & opportunities for {company_name}..."}
                }
            ]
            send_slack(channel_id, blocks=blocks)
            context = get_full_company_context(channel_id, thread_ts, company_name)
            if context:
                fallback_context, _ = run_advanced_crawler(company_name)
                risks = detect_red_flags_and_opportunities(context, company_name=company_name, fallback_context=fallback_context)
                blocks = build_risks_blocks(company_name, risks, url)
                send_slack(channel_id, blocks=blocks)
            else:
                # Fallback: Ask Gemini for generic risks & opportunities
                risks = detect_red_flags_and_opportunities(f"{company_name} is a company. Please provide general risks and opportunities, even if only based on industry knowledge.", company_name=company_name)
                blocks = build_risks_blocks(company_name, risks, url)
                send_slack(channel_id, blocks=blocks)
                return "", 200
        elif action_id == "timeline_events":
            send_slack(channel_id, text=f"⏳ Generating timeline/key events for {company_name}...")
            context = get_full_company_context(channel_id, thread_ts, company_name)
            if context:
                fallback_context, _ = run_advanced_crawler(company_name)
                timeline = extract_timeline_events(context, company_name=company_name, fallback_context=fallback_context)
                blocks = build_timeline_blocks(company_name, timeline, url)
                send_slack(channel_id, blocks=blocks)
            else:
                # Fallback: Ask Gemini for generic timeline/key events
                timeline = extract_timeline_events(f"{company_name} is a company. Please provide a general timeline or key events, even if only based on industry knowledge.", company_name=company_name)
                blocks = build_timeline_blocks(company_name, timeline, url)
                send_slack(channel_id, blocks=blocks)
                return "", 200
        elif action_id == "leadership":
            send_slack(channel_id, text=f"⏳ Fetching leadership info for {company_name}...")
            leadership_info = get_key_executives(company_name)
            send_slack(channel_id, text=leadership_info)
            # Org chart generation and upload skipped for now
        elif action_id == "ask_custom_question":
            key = f"{channel_id}:{channel_id}"
            if key in conversation_state:
                conversation_state[key]["qa_enabled"] = True
            else:
                conversation_state[key] = {"qa_enabled": True}
            send_slack(channel_id, text=f"💬 Please type your question as a new message in this channel (not as a thread reply) about {company_name}. I'll use all available company information to answer.")
        elif action_id == "swot_analysis":
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f":hourglass_flowing_sand: Generating SWOT analysis for {company_name}..."}
                }
            ]
            send_slack(channel_id, blocks=blocks)
            context = get_full_company_context(channel_id, thread_ts, company_name)
            if context:
                swot = generate_swot_analysis(context, company_name)
                blocks = build_swot_blocks(company_name, swot, url)
                send_slack(channel_id, blocks=blocks)
            else:
                # Fallback: Ask Gemini for generic SWOT
                swot = generate_swot_analysis(f"{company_name} is a company. Please provide a general SWOT analysis, even if only based on industry knowledge.", company_name)
                blocks = build_swot_blocks(company_name, swot, url)
                send_slack(channel_id, blocks=blocks)
                return "", 200
        elif action_id == "ask_another_question":
            # Reset Q&A for the channel, not the thread
            key = f"{channel_id}:{channel_id}"
            if key in conversation_state:
                conversation_state[key]["qa_enabled"] = True
            else:
                conversation_state[key] = {"qa_enabled": True}
            send_slack(channel_id, text=f"💬 Please type your next question as a new message in this channel (not as a thread reply) about {company_name}. I'll use all available company information to answer.")
            return "", 200
    return "", 200


@app.route("/slack/ask", methods=["POST"])
def slack_ask():
    # Only allow internal calls (not direct Slack events)
    if request.headers.get("X-Slack-Signature"):
        return "", 200
    question = request.form.get("text")
    channel_id = request.form.get("channel_id")
    thread_ts = request.form.get("thread_ts") or request.form.get("trigger_id")
    user_id = request.form.get("user_id")
    key = f"{channel_id}:{channel_id}"

    if not question:
        send_slack(channel_id, text="❌ Please provide a question.")
        return jsonify(response_type="ephemeral", text="No question provided.")

    if key not in conversation_state or not conversation_state[key].get("qa_enabled"):
        send_slack(channel_id, text="❌ Please click the 'Custom Question' button before asking a question.")
        return jsonify(response_type="ephemeral", text="No company context found or Q&A not enabled.")

    try:
        company_data = conversation_state[key]["data1"]
        company_name = company_data["company_name"]
        full_context = company_data.get("full_context", company_data["summary"])
        logging.info(f"[slack_ask] Answering custom question: '{question}' for company: {company_name}")
        mention = f"<@{user_id}> " if user_id else ""
        send_slack(channel_id, text=f"{mention}🔎 Answering your question: *{question}* ...")
        fallback_context, _ = run_advanced_crawler(company_name)
        if fallback_context:
            full_context = f"{full_context}\n\nAdditional Context:\n{fallback_context}"
        full_context = full_context[:8000]
        answer = answer_question(full_context, question)
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{mention}*Q: {question}*\n\n{answer}"
                }
            },
        ]
        # Always post as a new message in the channel (never in a thread)
        send_slack(channel_id, blocks=blocks)
        conversation_state[key]["qa_enabled"] = False
        return jsonify(response_type="in_channel", text="Q&A answered.")
    except Exception as e:
        logging.error(f"[slack_ask] Error: {e}", exc_info=True)
        send_slack(channel_id, text=f"❌ Error answering question: {str(e)}")
        return jsonify(response_type="ephemeral", text="Error processing question.")


@app.route("/slack/command", methods=["POST"])
def slack_command():
    user_input = request.form.get("text")
    channel_id = request.form.get("channel_id")
    thread_ts = request.form.get("thread_ts")
    key = f"{channel_id}:{thread_ts}" if thread_ts else f"{channel_id}:{channel_id}"

    if not user_input:
        return jsonify(response_type="ephemeral", text="❌ Please provide a company name or URL.")

    ack_text = "⏳ Processing your request. You will receive your report soon!"
    threading.Thread(target=process_summary_task, args=(user_input, channel_id, thread_ts)).start()
    return jsonify(response_type="in_channel", text=ack_text)


@app.route("/slack/file_upload", methods=["POST"])
def slack_file_upload():
    print("File upload endpoint hit!")
    event = request.json.get("event", {})
    print("Event data:", event)
    file_info = event.get("files", [{}])[0]
    file_url = file_info.get("url_private")
    filetype = file_info.get("filetype")
    filename = file_info.get("name")
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts")

    # Immediately acknowledge receipt
    send_slack(channel_id, "⏳ Analyzing your file for financials, please wait...", thread_ts=thread_ts)

    try:
        # Download file from Slack
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        r = requests.get(file_url, headers=headers)
        os.makedirs("downloads", exist_ok=True)
        local_path = f"downloads/{filename}"
        with open(local_path, "wb") as f:
            f.write(r.content)

        # Extract text/data
        if filetype in ["pdf"]:
            import pdfplumber
            with pdfplumber.open(local_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            df = None
        elif filetype in ["xlsx", "xls"]:
            import pandas as pd
            df = pd.read_excel(local_path)
            text = df.to_string()
        elif filetype == "csv":
            import pandas as pd
            df = pd.read_csv(local_path)
            text = df.to_string()
        else:
            send_slack(channel_id, "❌ Unsupported file type. Please upload PDF, Excel, or CSV.", thread_ts=thread_ts)
            return "", 200

        # Extract financials
        from summarizer import extract_financials
        financials = extract_financials(text)
        summary = "\n".join([f"{k}: {v}" for k, v in financials.items()])

        # Visualization: try multiple chart types if possible
        import matplotlib.pyplot as plt
        chart_paths = []
        # Bar chart for all numeric metrics
        metrics = []
        values = []
        for k, v in financials.items():
            try:
                num = float(v.replace(",", "").replace("₹", "").replace("$", "").replace("%", ""))
                metrics.append(k)
                values.append(num)
            except Exception:
                continue
        if metrics and values:
            # Bar chart
            plt.figure(figsize=(6, 4))
            plt.bar(metrics, values, color="#4682B4")
            plt.title("Key Financials (Bar Chart)")
            plt.tight_layout()
            bar_path = f"downloads/{filename}_bar.png"
            plt.savefig(bar_path)
            plt.close()
            chart_paths.append(bar_path)
            # Line chart (if more than 2 metrics)
            if len(metrics) > 2:
                plt.figure(figsize=(6, 4))
                plt.plot(metrics, values, marker='o', color="#2E8B57")
                plt.title("Key Financials (Line Chart)")
                plt.tight_layout()
                line_path = f"downloads/{filename}_line.png"
                plt.savefig(line_path)
                plt.close()
                chart_paths.append(line_path)
            # Pie chart (if 3-8 metrics, e.g. expense breakdown)
            if 3 <= len(metrics) <= 8:
                plt.figure(figsize=(6, 4))
                plt.pie(values, labels=metrics, autopct='%1.1f%%', startangle=140)
                plt.title("Key Financials (Pie Chart)")
                plt.tight_layout()
                pie_path = f"downloads/{filename}_pie.png"
                plt.savefig(pie_path)
                plt.close()
                chart_paths.append(pie_path)

        # Respond in Slack
        if summary.strip():
            send_slack(channel_id, f"✅ Financials extracted:\n{summary}", thread_ts=thread_ts)
            for chart_path in chart_paths:
                with open(chart_path, "rb") as img:
                    files = {'file': (chart_path, img, 'image/png')}
                    payload = {
                        "channels": channel_id,
                        "thread_ts": thread_ts,
                        "title": "Financials Chart"
                    }
                    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                    requests.post("https://slack.com/api/files.upload", params=payload, files=files, headers=headers)
        else:
            send_slack(channel_id, "❌ Could not extract financials from this file. Please check the format or try another file.", thread_ts=thread_ts)

    except Exception as e:
        print(f"Exception in file upload handler: {e}")
        send_slack(channel_id, f"❌ Error during processing: {str(e)}", thread_ts=thread_ts)

    return "", 200


def process_summary_task(user_input, channel_id, thread_ts=None):
    try:
        logging.info(f"[process_summary_task] Start for {user_input}")
        send_slack(channel_id, "⏳ Working...\n• Crawling site\n• Parsing PDFs\n• Generating report..", thread_ts=thread_ts)

        if is_url(user_input):
            logging.info("[process_summary_task] Detected URL input")
            main_text = fetch_text_from_url(user_input)
            logging.info("[process_summary_task] Fetched main text")
            pdf_links = extract_ir_links(user_input)
            logging.info(f"[process_summary_task] Found {len(pdf_links)} PDF links")
            pdf_texts = ""
            for link in pdf_links:
                if link.endswith(".pdf"):
                    try:
                        filename = link.split("/")[-1]
                        r = requests.get(link, timeout=10)
                        with open(filename, 'wb') as f:
                            f.write(r.content)
                        pdf_texts += extract_text_from_pdf(filename)
                        logging.info(f"[process_summary_task] Downloaded and parsed PDF: {filename}")
                    except Exception as e:
                        logging.error(f"[process_summary_task] Error downloading/parsing PDF {link}: {e}")
                        continue
            full_context = (main_text + "\n\n" + pdf_texts)[:12000]
            logging.info(f"[process_summary_task] Context length: {len(full_context)}")
            summary = re.sub(r"\*+", "", summarize_chunks(full_context)).strip()
            logging.info("[process_summary_task] Summary generated")
            ext = extract(user_input)
            company_name = ext.domain.capitalize()
        else:
            logging.info("[process_summary_task] Detected company name input")
            content, err = run_advanced_crawler(user_input)
            if err:
                send_slack(channel_id, f"❌ {err}\nPlease provide the company's website URL for more accurate results.", thread_ts=thread_ts)
                logging.error(f"[process_summary_task] Error from run_advanced_crawler: {err}")
                return
            logging.info(f"[process_summary_task] Aggregated content length: {len(content)}")
            summary = re.sub(r"\*+", "", summarize_chunks(content)).strip()
            logging.info("[process_summary_task] Summary generated (company name flow)")
            company_name = user_input.capitalize()
            user_input = resolve_company_website_duckduckgo(user_input) or user_input

        # Store conversation state for Q&A
        key = f"{channel_id}:{thread_ts}" if thread_ts else f"{channel_id}:{channel_id}"
        conversation_state[key] = {
            "data1": {
                "company_name": company_name,
                "summary": summary,
                "full_context": full_context if 'full_context' in locals() else content,
                "original_url": user_input
            }
        }
        logging.info(f"[process_summary_task] Stored context for key: {key}")

        # Clean up old conversation states
        cleanup_conversation_state()

        os.makedirs("downloads", exist_ok=True)
        pdf_path = f"downloads/{company_name}_Summary.pdf"
        ppt_path = f"downloads/{company_name}_Summary.pptx"
        export_summary_to_pdf(summary, pdf_path)
        logging.info(f"[process_summary_task] PDF exported: {pdf_path}")
        export_summary_to_ppt(summary, ppt_path, company_name)
        logging.info(f"[process_summary_task] PPT exported: {ppt_path}")

        pdf_url = f"{NGROK_DOMAIN}/downloads/{company_name}_Summary.pdf"
        ppt_url = f"{NGROK_DOMAIN}/downloads/{company_name}_Summary.pptx"

        blocks = build_summary_blocks(company_name, summary, pdf_url, ppt_url, user_input)
        send_slack(channel_id, blocks=blocks, thread_ts=thread_ts)
        logging.info(f"[process_summary_task] Sent summary for {company_name} to Slack.")

        # Send follow-up options
        followup_blocks = build_followup_options_blocks(company_name, user_input)
        send_slack(channel_id, blocks=followup_blocks, thread_ts=thread_ts)
        logging.info(f"[process_summary_task] Sent follow-up options for {company_name} to Slack.")

    except Exception as e:
        logging.error(f"[process_summary_task] Error: {e}", exc_info=True)
        send_slack(channel_id, f"❌ Error processing request: {str(e)}", thread_ts=thread_ts)


def build_summary_blocks(company_name, summary_text, pdf_url, ppt_url, original_url):
    chunk = summary_text[:2900] if summary_text else "No summary generated."
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📄 {company_name}: Strategic Summary"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"```{chunk}```"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📥 Download PDF"},
                    "url": pdf_url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📊 Download PPT"},
                    "url": ppt_url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔁 Regenerate"},
                    "action_id": "regenerate_summary",
                    "value": original_url
                }
            ]
        }
    ]


def send_slack(channel_id, text=None, blocks=None, thread_ts=None):
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel_id,
        "text": text or "Here's your summary!",
    }
    if thread_ts and re.match(r"^\d+\.\d+$", str(thread_ts)):
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks

    response = requests.post("https://slack.com/api/chat.postMessage", json=payload, headers=headers)
    logging.info(f"[send_slack] Slack API response: {response.status_code} {response.text}")


def process_company(url):
    logging.info(f"[process_company] Start processing: {url}")
    main_text = fetch_text_from_url(url)
    logging.info("[process_company] Fetched main text")
    pdf_links = extract_ir_links(url)
    logging.info(f"[process_company] Found {len(pdf_links)} PDF links")
    pdf_texts = ""
    for link in pdf_links:
        if link.endswith(".pdf"):
            try:
                filename = link.split("/")[-1]
                r = requests.get(link, timeout=10)
                with open(filename, 'wb') as f:
                    f.write(r.content)
                pdf_texts += extract_text_from_pdf(filename)
                logging.info(f"[process_company] Downloaded and parsed PDF: {filename}")
            except Exception as e:
                logging.error(f"[process_company] Error downloading/parsing PDF {link}: {e}")
                continue
    full_context = (main_text + "\n\n" + pdf_texts)[:12000]
    max_chars = 8000
    full_context = full_context[:max_chars]
    ext = extract(url)
    company_name = ext.domain.capitalize()
    logging.info(f"[process_company] Calling analyze_company for: {company_name}")
    try:
        analysis = analyze_company(full_context)
    except Exception as e:
        logging.error(f"[process_company] Error in analyze_company: {e}", exc_info=True)
        raise
    financials = extract_financials(full_context)
    segments = extract_business_segments(full_context)
    logging.info(f"[process_company] Finished processing: {company_name}")
    return {
        "company_name": company_name,
        "summary": analysis["summary"],
        "swot": analysis["swot"],
        "trends": analysis["trends"],
        "red_flags_opps": analysis["red_flags_opps"],
        "timeline_events": analysis["timeline_events"],
        "financials": financials,
        "segments": segments
    }


def build_followup_options_blocks(company_name, url):
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Would you like to know more about *{company_name}*? Here are some things I can help with:"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "SWOT Analysis"},
                    "action_id": "swot_analysis",
                    "value": url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Financial Trends"},
                    "action_id": "financial_trends",
                    "value": url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Risks & Opportunities"},
                    "action_id": "risks_opps",
                    "value": url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Timeline/Key Events"},
                    "action_id": "timeline_events",
                    "value": url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Leadership"},
                    "action_id": "leadership",
                    "value": url
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Ask a custom question"},
                    "action_id": "ask_custom_question",
                    "value": url
                }
            ]
        }
    ]


def get_key_executives(company_name):
    query = f"{company_name} leadership team"
    results = list(search(query, num_results=12, lang='en'))
    leadership_urls = [url for url in results if any(x in url.lower() for x in ['leadership', 'management', 'team', 'executive'])]
    snippets = []
    # If we have a leadership page, fetch its text for better extraction
    if leadership_urls:
        snippets.append(get_leadership_text(leadership_urls[0]))
    # Add up to 4 more URLs as fallback
    for url in leadership_urls[1:5]:
        snippets.append(url)
    prompt = f"""
From the following web search results, extract ONLY a bullet list of at least 8 key executives (name and title) for {company_name}.
- Do NOT include a company summary, description, or any other information.
- Output ONLY the list, in the format: Name: Title
- If you find fewer than 8, list as many as you can, but do not add any summary or explanation.

Web search results:
{chr(10).join(snippets)}
"""
    from summarizer import summarize_chunks
    response = summarize_chunks(prompt)
    import re
    # Only keep lines that look like 'Name: Title'
    filtered = []
    for line in response.split('\n'):
        if re.match(r"^[A-Za-z .'-]+: .+", line.strip()):
            filtered.append(line.strip())
    if not filtered:
        return "Could not extract leadership info. Please try again."
    return '\n'.join(filtered)

def get_leadership_text(url):
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        texts = soup.stripped_strings
        return '\n'.join(texts)
    except Exception:
        return ''


def get_company_logo_url(company_name):
    """
    Attempts to crawl the company's website and extract the logo URL.
    Returns an absolute or proxied logo URL, or None if not found.
    """
    from advanced_crawler import resolve_company_website_duckduckgo
    try:
        website = resolve_company_website_duckduckgo(company_name)
        if not website:
            return None
        resp = requests.get(website, timeout=10, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/58.0.3029.110 Safari/537.3"
            )
        })
        soup = BeautifulSoup(resp.text, "html.parser")
        # Look for <img> tags with 'logo' in class, id, or src
        for img in soup.find_all("img"):
            attrs = (img.get("class", []) + [img.get("id", "")] + [img.get("src", "")])
            if any("logo" in str(a).lower() for a in attrs):
                src = img.get("src")
                if src:
                    # Make absolute URL if needed
                    from urllib.parse import urljoin
                    return urljoin(website, src)
        return None
    except Exception as e:
        print(f"[WARN] Could not fetch logo for {company_name}: {e}")
        return None


def build_swot_blocks(company_name, swot, url):
    swot_text = f"SWOT Analysis for {company_name}:\n\n"
    for k in ["Strengths", "Weaknesses", "Opportunities", "Threats"]:
        swot_text += f"{k}:\n"
        if swot[k]:
            for item in swot[k]:
                swot_text += f"- {item}\n"
        else:
            swot_text += "N/A\n"
        swot_text += "\n"
    swot_text = swot_text.strip()
    if len(swot_text) > 2900:
        swot_text = swot_text[:2900]
    swot_text = swot_text.replace('**', '')  # Remove all bold markers
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": swot_text
            }
        }
    ]
    blocks += build_followup_options_blocks(company_name, url)
    return blocks


def build_trends_blocks(company_name, trends, url):
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Financial Trends for {company_name}:*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ("\n".join([f"- {t}" for t in trends]) if trends else "N/A")
            }
        }
    ]
    blocks += build_followup_options_blocks(company_name, url)
    return blocks


def build_risks_blocks(company_name, risks, url):
    risks_text = f"Risks & Opportunities for {company_name}:\n\n"
    risks_text += "Red Flags:\n"
    if risks.get('Red Flags'):
        for item in risks['Red Flags']:
            risks_text += f"- {item}\n"
    else:
        risks_text += "N/A\n"
    risks_text += "\nOpportunities:\n"
    if risks.get('Opportunities'):
        for item in risks['Opportunities']:
            risks_text += f"- {item}\n"
    else:
        risks_text += "N/A\n"
    risks_text = risks_text.strip()
    if len(risks_text) > 2900:
        risks_text = risks_text[:2900]
    risks_text = risks_text.replace('**', '')  # Remove all bold markers
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": risks_text
            }
        }
    ]
    blocks += build_followup_options_blocks(company_name, url)
    return blocks


def build_timeline_blocks(company_name, timeline, url):
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Timeline/Key Events for {company_name}:*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ("\n".join([f"{year}: {event}" for year, event in timeline]) if timeline else "N/A")
            }
        }
    ]
    blocks += build_followup_options_blocks(company_name, url)
    return blocks


# --- Test endpoint for yfinance ---
@app.route("/slack/test_yfinance", methods=["POST"])
def slack_test_yfinance():
    ticker = request.form.get("ticker")
    channel_id = request.form.get("channel_id")
    if not ticker or not channel_id:
        return "Missing ticker or channel_id", 400
    import yfinance as yf
    t = yf.Ticker(ticker)
    info = t.info
    msg = f"*yfinance info for {ticker}:*\n" + "\n".join([f"{k}: {v}" for k, v in info.items() if k in ['marketCap','totalRevenue','netIncomeToCommon','trailingPE','forwardPE','priceToSalesTrailing12Months','priceToBook','enterpriseToEbitda','trailingEps','revenueGrowth','recommendationKey']])
    send_slack(channel_id, text=msg)
    return "", 200


def download_and_parse_financial_docs(links, download_dir='downloads'):
    os.makedirs(download_dir, exist_ok=True)
    summaries = []
    for link in links:
        try:
            filename = link.split('/')[-1].split('?')[0]
            local_path = os.path.join(download_dir, filename)
            r = requests.get(link, timeout=15)
            with open(local_path, 'wb') as f:
                f.write(r.content)
            # Parse based on file type
            if filename.lower().endswith('.pdf'):
                with pdfplumber.open(local_path) as pdf:
                    text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
            elif filename.lower().endswith(('.xls', '.xlsx')):
                df = pd.read_excel(local_path)
                text = df.to_string()
            else:
                with open(local_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            from summarizer import extract_financials
            financials = extract_financials(text)
            summaries.append({'file': filename, 'financials': financials, 'link': link})
        except Exception as e:
            print(f'Error processing {link}: {e}')
    return summaries


def fetch_and_summarize_investor_docs(company_name):
    website = resolve_company_website_duckduckgo(company_name)
    if not website:
        return []
    ir_links = extract_ir_links(website)
    doc_summaries = download_and_parse_financial_docs(ir_links)
    return doc_summaries


def handle_file_shared_event(event):
    print("File shared event received:", event)
    # You can expand this logic to download and process the file as needed
    # For now, just log the event for debugging


def handle_file_share_message_event(event):
    print("File share message event received:", event)
    files = event.get("files", [])
    for file_info in files:
        file_url = file_info.get("url_private")
        filetype = file_info.get("filetype")
        filename = file_info.get("name")
        channel_id = event.get("channel")
        thread_ts = event.get("ts")
        send_slack(channel_id, "⏳ Analyzing your file for financials, please wait...")
        try:
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
            r = requests.get(file_url, headers=headers)
            os.makedirs("downloads", exist_ok=True)
            local_path = f"downloads/{filename}"
            with open(local_path, "wb") as f:
                f.write(r.content)

            text = ""
            extracted_metrics = {}
            if filetype in ["pdf"]:
                import pdfplumber
                with pdfplumber.open(local_path) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            elif filetype in ["xlsx", "xls"]:
                import pandas as pd
                excel = pd.ExcelFile(local_path)
                for sheet_name in excel.sheet_names:
                    df = excel.parse(sheet_name)
                    text += f"\n--- Sheet: {sheet_name} ---\n"
                    text += df.to_string()
                    for col in df.columns:
                        for idx, val in df[col].items():
                            label = str(idx).lower()
                            if isinstance(val, (int, float, str)):
                                sval = str(val)
                                if ("revenue" in label or "total income" in label) and not extracted_metrics.get("Revenue"):
                                    extracted_metrics["Revenue"] = sval
                                if ("net profit" in label or "net income" in label or "pat" in label) and not extracted_metrics.get("Net Profit"):
                                    extracted_metrics["Net Profit"] = sval
                                if "growth" in label and not extracted_metrics.get("Growth"):
                                    extracted_metrics["Growth"] = sval
                                if "margin" in label and not extracted_metrics.get("Operating Margin"):
                                    extracted_metrics["Operating Margin"] = sval
            elif filetype == "csv":
                import pandas as pd
                df = pd.read_csv(local_path)
                text += df.to_string()
                for col in df.columns:
                    for idx, val in df[col].items():
                        label = str(idx).lower()
                        if isinstance(val, (int, float, str)):
                            sval = str(val)
                            if ("revenue" in label or "total income" in label) and not extracted_metrics.get("Revenue"):
                                extracted_metrics["Revenue"] = sval
                            if ("net profit" in label or "net income" in label or "pat" in label) and not extracted_metrics.get("Net Profit"):
                                extracted_metrics["Net Profit"] = sval
                            if "growth" in label and not extracted_metrics.get("Growth"):
                                extracted_metrics["Growth"] = sval
                            if "margin" in label and not extracted_metrics.get("Operating Margin"):
                                extracted_metrics["Operating Margin"] = sval
            else:
                send_slack(channel_id, "❌ Unsupported file type. Please upload PDF, Excel, or CSV.")
                return

            from summarizer import extract_financials
            text_metrics = extract_financials(text)
            metrics = {**text_metrics, **{k: v for k, v in extracted_metrics.items() if v and v != 'nan'}}
            summary = "\n".join([f"{k}: {v}" for k, v in metrics.items()])

            import matplotlib.pyplot as plt
            chart_paths = []
            metrics_for_chart = []
            values = []
            for k, v in metrics.items():
                try:
                    num = float(str(v).replace(",", "").replace("₹", "").replace("$", "").replace("%", ""))
                    metrics_for_chart.append(k)
                    values.append(num)
                except Exception:
                    continue
            if metrics_for_chart and values:
                # Always generate a bar chart if at least one value
                plt.figure(figsize=(6, 4))
                plt.bar(metrics_for_chart, values, color="#4682B4")
                plt.title("Key Financials (Bar Chart)")
                plt.tight_layout()
                bar_path = f"downloads/{filename}_bar.png"
                plt.savefig(bar_path)
                plt.close()
                chart_paths.append(bar_path)
                # Only generate line/pie charts if enough metrics
                if len(metrics_for_chart) > 2:
                    plt.figure(figsize=(6, 4))
                    plt.plot(metrics_for_chart, values, marker='o', color="#2E8B57")
                    plt.title("Key Financials (Line Chart)")
                    plt.tight_layout()
                    line_path = f"downloads/{filename}_line.png"
                    plt.savefig(line_path)
                    plt.close()
                    chart_paths.append(line_path)
                if 3 <= len(metrics_for_chart) <= 8:
                    plt.figure(figsize=(6, 4))
                    plt.pie(values, labels=metrics_for_chart, autopct='%1.1f%%', startangle=140)
                    plt.title("Key Financials (Pie Chart)")
                    plt.tight_layout()
                    pie_path = f"downloads/{filename}_pie.png"
                    plt.savefig(pie_path)
                    plt.close()
                    chart_paths.append(pie_path)

            if summary.strip():
                send_slack(channel_id, f"✅ Financials extracted:\n{summary}")
                for chart_path in chart_paths:
                    with open(chart_path, "rb") as img:
                        files = {'file': (chart_path, img, 'image/png')}
                        payload = {
                            "channels": channel_id,
                            "title": "Financials Chart"
                        }
                        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                        requests.post("https://slack.com/api/files.upload", params=payload, files=files, headers=headers)
            else:
                send_slack(channel_id, "❌ Could not extract financials from this file. Please check the format or try another file.")

        except Exception as e:
            print(f"Exception in file share message event handler: {e}")
            send_slack(channel_id, f"❌ Error during processing: {str(e)}")


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0")
