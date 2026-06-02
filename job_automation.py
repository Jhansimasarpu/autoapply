"""
=============================================================
  JOB ALERT AUTOMATION — Jhansi Masarpu
  Monitors CareerLink, ADP, BeachShop for new part-time jobs
  Tailors resume via FREE Gemini API → emails PDF to you
  100% FREE — no paid APIs
=============================================================

SETUP (one-time, ~5 minutes):
------------------------------
1. Install dependencies:
      pip install requests beautifulsoup4 reportlab schedule python-dotenv

2. Create a file named .env in the same folder:
      GEMINI_API_KEY=AIzaSy...         ← your free Gemini key
      GMAIL_ADDRESS=masarpujhansi@gmail.com
      GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
      ALERT_EMAIL=masarpujhansi@gmail.com

3. Run:
      python job_automation.py
=============================================================
"""

import os, re, json, time, hashlib, smtplib, logging, schedule, requests
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
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER

load_dotenv()

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "AIzaSyAQ.Ab8RN6ItvuWwDmno7jQ08jaV1MZJ2DibExNopJtQT1iaT39Vbw")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "masarpujhansi@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
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
def load_seen():
    if Path(SEEN_JOBS_FILE).exists():
        return set(json.loads(Path(SEEN_JOBS_FILE).read_text()))
    return set()

def save_seen(seen):
    Path(SEEN_JOBS_FILE).write_text(json.dumps(list(seen)))

def job_id(title, company, url):
    return hashlib.md5(f"{title}{company}{url}".encode()).hexdigest()

# ── Scrapers ─────────────────────────────────────────────────────────────────
def scrape_careerlink():
    jobs = []
    try:
        url = ("https://www.careeronestop.org/Toolkit/Jobs/find-jobs-results.aspx"
               "?keyword=part+time+student+assistant&location=Long+Beach%2C+CA&source=NLX&currentpage=1")
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select(".job-title, [class*='jobTitle']")[:10]:
            title = card.get_text(strip=True)
            if any(k in title.lower() for k in KEYWORDS):
                jobs.append({"title": title, "company": "CareerLink listing",
                             "description": title, "url": url, "source": "CareerLink"})
    except Exception as e:
        log.warning(f"CareerLink scrape error: {e}")
    return jobs

def scrape_adp():
    jobs = []
    try:
        url = "https://jobs.adp.com/job-search-results/?keyword=part+time+student&location=Long+Beach%2C+CA&radius=10"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("h2.job-title, .jobTitle")[:10]:
            title = card.get_text(strip=True)
            link = card.find_parent("a") or card
            href = link.get("href", url)
            if not href.startswith("http"):
                href = "https://jobs.adp.com" + href
            if any(k in title.lower() for k in KEYWORDS):
                jobs.append({"title": title, "company": "ADP listing",
                             "description": title, "url": href, "source": "ADP"})
    except Exception as e:
        log.warning(f"ADP scrape error: {e}")
    return jobs

def scrape_beachshop():
    jobs = []
    try:
        url = "https://www.csulb.edu/beach-shops/employment"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.find_all(["h2","h3","h4","li","p"]):
            text = tag.get_text(strip=True)
            if any(k in text.lower() for k in ["hiring","position","apply","job","student"]):
                jobs.append({"title": text[:120], "company": "CSULB Beach Shop",
                             "description": text, "url": url, "source": "BeachShop"})
                if len(jobs) >= 5: break
    except Exception as e:
        log.warning(f"BeachShop scrape error: {e}")
    return jobs

def scrape_csulb():
    jobs = []
    try:
        url = "https://www.csulb.edu/career-development/on-campus-employment"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True)[:30]:
            title = a.get_text(strip=True)
            if any(k in title.lower() for k in KEYWORDS) and len(title) > 8:
                link = a["href"]
                if not link.startswith("http"):
                    link = "https://www.csulb.edu" + link
                jobs.append({"title": title, "company": "CSULB",
                             "description": title, "url": link, "source": "CSULB"})
    except Exception as e:
        log.warning(f"CSULB scrape error: {e}")
    return jobs

# ── Gemini resume tailor (FREE) ───────────────────────────────────────────────
def tailor_resume(job):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""You are an expert resume writer. Rewrite the candidate's resume below to be
perfectly tailored for the following job posting.

Rules:
- Keep all facts accurate — do NOT invent experience or skills
- Reorder bullet points so the most relevant experience appears first
- Mirror keywords from the job description naturally
- Keep the same sections and overall length
- Return ONLY the plain-text resume, no commentary, no markdown

JOB TITLE: {job['title']}
COMPANY: {job['company']}
JOB DESCRIPTION: {job['description']}

BASE RESUME:
{BASE_RESUME}"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=payload, timeout=30)
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

# ── PDF builder ───────────────────────────────────────────────────────────────
def build_pdf(resume_text, job_title):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    name_style = ParagraphStyle("name", fontSize=16, fontName="Helvetica-Bold",
                                alignment=TA_CENTER, spaceAfter=4)
    head_style = ParagraphStyle("head", fontSize=11, fontName="Helvetica-Bold",
                                spaceBefore=10, spaceAfter=2)
    body_style = ParagraphStyle("body", fontSize=10, fontName="Helvetica",
                                leading=14, spaceAfter=2)
    story = []
    for i, line in enumerate(resume_text.splitlines()):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
        elif i == 0:
            story.append(Paragraph(line, name_style))
        elif re.match(r'^[A-Z\s&/|–-]{4,}$', line) and len(line) < 60:
            story.append(Paragraph(line, head_style))
        else:
            story.append(Paragraph(line.replace("•", "&#x2022;"), body_style))
    doc.build(story)
    return buf.getvalue()

# ── Email sender ──────────────────────────────────────────────────────────────
def send_email(job, pdf_bytes):
    safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50]
    filename   = f"Jhansi_Resume_{safe_title.replace(' ','_')}.pdf"
    msg = MIMEMultipart()
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ALERT_EMAIL
    msg["Subject"] = f"New Job Alert: {job['title']} — {job['source']}"
    body = f"""Hi Jhansi,

A new job match was found!

Title:   {job['title']}
Source:  {job['source']}
Link:    {job['url']}

Your tailored resume is attached as a PDF — ready to upload.

Steps:
  1. Open the link above
  2. Log in and upload the attached PDF
  3. Submit — done in 2 minutes!

Good luck!
— AutoApply Bot
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
    log.info(f"Email sent: {job['title']}")

# ── Main check ────────────────────────────────────────────────────────────────
def check_jobs():
    log.info("Checking for new jobs...")
    seen = load_seen()
    new_count = 0
    all_jobs = scrape_careerlink() + scrape_adp() + scrape_beachshop() + scrape_csulb()
    for job in all_jobs:
        jid = job_id(job["title"], job["company"], job["url"])
        if jid in seen:
            continue
        log.info(f"New job: {job['title']} ({job['source']})")
        try:
            tailored = tailor_resume(job)
            pdf      = build_pdf(tailored, job["title"])
            send_email(job, pdf)
            seen.add(jid)
            new_count += 1
            time.sleep(2)
        except Exception as e:
            log.error(f"Failed '{job['title']}': {e}")
    save_seen(seen)
    log.info(f"Done. {new_count} new job(s) processed.")

# ── Schedule ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not GMAIL_APP_PASSWORD:
        raise SystemExit("Set GMAIL_APP_PASSWORD in your .env file.")
    log.info("AutoApply bot started — FREE Gemini API")
    log.info(f"Alerts → {ALERT_EMAIL} at 8AM and 6PM daily")
    check_jobs()
    schedule.every().day.at("08:00").do(check_jobs)
    schedule.every().day.at("18:00").do(check_jobs)
    while True:
        schedule.run_pending()
        time.sleep(60)
