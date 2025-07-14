import os
import requests
from flask import Flask, request, jsonify
from crawler import fetch_text_from_url, extract_ir_links
from pdf_parser import extract_text_from_pdf
from summarizer import init_gemini, summarize_chunks
from pdf_exporter import export_summary_to_pdf
from ppt_exporter import export_summary_to_ppt
from urllib.parse import urlparse
import re

app = Flask(__name__)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

init_gemini(os.getenv("GEMINI_API_KEY"))

def send_message_to_slack(channel_id, message):
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel_id,
        "text": message
    }
    requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=payload)

@app.route("/slack/events", methods=["POST"])
def handle_slack_event():
    data = request.form
    text = data.get("text", "")
    channel_id = data.get("channel_id")

    if not text.startswith("http"):
        return jsonify(response_type="ephemeral", text="❌ Please provide a valid URL.")

    send_message_to_slack(channel_id, f"🔍 Scraping and summarizing content from: {text}")

    try:
        main_text = fetch_text_from_url(text)
        ir_links = extract_ir_links(text)

        pdf_texts = ""
        for link in ir_links:
            if link.endswith(".pdf"):
                try:
                    filename = link.split("/")[-1]
                    r = requests.get(link, timeout=10)
                    filepath = os.path.join("downloads", filename)
                    with open(filepath, 'wb') as f:
                        f.write(r.content)
                    pdf_texts += extract_text_from_pdf(filepath)
                except:
                    continue

        full_context = (main_text + "\n" + pdf_texts)[:12000]
        raw_summary = summarize_chunks(full_context)
        summary = re.sub(r"\*+", "", raw_summary).strip()

        parsed = urlparse(text)
        domain = parsed.hostname.split(".")[1].capitalize()
        company_name = domain

        # Save files
        pdf_path = f"downloads/{company_name}_Summary.pdf"
        ppt_path = f"downloads/{company_name}_Summary.pptx"

        export_summary_to_pdf(summary, pdf_path)
        export_summary_to_ppt(summary, ppt_path, company_name)

        slack_message = f"""
*📌 Summary for {company_name}:*\n\n{summary[:1800]}...

📥 *Files available:*
• <{pdf_path}|Download PDF>
• <{ppt_path}|Download PPT>
"""
        send_message_to_slack(channel_id, slack_message)

    except Exception as e:
        send_message_to_slack(channel_id, f"❌ Error during processing: {str(e)}")

    return jsonify(response_type="in_channel", text="✅ Processing complete.")
