# QA Job Circular Matcher

A local Streamlit app that reads the daily QA job circular PDF, finds every job posting, and ranks
them against your **Automation QA** and **Manual QA** master resumes.

Runs fully offline: no API key, no LLM, no internet needed.

## Setup (one time)

Requires Python 3.10+.

```powershell
cd Auto_Apply
pip install -r requirements.txt
```

Add your resumes, either by:
- pasting plain text into `resumes/automation_resume.txt` and `resumes/manual_resume.txt`, or
- opening the **Resumes** tab in the app, pasting, and clicking **Save**.

The Resumes tab shows which skills were recognised in each resume, so you can see what is used for matching.

## Daily use

```powershell
streamlit run app.py
```

1. The app opens at http://localhost:8501.
2. Drag the day's PDF (e.g. `3-6 Years - QA Jobs.pdf`) onto the uploader. Results appear immediately.
3. Review the **Automation QA Matches** and **Manual QA Matches** tabs (highest score first).
4. Each card has a one-click **Copy** for the HR email, an **Email** button that opens your mail
   client with the subject `Application for <Role> - <Your Name>`, and an **Applied** checkbox.
5. Click **Export to CSV** in each tab to save the day's tracked applications.

## How matching works

1. **Read the PDF**: `pypdf` extracts text from every page (`pdfplumber` as fallback). Repeating
   headers, WhatsApp promos and the intro page are removed.
2. **Find postings**: each posting in the circular ends with a line containing the HR email, so the
   text is split there. The first lines of each posting give location, company, role and experience.
   If the company isn't named, it is taken from the email domain. Publisher emails such as
   `swtestingstudio@gmail.com` are ignored.
3. **Detect skills**: a built-in dictionary of ~80 QA skills (Selenium, Playwright, Postman, STLC,
   SQL, Jira, banking domain, ...) is matched against each posting and each resume.
4. **Score (0-100%)**: 85% is the share of the posting's skills found in your resume (tools and
   languages count more than general practices), and 15% is how well your experience fits the
   posting's range. Postings with fewer than 3 recognisable skills are capped at 85%.
5. **Sort into lists**: jobs that need automation tools go to the Automation list (scored against the
   automation resume). Others go to the Manual list. Jobs asking for both go where they score higher.

## Sidebar settings

| Setting | Purpose |
| --- | --- |
| Your total experience | Compared with each job's experience range (pre-filled from your resume) |
| Minimum match score | Default 50%; postings below it are hidden |
| Show in both lists | Lets jobs that need both manual and automation skills appear in both lists |
| Your name | Used in the mailto subject |
| Ignored admin emails | Publisher addresses that are never treated as HR contacts |

## Adding skills

If a skill you care about isn't recognised, add a line to the `_SKILLS` list in `matcher.py`:

```python
("Katalon", 3, True, r"\bkatalon\b"),   # name, weight, is automation tool, regex
```

## Troubleshooting

- **"No readable text was found"**: the PDF is scanned/image-only; OCR it first.
- **No postings found**: the parser expects each posting to end with an HR email line, as in the
  Software Testing Studio circulars.
