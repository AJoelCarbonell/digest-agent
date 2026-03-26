#!/usr/bin/env python3
"""
Evening Newsletter Digest Agent
Reads unread emails from Gmail (newsletters + forwarded URLs),
summarizes each using Groq, and sends a digest to the recipient.
"""

import imaplib
import smtplib
import email
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import html2text
import os
from groq import Groq

# ── Config ────────────────────────────────────────────────────────────────────
GMAIL_USER       = "ajc198412@gmail.com"
GMAIL_APP_PW     = os.environ["GMAIL_APP_PW"]
DIGEST_RECIPIENT = "alexander@hvcapital.com"
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
GROQ_MODEL       = "llama-3.3-70b-versatile"
# ─────────────────────────────────────────────────────────────────────────────


def connect_imap():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PW)
    return mail


def decode_str(value):
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            result.append(part)
    return "".join(result)


def extract_email_content(msg):
    subject = decode_str(msg.get("Subject", "(no subject)"))
    sender  = decode_str(msg.get("From", "Unknown"))

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not body:
                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
            elif ct == "text/html":
                raw_html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                h = html2text.HTML2Text()
                h.ignore_links = False
                h.body_width = 0
                body = h.handle(raw_html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="ignore")

    return subject, sender, body.strip()


def extract_url(body):
    stripped = body.strip()
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    if len(lines) == 1 and re.match(r'^https?://\S+$', lines[0]):
        return lines[0]
    if re.match(r'^https?://\S+$', stripped):
        return stripped
    return None


def fetch_webpage_text(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DigestBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:6000]


def summarize(client, name, content, url=""):
    prompt = f"""You are a concise analyst summarizing content for a busy professional.

Source: {name}
{f"URL: {url}" if url else ""}

Content:
{content[:5000]}

Write a summary following these rules exactly:

**Overview**
2-3 sentences capturing the core message.

**Key Insights**
- If the content has clear subheadings or sections: group your bullets under each subheading, with 2-3 bullets per subheading. Use the subheading as a bold label, e.g. **Section Title** followed by its bullets.
- If the content has no clear subheadings: provide the 5 most relevant insights as a flat list of bullets.
- Each bullet should be substantive — one clear, specific point, not a vague summary.

{"**Link**" + chr(10) + url if url else ""}

No extra commentary. No filler. Only output the structured summary.
"""
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()


def build_digest_html(items):
    date_str = datetime.now().strftime("%A, %d %B %Y")

    sections = ""
    for item in items:
        icon = "🌐" if item["type"] == "webpage" else "📰"
        meta = item.get("url") or item.get("sender", "")
        summary_html = item["summary"].replace("\n", "<br>")
        sections += f"""
        <div style="margin-bottom:28px; padding:18px 20px; border-left:4px solid #2563eb;
                    background:#f8fafc; border-radius:0 6px 6px 0;">
          <h2 style="margin:0 0 4px 0; font-size:17px; color:#1e293b;">{icon}&nbsp;{item['source']}</h2>
          <p style="margin:0 0 12px 0; font-size:12px; color:#64748b;">{meta}</p>
          <div style="font-size:14px; line-height:1.7; color:#334155;">{summary_html}</div>
        </div>"""

    if not items:
        sections = "<p style='color:#64748b;'>No newsletters or links received today.</p>"

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
             max-width:680px; margin:0 auto; padding:24px; color:#1e293b; background:#fff;">

  <div style="border-bottom:2px solid #2563eb; padding-bottom:12px; margin-bottom:24px;">
    <h1 style="margin:0; font-size:22px; color:#1e293b;">📬 Evening Digest</h1>
    <p style="margin:4px 0 0 0; font-size:13px; color:#64748b;">{date_str} &middot; {len(items)} item{'s' if len(items) != 1 else ''}</p>
  </div>

  {sections}

  <p style="margin-top:36px; font-size:11px; color:#94a3b8; border-top:1px solid #e2e8f0; padding-top:12px;">
    Sent by your Digest Agent &middot;
    Forward newsletters or paste a URL to <a href="mailto:ajc198412@gmail.com" style="color:#2563eb;">ajc198412@gmail.com</a>
  </p>

</body>
</html>"""


def send_digest(html_body, item_count):
    date_str = datetime.now().strftime("%d %b")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📬 Evening Digest — {date_str} ({item_count} item{'s' if item_count != 1 else ''})"
    msg["From"]    = GMAIL_USER
    msg["To"]      = DIGEST_RECIPIENT
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PW)
        server.sendmail(GMAIL_USER, DIGEST_RECIPIENT, msg.as_string())


def mark_read(mail, email_ids):
    for eid in email_ids:
        mail.store(eid, "+FLAGS", "\\Seen")


def main():
    client = Groq(api_key=GROQ_API_KEY)

    print(f"[{datetime.now():%H:%M}] Connecting to Gmail...")
    mail = connect_imap()
    mail.select("inbox")

    _, data = mail.search(None, "UNSEEN")
    email_ids = data[0].split()
    print(f"[{datetime.now():%H:%M}] Found {len(email_ids)} unread email(s)")

    items = []
    processed_ids = []

    for eid in email_ids:
        _, raw = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(raw[0][1])
        subject, sender, body = extract_email_content(msg)

        url = extract_url(body)

        if url:
            print(f"  → URL detected: {url}")
            try:
                page_text = fetch_webpage_text(url)
                summary   = summarize(client, url, page_text, url=url)
                items.append({
                    "type":    "webpage",
                    "source":  url,
                    "url":     url,
                    "summary": summary,
                })
            except Exception as e:
                print(f"    Failed to fetch {url}: {e}")
        else:
            print(f"  → Newsletter: {subject[:60]}")
            try:
                summary = summarize(client, subject, body)
                items.append({
                    "type":    "newsletter",
                    "source":  subject,
                    "sender":  sender,
                    "summary": summary,
                })
            except Exception as e:
                print(f"    Failed to summarize '{subject}': {e}")

        processed_ids.append(eid)

    print(f"[{datetime.now():%H:%M}] Sending digest ({len(items)} items) to {DIGEST_RECIPIENT}...")
    html = build_digest_html(items)
    send_digest(html, len(items))

    mark_read(mail, processed_ids)
    mail.logout()
    print(f"[{datetime.now():%H:%M}] Done ✓")


if __name__ == "__main__":
    main()
