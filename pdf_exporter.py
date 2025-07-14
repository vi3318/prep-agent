from reportlab.lib.pagesizes import LETTER
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib import colors
import re

def clean_html_for_reportlab(text):
    """
    Clean HTML text to be compatible with ReportLab's Paragraph parser.
    Removes unsupported attributes and tags that cause parsing errors.
    Also removes all <img> tags (no images in PDF).
    """
    if not text:
        return text
    # Remove all <img> tags entirely
    text = re.sub(r'<img[^>]*>', '', text)
    # Remove align, style, class attributes from any tags (defensive)
    text = re.sub(r' (align|style|class)="[^"]*"', '', text)
    # Remove unsupported HTML tags
    unsupported_tags = ['<div>', '</div>', '<span>', '</span>', '<p>', '</p>']
    for tag in unsupported_tags:
        text = text.replace(tag, '')
    return text.strip()

def format_url(url):
    """
    Format a URL as underlined blue text for the PDF.
    """
    # ReportLab supports <a href="...">text</a> for clickable links
    return f'<font color="#1155cc"><u><a href="{url}">{url}</a></u></font>'

def export_summary_to_pdf(summary, filename, company_name="Company"):
    """
    Exports a structured summary to a professional-looking PDF.
    - Uses a more robust parsing method to correctly style all headings.
    - Intelligently formats list items with proper bullet points.
    - Includes HTML cleaning to prevent ReportLab parsing errors.
    - Removes all images and improves formatting for clarity and professionalism.
    """
    doc = SimpleDocTemplate(filename, pagesize=LETTER,
                            rightMargin=50, leftMargin=50,
                            topMargin=50, bottomMargin=50)

    # --- Style Configuration ---
    styles = getSampleStyleSheet()
    
    styles['BodyText'].fontSize = 11
    styles['BodyText'].leading = 16
    styles['BodyText'].spaceAfter = 8
    styles['BodyText'].fontName = 'Helvetica'

    styles.add(ParagraphStyle(name='CustomTitle', 
                              fontSize=22, 
                              leading=28, 
                              fontName='Helvetica-Bold',
                              spaceAfter=20))
                              
    styles.add(ParagraphStyle(name='SectionHeader', 
                              fontSize=15, 
                              leading=20, 
                              fontName='Helvetica-Bold',
                              textColor=colors.HexColor('#003366'), 
                              spaceAfter=14, 
                              spaceBefore=18))
    
    styles.add(ParagraphStyle(name='BulletStyle',
                              parent=styles['BodyText'],
                              leftIndent=24,
                              bulletIndent=12,
                              spaceAfter=6))

    styles.add(ParagraphStyle(name='URLStyle',
                              parent=styles['BodyText'],
                              textColor=colors.HexColor('#1155cc'),
                              underline=True,
                              spaceAfter=10))

    story = []

    # --- Document Title ---
    story.append(Paragraph(f"{company_name} – Executive Briefing", styles['CustomTitle']))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 24))

    # --- Section Parsing (NEW, MORE ROBUST LOGIC) ---
    heading_pattern = r'\*\*(.+?)\*\*'
    matches = list(re.finditer(heading_pattern, summary))
    
    if not matches:
        for line in summary.split('\n'):
            clean_line = clean_html_for_reportlab(line)
            if clean_line:
                # Format URLs in the line
                clean_line = re.sub(r'(https?://\S+)', lambda m: format_url(m.group(1)), clean_line)
                try:
                    story.append(Paragraph(clean_line, styles['BodyText']))
                except Exception as e:
                    print(f"Error parsing line: {clean_line[:100]}...\nError: {e}")
                    story.append(Paragraph(clean_line.replace('<', '&lt;').replace('>', '&gt;'), styles['BodyText']))
        doc.build(story)
        return

    for i, match in enumerate(matches):
        heading_text = match.group(1).strip()
        start_of_body = match.end()
        end_of_body = matches[i+1].start() if i + 1 < len(matches) else len(summary)
        body_text = summary[start_of_body:end_of_body].strip().lstrip(':').strip()

        story.append(Spacer(1, 10))
        story.append(Paragraph(heading_text, styles['SectionHeader']))
        story.append(Spacer(1, 6))

        is_bullet_section = "Offerings" in heading_text or "Initiatives" in heading_text or "News summary" in heading_text
        body_lines = body_text.split('\n')
        for line in body_lines:
            clean_line = line.strip("-–•●* ").strip()
            if clean_line:
                clean_line = clean_html_for_reportlab(clean_line)
                # Format URLs in the line
                clean_line = re.sub(r'(https?://\S+)', lambda m: format_url(m.group(1)), clean_line)
                if is_bullet_section and (clean_line.startswith('-') or clean_line.startswith('•') or clean_line.startswith('*')):
                    try:
                        story.append(Paragraph(clean_line, styles['BulletStyle'], bulletText='•'))
                    except Exception as e:
                        print(f"Error parsing bullet line: {clean_line[:100]}...\nError: {e}")
                        fallback_text = clean_line.replace('<', '&lt;').replace('>', '&gt;')
                        story.append(Paragraph(fallback_text, styles['BulletStyle'], bulletText='•'))
                else:
                    try:
                        story.append(Paragraph(clean_line, styles['BodyText']))
                    except Exception as e:
                        print(f"Error parsing line: {clean_line[:100]}...\nError: {e}")
                        fallback_text = clean_line.replace('<', '&lt;').replace('>', '&gt;')
                        story.append(Paragraph(fallback_text, styles['BodyText']))
            story.append(Spacer(1, 2))
        story.append(Spacer(1, 16))

    doc.build(story)
