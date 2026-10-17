"""Offline job matching: PDF extraction, rule-based posting parsing, and
skill-keyword scoring against the Automation and Manual QA resumes."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

DEFAULT_IGNORED_EMAILS = {"swtestingstudio@gmail.com"}
PUBLIC_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Lines that repeat across the circular and are never part of a posting.
NOISE_PATTERNS = [
    re.compile(r"^\s*Software Testing Studio \|", re.I),
    re.compile(r"^\s*Disclaimer:", re.I),
    re.compile(r"^\s*For Interview Preparation", re.I),
    re.compile(r"^\s*WhatsApp\b", re.I),
    re.compile(r"^\s*Interview Prep \|", re.I),
    re.compile(r"^\s*Money Meter\b", re.I),
]

EXP_LINE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*\+?\s*(?:yrs?|years?)\.?\s*$", re.I)
ROLE_HINT_RE = re.compile(
    r"\b(qa|qe|test|tester|testing|engineer|sdet|automation|analyst|lead|quality)\b", re.I)
RESUME_EXP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", re.I)


class PipelineError(Exception):
    """User-facing error raised for unrecoverable failures."""


# ---------------------------------------------------------------------------
# Skill dictionary: (name, weight, is_automation_tool, regex)
# Weight 3 = tools/languages, 2 = domains & key areas, 1 = general practices.
# ---------------------------------------------------------------------------

_SKILLS: list[tuple[str, int, bool, str]] = [
    # Automation tools & frameworks
    ("Selenium", 3, True, r"\bselenium\b"),
    ("Playwright", 3, True, r"\bplaywright\b"),
    ("Cypress", 3, True, r"\bcypress\b"),
    ("Appium", 3, True, r"\bappium\b"),
    ("Tosca", 3, True, r"\btosca\b"),
    ("Robot Framework", 3, True, r"\brobot framework\b"),
    ("PyTest", 3, True, r"\bpytest\b"),
    ("TestNG", 3, True, r"\btestng\b"),
    ("JUnit", 2, True, r"\bjunit\b"),
    ("Cucumber/BDD", 2, True, r"\bcucumber\b|\bbdd\b"),
    ("Rest Assured", 3, True, r"\brest\s*assured\b"),
    ("XCUITest", 3, True, r"\bxcuitest\b"),
    ("Page Object Model", 2, True, r"page object|\bpom\b"),
    ("Framework Design", 2, True, r"framework (?:design|development)|automation frameworks?|frameworks? from scratch"),
    ("Automation Testing", 2, True, r"\bautomat(?:ion|ed)\b|\bsdet\b"),
    # Languages
    ("Python", 3, False, r"\bpython\b"),
    ("Java", 3, False, r"\bjava\b"),
    ("JavaScript", 3, False, r"\bjavascript\b|\bnode\.?js\b"),
    ("TypeScript", 3, False, r"\btypescript\b"),
    ("C#", 3, False, r"\bc#"),
    ("Groovy", 2, False, r"\bgroovy\b"),
    ("Go", 2, False, r"\bgolang\b|\bgo\s*/|/\s*go\b"),
    ("Swift", 2, False, r"\bswift\b"),
    ("PySpark", 2, False, r"\bpyspark\b"),
    ("Shell Scripting", 1, False, r"shell scripting|\bbash\b"),
    # DevOps & cloud
    ("CI/CD", 2, False, r"\bci\s*[/-]\s*cd\b"),
    ("Jenkins", 2, False, r"\bjenkins\b"),
    ("GitHub Actions", 2, False, r"github actions"),
    ("Azure DevOps", 2, False, r"azure devops"),
    ("Git", 1, False, r"\bgit\b|\bgithub\b"),
    ("Maven", 1, False, r"\bmaven\b"),
    ("Docker", 2, False, r"\bdocker\b"),
    ("Kubernetes", 2, False, r"\bkubernetes\b|\bhelm\b|openshift"),
    ("AWS", 2, False, r"\baws\b"),
    ("Azure", 2, False, r"\bazure\b(?!\s*devops)"),
    ("GCP", 2, False, r"\bgcp\b|google cloud"),
    ("Linux", 1, False, r"\blinux\b"),
    # API & data
    ("API Testing", 2, False, r"\bapis?\b"),
    ("Postman", 2, False, r"\bpostman\b"),
    ("Swagger", 1, False, r"\bswagger\b"),
    ("SoapUI", 1, False, r"\bsoap\s*ui\b"),
    ("REST/SOAP", 1, False, r"\brest\b(?!\s*assured)|\bsoap\b"),
    ("GraphQL", 2, False, r"\bgraphql\b"),
    ("Microservices", 1, False, r"microservices?"),
    ("SQL", 2, False, r"\bsql\b|\bmysql\b"),
    ("Database Testing", 1, False, r"database|backend testing|data validation|\bdb testing\b"),
    ("ETL/Data Warehouse", 2, False, r"\betl\b|data warehouse|data pipeline|big data|data migration|data conversion"),
    ("Kafka", 2, False, r"\bkafka\b"),
    ("MDM", 2, False, r"\bmdm\b|\breltio\b"),
    # Testing practices
    ("Manual Testing", 2, False, r"\bmanual\b"),
    ("Functional Testing", 1, False, r"\bfunctional\b"),
    ("Regression Testing", 1, False, r"\bregression\b"),
    ("Integration Testing", 1, False, r"\bintegration\b"),
    ("Smoke/Sanity", 1, False, r"\bsmoke\b|\bsanity\b"),
    ("End-to-End Testing", 1, False, r"end[- ]to[- ]end|\be2e\b"),
    ("UAT/SIT", 1, False, r"\buat\b|\bsit\b"),
    ("Exploratory Testing", 1, False, r"\bexploratory\b"),
    ("SDLC/STLC", 1, False, r"\bstlc\b|\bsdlc\b"),
    ("Test Case Design", 1, False, r"test cases?|test scenarios?"),
    ("Test Planning", 1, False, r"test plan|test strategy|test estimation"),
    ("Defect Management", 1, False, r"\bdefects?\b|bug tracking|bug reporting"),
    ("Jira", 1, False, r"\bjira\b"),
    ("Test Management Tools", 1, False, r"\balm\b|\bhpqc\b|testrail|zephyr|\bxray\b"),
    ("Agile/Scrum", 1, False, r"\bagile\b|\bscrum\b"),
    ("Web Testing", 1, False, r"\bweb\b|cross[- ]browser"),
    ("Mobile Testing", 2, False, r"\bmobile\b|\bandroid\b|\bios\b"),
    ("Performance Testing", 2, False, r"performance|load testing|\bjmeter\b|\blocust\b|\bk6\b"),
    ("Security Testing", 2, False, r"security testing|\bowasp\b"),
    ("AI/GenAI Testing", 2, False, r"\bai\b|\bgenai\b|agentic|\beval\b"),
    # Domains
    ("Banking/FinTech", 2, False, r"banking|financial|finacle|temenos|lending|\bnbfc\b|fintech|\blos\b|\blms\b"),
    ("Payments/Cards", 2, False, r"payments?|debit card|credit card|\bpos\b|\bemv\b|\batm\b"),
    ("Insurance", 2, False, r"insurance"),
    ("Healthcare", 2, False, r"health\s*care|\bclaims\b|\bpayer\b"),
    ("E-commerce", 1, False, r"e-?\s*commerce"),
    ("Mainframe", 3, False, r"mainframe|\bcobol\b|\bjcl\b|\bcics\b"),
    ("ServiceNow", 3, False, r"servicenow"),
    ("Salesforce", 3, False, r"salesforce"),
    ("BI/Reporting", 2, False, r"power bi|tableau|looker|\bssrs\b|\bqlik\b"),
]

SKILLS = [(name, weight, auto, re.compile(rx, re.I)) for name, weight, auto, rx in _SKILLS]
SKILL_WEIGHT = {name: weight for name, weight, _, _ in SKILLS}
AUTOMATION_TOOLS = {name for name, _, auto, _ in SKILLS if auto}


def detect_skills(text: str) -> list[str]:
    return [name for name, _, _, rx in SKILLS if rx.search(text)]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    pages: list[str]
    failed_pages: list[int] = field(default_factory=list)
    empty_pages: list[int] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(len(p) for p in self.pages)


@dataclass
class Posting:
    company: str
    role: str
    experience: str
    location: str
    hr_email: str
    description: str
    skills: list[str]
    job_type: str = "manual"  # automation | manual | hybrid
    automation_score: int = 0
    manual_score: int = 0
    automation_matching_skills: list[str] = field(default_factory=list)
    manual_matching_skills: list[str] = field(default_factory=list)
    automation_reasoning: str = ""
    manual_reasoning: str = ""


@dataclass
class Match:
    match_score: int
    company: str
    role: str
    experience: str
    location: str
    hr_email: str
    key_matching_skills: list[str]
    reasoning: str


@dataclass
class MatchResults:
    automation_matches: list[Match] = field(default_factory=list)
    manual_matches: list[Match] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _clean_page(text: str) -> str:
    text = (text.replace("\u2013", "-").replace("\u2014", "-")
                .replace("\ufffd", " ").replace("amp;", "&"))
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not any(p.match(ln) for p in NOISE_PATTERNS)]
    return "\n".join(lines)


def extract_pdf_text(pdf_bytes: bytes) -> ExtractionResult:
    """Extract text per page with pypdf, falling back to pdfplumber per page."""
    if not pdf_bytes:
        raise PipelineError("The uploaded file is empty.")

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise PipelineError("The PDF is password-protected and cannot be read.") from exc
        page_count = len(reader.pages)
    except PdfReadError as exc:
        raise PipelineError(f"Could not read the PDF (corrupt or not a PDF): {exc}") from exc
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"Could not open the PDF: {exc}") from exc

    if page_count == 0:
        raise PipelineError("The PDF contains no pages.")

    plumber_doc = None
    pages: list[str] = []
    failed: list[int] = []
    empty: list[int] = []

    for idx in range(page_count):
        text = ""
        try:
            text = reader.pages[idx].extract_text() or ""
        except Exception as exc:
            log.warning("pypdf failed on page %s: %s", idx + 1, exc)

        if not text.strip():
            try:
                if plumber_doc is None:
                    import pdfplumber
                    plumber_doc = pdfplumber.open(io.BytesIO(pdf_bytes))
                text = plumber_doc.pages[idx].extract_text() or ""
            except Exception as exc:
                log.warning("pdfplumber failed on page %s: %s", idx + 1, exc)
                failed.append(idx + 1)

        cleaned = _clean_page(text)
        if not cleaned and (idx + 1) not in failed:
            empty.append(idx + 1)
        pages.append(cleaned)

    if plumber_doc is not None:
        plumber_doc.close()

    result = ExtractionResult(pages=pages, failed_pages=failed, empty_pages=empty)
    if result.total_chars < 50:
        raise PipelineError(
            "No readable text was found in the PDF. It may be a scanned/image-only "
            "document - run it through OCR first."
        )
    return result


# ---------------------------------------------------------------------------
# Posting parsing
# ---------------------------------------------------------------------------

def _company_from_email(email: str) -> str:
    domain = email.split("@", 1)[1].lower()
    if domain in PUBLIC_EMAIL_DOMAINS:
        return ""
    return domain.split(".")[0].replace("-", " ").title()


def _parse_block(block: list[str], ignored: set[str]) -> Posting | None:
    emails = [e.strip(".").lower() for e in EMAIL_RE.findall(block[-1])]
    emails = [e for e in emails if e not in ignored]
    body = block[:-1]
    if not emails or len(body) < 2:
        return None

    location = body[0]
    exp_idx = next((i for i, ln in enumerate(body[1:5], start=1) if EXP_LINE_RE.match(ln)), None)
    if exp_idx is not None:
        header, experience, desc = body[1:exp_idx], body[exp_idx], body[exp_idx + 1:]
        if len(header) >= 2:
            company, role = header[0], " ".join(header[1:])
        else:
            company, role = "", header[0] if header else ""
    else:
        experience = ""
        if (len(body) >= 3 and len(body[2]) <= 45 and ":" not in body[2]
                and ROLE_HINT_RE.search(body[2]) and not ROLE_HINT_RE.search(body[1])):
            company, role, desc = body[1], body[2], body[3:]
        else:
            company, role, desc = "", body[1], body[2:]

    hr_email = emails[0]
    company = company or _company_from_email(hr_email)
    description = "\n".join(desc)
    return Posting(
        company=company.strip(),
        role=role.strip(),
        experience=experience.strip(),
        location=location.strip(),
        hr_email=hr_email,
        description=description,
        skills=detect_skills(f"{role}\n{description}"),
    )


def parse_postings(extraction: ExtractionResult,
                   ignored_emails: set[str] | None = None) -> list[Posting]:
    """Split the circular into postings. Each posting ends with the line that
    carries its contact email; leading pages without any email (intro/how-to-apply)
    are skipped."""
    ignored = {e.lower() for e in (ignored_emails or DEFAULT_IGNORED_EMAILS)}
    pages = list(extraction.pages)
    while pages and not EMAIL_RE.search(pages[0]):
        pages.pop(0)

    lines = [ln for page in pages for ln in page.splitlines() if ln.strip()]
    blocks: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        current.append(ln)
        if EMAIL_RE.search(ln):
            blocks.append(current)
            current = []

    postings, seen = [], set()
    for block in blocks:
        posting = _parse_block(block, ignored)
        if posting is None:
            continue
        key = (posting.hr_email, posting.role.lower())
        if key in seen:
            continue
        seen.add(key)
        postings.append(posting)
    return postings


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def parse_experience_range(text: str) -> tuple[float | None, float | None]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(\+)?\s*(?:yrs?|years?)", text or "", re.I)
    if not m:
        return None, None
    low = float(m.group(1))
    if m.group(2):
        return low, float(m.group(2))
    return low, None if m.group(3) else low


def guess_resume_experience(*resumes: str) -> float | None:
    for text in resumes:
        values = [float(v) for v in RESUME_EXP_RE.findall(text or "") if float(v) <= 40]
        if values:
            return max(values)
    return None


def _experience_fit(job_exp: str, my_years: float) -> tuple[float, str]:
    low, high = parse_experience_range(job_exp)
    if low is None:
        return 0.7, "experience not specified"
    upper = high if high is not None else float("inf")
    if low <= my_years <= upper:
        return 1.0, f"your {my_years:g} yrs fits {job_exp}"
    gap = low - my_years if my_years < low else my_years - upper
    if gap <= 1:
        return 0.5, f"your {my_years:g} yrs is close to {job_exp}"
    return 0.0, f"your {my_years:g} yrs is outside {job_exp}"


def score_posting(job_skills: list[str], job_exp: str, resume_skills: set[str],
                  my_years: float) -> tuple[int, list[str], str]:
    if not job_skills:
        return 0, [], "No recognisable skills in this posting."
    matched = [s for s in job_skills if s in resume_skills]
    missing = [s for s in job_skills if s not in resume_skills]
    total_w = sum(SKILL_WEIGHT[s] for s in job_skills)
    matched_w = sum(SKILL_WEIGHT[s] for s in matched)
    exp_fit, exp_note = _experience_fit(job_exp, my_years)
    score = round(100 * (0.85 * matched_w / total_w + 0.15 * exp_fit))

    reasoning = f"Matches {len(matched)} of {len(job_skills)} skills; {exp_note}."
    if len(job_skills) < 3:
        score = min(score, 85)
        reasoning += " Posting has few details, so the score is capped."
    if missing:
        reasoning += f" Missing: {', '.join(missing[:5])}."
    return score, matched, reasoning


def classify_job(p: Posting) -> str:
    has_auto = bool(AUTOMATION_TOOLS & set(p.skills))
    manual_in_role = re.search(r"\bmanual\b|\bfunctional tester\b", p.role, re.I)
    manual_in_text = re.search(r"\bmanual\b", p.description, re.I)
    if manual_in_role and not has_auto:
        return "manual"
    if has_auto and (manual_in_role or manual_in_text):
        return "hybrid"
    return "automation" if has_auto else "manual"


def score_postings(postings: list[Posting], automation_resume: str, manual_resume: str,
                   my_years: float) -> list[Posting]:
    auto_skills = set(detect_skills(automation_resume))
    manual_skills = set(detect_skills(manual_resume))
    for p in postings:
        p.job_type = classify_job(p)
        p.automation_score, p.automation_matching_skills, p.automation_reasoning = score_posting(
            p.skills, p.experience, auto_skills, my_years)
        p.manual_score, p.manual_matching_skills, p.manual_reasoning = score_posting(
            p.skills, p.experience, manual_skills, my_years)
    return postings


def bucket_postings(postings: list[Posting], threshold: int = 50,
                    allow_both: bool = False) -> MatchResults:
    """Automation jobs go to the automation list and manual jobs to the manual list.
    Hybrid jobs go where they score higher, or to both when allow_both is set."""
    results = MatchResults()
    for p in postings:
        auto_ok = p.job_type in ("automation", "hybrid") and p.automation_score >= threshold
        manual_ok = p.job_type in ("manual", "hybrid") and p.manual_score >= threshold
        if p.job_type == "hybrid" and auto_ok and manual_ok and not allow_both:
            if p.automation_score >= p.manual_score:
                manual_ok = False
            else:
                auto_ok = False
        common = dict(company=p.company, role=p.role, experience=p.experience,
                      location=p.location, hr_email=p.hr_email)
        if auto_ok:
            results.automation_matches.append(Match(
                match_score=p.automation_score, key_matching_skills=p.automation_matching_skills[:8],
                reasoning=p.automation_reasoning, **common))
        if manual_ok:
            results.manual_matches.append(Match(
                match_score=p.manual_score, key_matching_skills=p.manual_matching_skills[:8],
                reasoning=p.manual_reasoning, **common))

    results.automation_matches.sort(key=lambda m: m.match_score, reverse=True)
    results.manual_matches.sort(key=lambda m: m.match_score, reverse=True)
    return results
