import google.generativeai as genai
from textblob import TextBlob
import re
import os

def init_gemini(api_key=None):
    if api_key is None:
        api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key)

def summarize_chunks(content):
    model = genai.GenerativeModel('gemini-2.5-flash')

    max_chars = 12000
    if len(content) > max_chars:
        content = content[:max_chars]
    prompt = f"""
You are a senior investment analyst preparing a concise executive briefing document. Your audience is a busy executive who needs to understand a company's core identity and strategy at a glance.

Your task is to analyze the provided business content, which may include information from the company's website, Wikipedia, Yahoo Finance, news articles, and other reliable sources. **Integrate facts from all sources, not just the website.**

**Instructions:**
- Use all available information, including company history, industry, market cap, financials, leadership, and recent news if present.
- Integrate and cross-reference facts from Wikipedia, Yahoo Finance, and news with the website content.
- Include key financials (revenue, market cap, etc.), industry, and company history if available.
- Highlight unique strengths, strategic direction, and market positioning with data and specifics.
- Use numbers and named facts where possible (e.g., "2023 revenue: $X billion", "Founded in 19XX", "Market cap: $X billion").
- Keep the output concise, professional, and under 400 words, but maximize factual richness.
- Use Markdown for formatting.
- Use bold headings for each section.
- Write in a professional, objective, and data-driven tone.
- Do not use conversational language or first-person phrases (e.g., "I think," "As an analyst...").
- Do not add any introductory or concluding paragraphs that are not part of the requested structure.

**Briefing Structure:**

**1. Executive Summary:**
A single, dense paragraph of **no more than 3 sentences** that captures the company's core identity, primary business, and market position, integrating facts from all sources.

**2. Key Offerings & Business Segments:**
A bulleted list identifying the company's main products, services, or business divisions.

**3. Strategic Direction & Initiatives:**
A bulleted list of the company's stated strategic goals, key initiatives, or areas of focus mentioned in the text (e.g., AI integration, market expansion, sustainability efforts).

**4. Market Positioning & Target Audience:**
A brief description (1-2 sentences) of the company's apparent position in the market (e.g., "a low-cost leader," "a premium B2B provider") and the primary customers it serves.

--- START OF BUSINESS CONTENT ---

{content}

--- END OF BUSINESS CONTENT ---
"""
    response = model.generate_content(prompt)
    return response.text.strip()

# For comparing two companies
def compare_companies_summary(content1, content2):
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
Compare the strategic focus, recent developments, and market positioning of two companies based on the following data:

---
Company A:
{content1}

---
Company B:
{content2}
"""
    response = model.generate_content(prompt)
    return response.text.strip()

# Bullet-point meeting notes
def generate_meeting_notes(content):
    model = genai.GenerativeModel('ggemini-2.5-flash')
    prompt = f"""
From the following content, generate bullet-point meeting preparation notes, listing strategic initiatives and key developments:

{content}
"""
    response = model.generate_content(prompt)
    return response.text.strip()

# Sentiment analysis
def analyze_sentiment(text):
    blob = TextBlob(text)
    return blob.sentiment  # Returns (polarity, subjectivity)

def extract_financials(text):
    """
    Extracts key financials (revenue, profit, growth, etc.) from text using regex and Gemini if available.
    Returns a dict with keys: Revenue, Net Profit, Growth, Operating Margin, etc.
    """
    # Simple regex for numbers (USD, INR, EUR, etc.)
    patterns = {
        'Revenue': r'(?:Revenue|Total Revenue|Turnover)[^\d$€₹]*([\$€₹]?[\d,.]+\s*(?:million|billion|crore|lakh|mn|bn)?)',
        'Net Profit': r'(?:Net Profit|Net Income|Profit after Tax)[^\d$€₹]*([\$€₹]?[\d,.]+\s*(?:million|billion|crore|lakh|mn|bn)?)',
        'Growth': r'(?:YoY Growth|Growth|Increase)[^\d-]*([\d.]+%)',
        'Operating Margin': r'(?:Operating Margin)[^\d-]*([\d.]+%)',
    }
    results = {}
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            results[key] = m.group(1)
    # If not enough data, use Gemini to summarize
    if len(results) < 2:
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
Extract the following financial metrics from the text below (if present): Revenue, Net Profit, Growth, Operating Margin. If not found, say 'N/A'.
Text:\n{text}\n
Format:
Revenue: ...\nNet Profit: ...\nGrowth: ...\nOperating Margin: ...
"""
        response = model.generate_content(prompt)
        for line in response.text.strip().split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                results[k.strip()] = v.strip()
    return results

def generate_swot_analysis(text, company_name="The company"):
    model = genai.GenerativeModel('gemini-2.5-flash')
    # If context is empty or not useful, use a minimal prompt for debugging
    if not text or len(text.strip()) < 100:
        prompt = f"""
Generate a realistic SWOT analysis for {company_name}, a major IT services company in India. Use your knowledge of the company and industry to infer likely points.

Format as:
Strengths:
- ...
Weaknesses:
- ...
Opportunities:
- ...
Threats:
- ...

Keep the total output under 2500 characters.
"""
    else:
        prompt = f"""
You are an expert business analyst. Based on the following information about {company_name} (including implicit clues), generate a detailed and realistic SWOT analysis (Strengths, Weaknesses, Opportunities, Threats). If information is not explicit, use your knowledge of {company_name}, its industry, and similar companies to infer likely SWOT points. Do not leave any section blank.

Format as:
Strengths:
- ...
Weaknesses:
- ...
Opportunities:
- ...
Threats:
- ...

Keep the total output under 2500 characters.

Text:
{text}
"""
    print(f"SWOT prompt for {company_name}:\n{prompt}")
    response = model.generate_content(prompt)
    print('Gemini SWOT raw response:', response.text)  # For debugging
    swot = {'Strengths': [], 'Weaknesses': [], 'Opportunities': [], 'Threats': []}
    current = None
    for line in response.text.strip().split('\n'):
        l = line.strip().replace('*', '').replace(':', '').strip()
        if l in swot:
            current = l
        elif current and (line.strip().startswith('-') or line.strip().startswith('*')):
            swot[current].append(line.strip()[1:].strip())
    return swot

def extract_business_segments(text):
    """
    Extracts business segment breakdowns from text. Returns a dict {segment: value}.
    Looks for lines like 'Cloud: 40%' or bullet lists with numbers.
    If not enough data, uses Gemini to summarize.
    """
    segments = {}
    # Regex for lines like 'Segment: 40%' or 'Segment - 40%'
    pattern = r'([A-Za-z &]+)[\-:]+\s*([\d.]+)%'
    for match in re.finditer(pattern, text):
        name = match.group(1).strip()
        value = float(match.group(2))
        segments[name] = value
    # If not enough segments, try Gemini
    if len(segments) < 2:
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
Extract the business segment breakdown (segment name and percentage) from the text below. Format as:
Segment: %
...
Text:\n{text}
"""
        response = model.generate_content(prompt)
        for line in response.text.strip().split('\n'):
            if ':' in line and '%' in line:
                k, v = line.split(':', 1)
                try:
                    segments[k.strip()] = float(v.strip().replace('%',''))
                except Exception:
                    continue
    return segments

def answer_question(context, question):
    """
    Uses Gemini to answer a user question using the provided company context/summary.
    Returns a string answer.
    """
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
You are an expert business analyst with deep knowledge of companies, industries, and business strategy. Use the following company information to answer the user's question comprehensively and professionally.

**Instructions:**
- Provide detailed, factual answers based on the available information
- If specific information is not available, use your own knowledge as of 2024 to answer as best as possible
- For financial questions, include numbers and metrics when available
- For strategic questions, explain the company's approach and reasoning
- For operational questions, describe processes and capabilities
- Keep answers informative but concise (2-4 sentences for simple questions, up to 6 sentences for complex ones)
- Use professional, objective language
- If the question is unclear, ask for clarification or provide a general answer

**Company Information:**
{context}

If the answer is not in the above context, use your own knowledge as of 2024 to answer as best as possible.

**Question:** {question}

**Answer:**"""
    response = model.generate_content(prompt)
    return response.text.strip()

def detect_trends(text):
    """
    Uses Gemini to extract and summarize recent financial trends or changes in the company's strategy or financials.
    Returns a list of trend statements.
    """
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
Analyze the following company information and list the most important recent financial trends or changes from the last 1-3 years. 
- Include revenue, profit, growth rates, margins, and any notable financial events (e.g., acquisitions, major investments, restructuring).
- Use numbers and specifics where possible (e.g., 'Revenue grew 10% in 2023 to $X billion').
- Integrate data from all available sources (website, Yahoo Finance, news, etc.).
- Format as a clean bullet list (no code block, no markdown), one trend per line.
- Keep the output under 2000 characters.

Company Info:
{text}
"""
    response = model.generate_content(prompt)
    trends = []
    for line in response.text.strip().split('\n'):
        if line.strip().startswith('-'):
            trends.append(line.strip()[1:].strip())
        elif line.strip() and not line.strip().startswith('-'):
            trends.append(line.strip())
    return trends

def detect_red_flags_and_opportunities(text, company_name=None, industry=None, fallback_context=None):
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
Analyze the following company information and list:
- Red Flags: Any risks, negative trends, controversies, or issues that could be a concern for a client or investor.
- Opportunities: Any growth areas, positive trends, new initiatives, or strengths that could be leveraged.

If the information is insufficient, INFER likely risks and opportunities based on the company's industry, similar companies, and recent news. Always provide at least 3 red flags and 3 opportunities, even if you must generalize.

Company: {company_name or 'N/A'}
Industry: {industry or 'N/A'}

Company Info:
{text}

{f"Additional Context:\n{fallback_context}" if fallback_context else ""}
"""
    response = model.generate_content(prompt)
    result = {'Red Flags': [], 'Opportunities': []}
    current = None
    for line in response.text.strip().split('\n'):
        if line.strip().startswith('Red Flags'):
            current = 'Red Flags'
        elif line.strip().startswith('Opportunities'):
            current = 'Opportunities'
        elif current and line.strip().startswith('-'):
            result[current].append(line.strip()[1:].strip())
    # Fallback: If still empty, add generic industry risks/opps
    if len(result['Red Flags']) < 3:
        result['Red Flags'] += [
            "Regulatory changes impacting the industry",
            "Increased competition from global players",
            "Potential data security or privacy breaches"
        ][:3-len(result['Red Flags'])]
    if len(result['Opportunities']) < 3:
        result['Opportunities'] += [
            "Expansion into emerging markets",
            "Adoption of new technologies",
            "Strategic partnerships or acquisitions"
        ][:3-len(result['Opportunities'])]
    return result

def extract_timeline_events(text, company_name=None, fallback_context=None):
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
Extract a timeline of the most important company events (e.g., product launches, acquisitions, leadership changes, major partnerships, strategic initiatives) from the text below. For each event, include the year and a short description.

If company-specific events are not found, INFER likely milestones based on the company's industry, similar companies, and recent news. Always provide at least 3 events, even if you must generalize.

Company: {company_name or 'N/A'}

Text:
{text}

{f"Additional Context:\n{fallback_context}" if fallback_context else ""}
"""
    response = model.generate_content(prompt)
    events = []
    for line in response.text.strip().split('\n'):
        if ':' in line:
            try:
                year, desc = line.split(':', 1)
                year = year.strip()
                desc = desc.strip()
                if year.isdigit() and len(year) == 4:
                    events.append((int(year), desc))
            except Exception:
                continue
    # Fallback: If still empty, add generic events
    if len(events) < 3:
        import datetime
        year = datetime.datetime.now().year
        fallback_events = [
            (year-2, "Adopted digital transformation initiatives"),
            (year-1, "Expanded into new markets"),
            (year, "Launched new product line or service")
        ]
        events += fallback_events[:3-len(events)]
    events.sort()
    return events

def analyze_company(text):
    """
    Batches all Gemini calls (summary, SWOT, trends, red flags, timeline) into a single call.
    Returns a dict with keys: summary, swot, trends, red_flags_opps, timeline_events.
    """
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
You are a senior business analyst. Analyze the following company information and provide:

1. **Summary:** A concise executive summary (3-4 sentences).
2. **SWOT Analysis:** List Strengths, Weaknesses, Opportunities, Threats as bullet points.
3. **Recent Trends:** List the most important recent trends or changes (last 1-3 years) as bullet points.
4. **Red Flags & Opportunities:**
   - Red Flags: Any risks, negative trends, controversies, or issues (bullets).
   - Opportunities: Any growth areas, positive trends, new initiatives, or strengths (bullets).
5. **Timeline:** List the most important company events (year: event description).

Format your response as:

Summary:
...

SWOT:
Strengths:
- ...
Weaknesses:
- ...
Opportunities:
- ...
Threats:
- ...

Trends:
- ...

Red Flags:
- ...
Opportunities:
- ...

Timeline:
2023: ...
2022: ...
...

Company Info:
{text}
"""
    response = model.generate_content(prompt)
    result = {"summary": "", "swot": {"Strengths": [], "Weaknesses": [], "Opportunities": [], "Threats": []}, "trends": [], "red_flags_opps": {"Red Flags": [], "Opportunities": []}, "timeline_events": []}
    lines = response.text.strip().split('\n')
    current = None
    swot_section = None
    for line in lines:
        l = line.strip()
        if l.lower().startswith('summary:'):
            current = 'summary'
            result['summary'] = ""
        elif l.lower().startswith('swot:'):
            current = 'swot'
        elif l in ['Strengths:', 'Weaknesses:', 'Opportunities:', 'Threats:']:
            swot_section = l.replace(':', '')
            current = 'swot_section'
        elif l.lower().startswith('trends:'):
            current = 'trends'
        elif l.lower().startswith('red flags:'):
            current = 'red_flags'
        elif l.lower().startswith('opportunities:') and current != 'swot_section':
            current = 'opportunities'
        elif l.lower().startswith('timeline:'):
            current = 'timeline'
        elif current == 'summary' and l:
            result['summary'] += l + ' '
        elif current == 'swot_section' and swot_section and l.startswith('-'):
            result['swot'][swot_section].append(l[1:].strip())
        elif current == 'trends' and l.startswith('-'):
            result['trends'].append(l[1:].strip())
        elif current == 'red_flags' and l.startswith('-'):
            result['red_flags_opps']['Red Flags'].append(l[1:].strip())
        elif current == 'opportunities' and l.startswith('-'):
            result['red_flags_opps']['Opportunities'].append(l[1:].strip())
        elif current == 'timeline' and ':' in l:
            try:
                year, desc = l.split(':', 1)
                year = year.strip()
                desc = desc.strip()
                if year.isdigit() and len(year) == 4:
                    result['timeline_events'].append((int(year), desc))
            except Exception:
                continue
    result['timeline_events'].sort()
    result['summary'] = result['summary'].strip()
    return result
