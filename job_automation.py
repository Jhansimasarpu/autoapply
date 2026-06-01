"""
=============================================================
  JOB ALERT AUTOMATION — Jhansi Masarpu
  Monitors CareerLink, ADP, BeachShop for new part-time jobs
  Tailors resume via Claude API → emails PDF to you
=============================================================

SETUP INSTRUCTIONS (one-time, ~10 minutes):
--------------------------------------------
1. Install dependencies:
      pip install requests beautifulsoup4 anthropic reportlab schedule python-dotenv

2. Create a file named  .env  in the same folder as this script:
      ANTHROPIC_API_KEY=sk-ant-xxxxxx       ← get from console.anthropic.com
      GMAIL_ADDRESS=masarpujhansi@gmail.com
      GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx  ← Google Account → Security → App Passwords
      ALERT_EMAIL=masarpujhansi@gmail.com

3. Place your base resume text in  base_resume.txt  (same folder).
   Paste the plain-text content of your resume there — both versions are
   merged for you at the bottom of this file as a starter.

4. Run:  python job_automation.py
   It will check twice a day (8 AM and 6 PM) and email you when new jobs appear.
   To run continuously on Replit: wrap in a Replit Always-On repl.

=============================================================
"""

import os
import re
import json
import time
import hashlib
import smtplib
import logging
import schedule
import requests

from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT, TA_CENTER
import anthropic

# ── Config ──────────────────────────────────────────────────────────────────
load_dotenv()

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_EMAIL        = os.getenv("ALERT_EMAIL", "masarpujhansi@gmail.com")

SEEN_JOBS_FILE = "seen_jobs.json"
BASE_RESUME    = Path("base_resume.txt").read_text() if Path("base_resume.txt").exists() else ""

KEYWORDS = [
    "part time", "part-time", "student assistant", "csulb",
    "retail", "customer service", "warehouse", "logistics",
    "food", "beverage", "administrative", "office assistant"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("job_bot.log")]
)
log = logging.getLogger(__name__)

# ── Seen-jobs store ──────────────────────────────────────────────────────────
def load_seen() -> set:
    if Path(SEEN_JOBS_FILE).exists():
        return set(json.loads(Path(SEEN_JOBS_FILE).read_text()))
    return set()

def save_seen(seen: set):
    Path(SEEN_JOBS_FILE).write_text(json.dumps(list(seen)))

def job_id(title: str, company: str, url: str) -> str:
    return hashlib.md5(f"{title}{company}{url}".encode()).hexdigest()

# ── Scrapers ─────────────────────────────────────────────────────────────────

def scrape_careerlink() -> list[dict]:
    """
    Scrapes PA CareerLink public job search.
    Adjust the URL if you use a different state/region CareerLink.
    """
    jobs = []
    for kw in ["part time student assistant csulb", "part time retail Long Beach"]:
        try:
            url = (
                "https://www.careeronestop.org/Toolkit/Jobs/find-jobs-results.aspx"
                f"?keyword={requests.utils.quote(kw)}&location=Long+Beach%2C+CA"
                "&source=NLX&currentpage=1"
            )
            resp = requests.get(url, timeout=15,
                                headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select(".job-title, [class*='jobTitle']")[:10]:
                title = card.get_text(strip=True)
                if any(k in title.lower() for k in KEYWORDS):
                    jobs.append({
                        "title": title,
                        "company": "CareerLink listing",
                        "description": title,
                        "url": url,
                        "source": "CareerLink"
                    })
        except Exception as e:
            log.warning(f"CareerLink scrape error: {e}")
    return jobs


def scrape_adp() -> list[dict]:
    """
    ADP Job Board public search — searches for CSULB / Long Beach part-time roles.
    ADP hosts job postings at workforcenow.adp.com for many employers.
    """
    jobs = []
    try:
        url = (
            "https://jobs.adp.com/job-search-results/"
            "?keyword=part+time+student&location=Long+Beach%2C+CA&radius=10"
        )
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("h2.job-title, .jobTitle, [data-job-title]")[:10]:
            title = card.get_text(strip=True)
            parent = card.find_parent("a") or card
            link = parent.get("href", url)
            if not link.startswith("http"):
                link = "https://jobs.adp.com" + link
            if any(k in title.lower() for k in KEYWORDS):
                jobs.append({
                    "title": title,
                    "company": "ADP listing",
                    "description": title,
                    "url": link,
                    "source": "ADP"
                })
    except Exception as e:
        log.warning(f"ADP scrape error: {e}")
    return jobs


def scrape_beachshop() -> list[dict]:
    """
    CSULB Beach Shop student job postings.
    """
    jobs = []
    try:
        url = "https://www.csulb.edu/beach-shops/employment"
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        # Generic: grab any text that looks like a job posting
        for tag in soup.find_all(["h2", "h3", "h4", "li", "p"]):
            text = tag.get_text(strip=True)
            if any(k in text.lower() for k in ["hiring", "position", "apply", "job", "student"]):
                jobs.append({
                    "title": text[:120],
                    "company": "CSULB Beach Shop",
                    "description": text,
                    "url": url,
                    "source": "BeachShop"
                })
                if len(jobs) >= 5:
                    break
    except Exception as e:
        log.warning(f"BeachShop scrape error: {e}")
    return jobs


def scrape_csulb_jobs() -> list[dict]:
    """
    CSULB Student Employment / Handshake feed.
    """
    jobs = []
    try:
        url = "https://www.csulb.edu/career-development/on-campus-employment"
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True)[:30]:
            title = a.get_text(strip=True)
            if any(k in title.lower() for k in KEYWORDS) and len(title) > 8:
                link = a["href"]
                if not link.startswith("http"):
                    link = "https://www.csulb.edu" + link
                jobs.append({
                    "title": title,
                    "company": "CSULB",
                    "description": title,
                    "url": link,
                    "source": "CSULB"
                })
    except Exception as e:
        log.warning(f"CSULB scrape error: {e}")
    return jobs


# ── Claude resume tailor ─────────────────────────────────────────────────────

def tailor_resume(job: dict) -> str:
    """Call Claude API to rewrite the base resume for this specific job."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are an expert resume writer. Rewrite the candidate's resume below to be
perfectly tailored for the following job posting. Rules:
- Keep all facts accurate (do NOT invent experience or skills)
- Reorder bullet points to surface the most relevant experience first
- Mirror keywords from the job description naturally
- Keep the same sections and overall length
- Return ONLY the plain-text resume, no commentary

JOB TITLE: {job['title']}
COMPANY / SOURCE: {job['company']}
JOB DESCRIPTION:
{job['description']}

BASE RESUME:
{BASE_RESUME}
"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ── PDF builder ──────────────────────────────────────────────────────────────

def build_pdf(resume_text: str, job_title: str) -> bytes:
    """Convert tailored resume plain text to a clean PDF, return bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch
    )
    styles = getSampleStyleSheet()
    name_style = ParagraphStyle("name", fontSize=16, fontName="Helvetica-Bold",
                                alignment=TA_CENTER, spaceAfter=4)
    head_style = ParagraphStyle("head", fontSize=11, fontName="Helvetica-Bold",
                                spaceBefore=10, spaceAfter=2)
    body_style = ParagraphStyle("body", fontSize=10, fontName="Helvetica",
                                leading=14, spaceAfter=2)

    story = []
    for line in resume_text.splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
        elif re.match(r'^[A-Z\s&/|–-]{4,}$', line) and len(line) < 60:
            story.append(Paragraph(line, head_style))
        elif story and isinstance(story[0], Paragraph) and story[0].style == name_style:
            story.append(Paragraph(line, body_style))
        elif not story:
            story.append(Paragraph(line, name_style))
        else:
            story.append(Paragraph(line.replace("•", "&#x2022;"), body_style))

    doc.build(story)
    return buf.getvalue()


# ── Email sender ─────────────────────────────────────────────────────────────

def send_email(job: dict, pdf_bytes: bytes):
    safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50]
    filename   = f"Jhansi_Resume_{safe_title.replace(' ', '_')}.pdf"

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ALERT_EMAIL
    msg["Subject"] = f"🆕 Job Alert: {job['title']} — {job['source']}"

    body = f"""Hi Jhansi,

A new job was found that matches your keywords!

📌  Title:   {job['title']}
🏢  Source:  {job['source']}
🔗  Link:    {job['url']}

Your tailored resume is attached as a PDF — ready to upload.

Steps:
  1. Open the link above
  2. Log in with your credentials
  3. Upload the attached PDF resume
  4. Submit — done!

Good luck! 🎉

— Your Job Bot
"""
    msg.attach(MIMEText(body, "plain"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, ALERT_EMAIL, msg.as_string())

    log.info(f"✉️  Email sent for: {job['title']}")


# ── Main check loop ───────────────────────────────────────────────────────────

def check_jobs():
    log.info("🔍  Checking for new jobs...")
    seen = load_seen()
    new_count = 0

    all_jobs = (
        scrape_careerlink() +
        scrape_adp()        +
        scrape_beachshop()  +
        scrape_csulb_jobs()
    )

    for job in all_jobs:
        jid = job_id(job["title"], job["company"], job["url"])
        if jid in seen:
            continue

        log.info(f"🆕  New job found: {job['title']} ({job['source']})")
        try:
            tailored = tailor_resume(job)
            pdf      = build_pdf(tailored, job["title"])
            send_email(job, pdf)
            seen.add(jid)
            new_count += 1
            time.sleep(2)   # be polite to the API
        except Exception as e:
            log.error(f"Failed processing '{job['title']}': {e}")

    save_seen(seen)
    log.info(f"✅  Done. {new_count} new job(s) processed.")


# ── Schedule: twice a day ─────────────────────────────────────────────────────

if __name__ == "__main__":
    if not ANTHROPIC_API_KEY:
        raise SystemExit("❌  Set ANTHROPIC_API_KEY in your .env file.")
    if not GMAIL_APP_PASSWORD:
        raise SystemExit("❌  Set GMAIL_APP_PASSWORD in your .env file.")

    log.info("🤖  Job automation bot started.")
    log.info(f"📅  Schedule: 8:00 AM and 6:00 PM daily → alerts to {ALERT_EMAIL}")

    # Run once immediately on start
    check_jobs()

    schedule.every().day.at("08:00").do(check_jobs)
    schedule.every().day.at("18:00").do(check_jobs)

    while True:
        schedule.run_pending()
        time.sleep(60)
