"""QA Job Circular Matcher - Streamlit UI.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from matcher import (
    DEFAULT_IGNORED_EMAILS,
    Match,
    MatchResults,
    PipelineError,
    bucket_postings,
    detect_skills,
    extract_pdf_text,
    guess_resume_experience,
    parse_postings,
    score_postings,
)

BASE_DIR = Path(__file__).parent
RESUME_DIR = BASE_DIR / "resumes"
RESUME_FILES = {
    "automation": RESUME_DIR / "automation_resume.txt",
    "manual": RESUME_DIR / "manual_resume.txt",
}
PLACEHOLDER_MARKER = "PASTE YOUR"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="QA Job Matcher", page_icon="🎯", layout="wide")

st.markdown(
    """
    <style>
      .score-badge {display:inline-block; padding:6px 12px; border-radius:999px;
                    font-weight:700; font-size:1.05rem; color:#fff; min-width:64px; text-align:center;}
      .score-high {background:#16a34a;} .score-mid {background:#ca8a04;} .score-low {background:#ea580c;}
      .skill-tag {display:inline-block; background:#e0e7ff; color:#3730a3; padding:2px 10px;
                  margin:2px 4px 2px 0; border-radius:999px; font-size:0.8rem;}
      .job-title {font-size:1.1rem; font-weight:700; margin-bottom:0;}
      .job-company {color:#6b7280; margin-top:0;}
      .mailto-btn {display:inline-block; padding:4px 12px; border-radius:6px; background:#2563eb;
                   color:#fff !important; text-decoration:none !important; font-size:0.85rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Resume storage
# ---------------------------------------------------------------------------

def load_resume(kind: str) -> str:
    path = RESUME_FILES[kind]
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        st.error(f"Could not read {path.name}: {exc}")
        return ""


def save_resume(kind: str, text: str) -> None:
    RESUME_DIR.mkdir(exist_ok=True)
    RESUME_FILES[kind].write_text(text.strip() + "\n", encoding="utf-8")


def resume_is_valid(text: str) -> bool:
    return len(text.strip()) > 100 and PLACEHOLDER_MARKER not in text


# ---------------------------------------------------------------------------
# Sidebar settings
# ---------------------------------------------------------------------------

def sidebar_settings() -> dict:
    st.sidebar.header("⚙️ Settings")

    guessed = guess_resume_experience(load_resume("automation"), load_resume("manual"))
    my_years = st.sidebar.number_input(
        "Your total experience (years)", 0.0, 40.0, float(guessed or 4.0), 0.5,
        help="Compared with each job's experience range. Pre-filled from your resume when possible.",
    )
    threshold = st.sidebar.slider("Minimum match score (%)", 0, 100, 50, 5)
    allow_both = st.sidebar.toggle(
        "Show a job in both lists if it qualifies for both", value=False,
        help="Applies to jobs that ask for both manual and automation skills. "
             "Off: they go only to the list where they score higher.",
    )
    candidate_name = st.sidebar.text_input("Your name (email subject)", value="Mohd Umar Abbasi")
    ignored_raw = st.sidebar.text_area(
        "Ignored admin emails (one per line)",
        value="\n".join(sorted(DEFAULT_IGNORED_EMAILS)), height=80,
    )

    return {
        "my_years": float(my_years),
        "threshold": threshold,
        "allow_both": allow_both,
        "candidate_name": candidate_name.strip(),
        "ignored": frozenset(e.strip().lower() for e in ignored_raw.splitlines() if e.strip()),
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def mailto_link(email: str, role: str, candidate_name: str) -> str:
    subject = f"Application for {role} - {candidate_name}"
    return f"mailto:{email}?subject={quote(subject)}"


def score_class(score: int) -> str:
    return "score-high" if score >= 80 else "score-mid" if score >= 65 else "score-low"


def copy_button(text: str) -> None:
    """One-click clipboard button (Clipboard API with execCommand fallback)."""
    payload = json.dumps(text)
    components.html(
        f"""
        <button id="b" style="padding:4px 12px;border:1px solid #cbd5e1;border-radius:6px;
                background:#f8fafc;cursor:pointer;font-size:0.85rem;font-family:sans-serif;">📋 Copy</button>
        <script>
          const b = document.getElementById('b');
          b.onclick = async () => {{
            const t = {payload};
            try {{ await navigator.clipboard.writeText(t); }}
            catch (e) {{
              const ta = document.createElement('textarea'); ta.value = t;
              document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
            }}
            b.textContent = '✅ Copied'; setTimeout(() => b.textContent = '📋 Copy', 1500);
          }};
        </script>
        """,
        height=38,
    )


def job_id(m: Match) -> str:
    return hashlib.md5(f"{m.company}|{m.role}|{m.hr_email}".encode()).hexdigest()[:12]


def is_applied(m: Match) -> bool:
    return st.session_state.setdefault("applied", {}).get(job_id(m), False)


def _sync_applied(jid: str, widget_key: str) -> None:
    st.session_state.setdefault("applied", {})[jid] = st.session_state[widget_key]


def render_card(m: Match, bucket: str, idx: int, candidate_name: str) -> None:
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 5, 3])
        with c1:
            st.markdown(f'<span class="score-badge {score_class(m.match_score)}">{m.match_score}%</span>',
                        unsafe_allow_html=True)
        with c2:
            st.markdown(
                f'<p class="job-title">{html.escape(m.role or "Unknown role")}</p>'
                f'<p class="job-company">🏢 {html.escape(m.company or "Unknown company")}</p>',
                unsafe_allow_html=True,
            )
            st.markdown(f"📍 {m.location or '—'} &nbsp;&nbsp; 💼 {m.experience or '—'}")
            if m.key_matching_skills:
                st.markdown("".join(f'<span class="skill-tag">{html.escape(s)}</span>'
                                    for s in m.key_matching_skills), unsafe_allow_html=True)
            if m.reasoning:
                st.caption(m.reasoning)
        with c3:
            if m.hr_email:
                st.markdown(f"✉️ `{m.hr_email}`")
                b1, b2 = st.columns(2)
                with b1:
                    copy_button(m.hr_email)
                with b2:
                    st.markdown(
                        f'<a class="mailto-btn" href="{html.escape(mailto_link(m.hr_email, m.role, candidate_name))}" '
                        f'target="_blank">📨 Email</a>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No HR email found in the posting.")
            jid = job_id(m)
            widget_key = f"applied_cb_{bucket}_{idx}_{jid}"
            st.checkbox("Applied", value=is_applied(m), key=widget_key,
                        on_change=_sync_applied, args=(jid, widget_key))


def matches_to_df(matches: list[Match], candidate_name: str) -> pd.DataFrame:
    rows = []
    for m in matches:
        rows.append({
            "Date": date.today().isoformat(),
            "Match Score": m.match_score,
            "Company": m.company,
            "Role": m.role,
            "Experience": m.experience,
            "Location": m.location,
            "HR Email": m.hr_email,
            "Key Matching Skills": ", ".join(m.key_matching_skills),
            "Reasoning": m.reasoning,
            "Mailto": mailto_link(m.hr_email, m.role, candidate_name) if m.hr_email else "",
            "Applied": is_applied(m),
        })
    return pd.DataFrame(rows)


def render_bucket(matches: list[Match], bucket: str, label: str, candidate_name: str) -> None:
    if not matches:
        st.info(f"No {label} above the current threshold.")
        return

    df = matches_to_df(matches, candidate_name)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Matches", len(matches))
    m2.metric("Top score", f"{df['Match Score'].max()}%")
    m3.metric("Average", f"{df['Match Score'].mean():.0f}%")
    m4.metric("Applied", int(df["Applied"].sum()))

    tc1, tc2 = st.columns([3, 1])
    with tc1:
        view = st.radio("View", ["Cards", "Table"], horizontal=True, key=f"view_{bucket}",
                        label_visibility="collapsed")
    with tc2:
        st.download_button(
            "⬇️ Export to CSV",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{bucket}_qa_matches_{date.today().isoformat()}.csv",
            mime="text/csv",
            key=f"csv_{bucket}",
            width="stretch",
        )

    if view == "Table":
        st.dataframe(
            df.drop(columns=["Date"]),
            hide_index=True,
            width="stretch",
            column_config={
                "Match Score": st.column_config.ProgressColumn(
                    "Match Score", format="%d%%", min_value=0, max_value=100),
                "Mailto": st.column_config.LinkColumn("Apply", display_text="📨 Email"),
                "Applied": st.column_config.CheckboxColumn("Applied"),
            },
        )
    else:
        for idx, m in enumerate(matches):
            render_card(m, bucket, idx, candidate_name)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def resumes_page() -> None:
    st.subheader("📄 Master Resumes")
    st.caption(f"Stored as plain text in `{RESUME_DIR.relative_to(BASE_DIR)}/`. "
               "Paste the full text of each resume once; it is reused for every circular.")
    cols = st.columns(2)
    for col, (kind, title) in zip(cols, [("automation", "Automation QA Resume"),
                                          ("manual", "Manual QA Resume")]):
        with col:
            current = load_resume(kind)
            text = st.text_area(title, value=current, height=520, key=f"resume_{kind}")
            status = "✅ Looks good" if resume_is_valid(text) else "⚠️ Missing or still the placeholder"
            st.caption(f"{status} · {len(text.split())} words")
            skills = detect_skills(text) if resume_is_valid(text) else []
            if skills:
                st.markdown("**Skills recognised for matching:** "
                            + "".join(f'<span class="skill-tag">{html.escape(s)}</span>' for s in skills),
                            unsafe_allow_html=True)
            if st.button(f"💾 Save {title}", key=f"save_{kind}", width="stretch"):
                try:
                    save_resume(kind, text)
                    st.success(f"Saved to {RESUME_FILES[kind].name}")
                except OSError as exc:
                    st.error(f"Could not save: {exc}")


def matcher_page(settings: dict) -> None:
    automation_resume = load_resume("automation")
    manual_resume = load_resume("manual")
    missing = [n for n, t in [("Automation", automation_resume), ("Manual", manual_resume)]
               if not resume_is_valid(t)]
    if missing:
        st.warning(f"{' and '.join(missing)} resume not set up yet. Open the **Resumes** tab and paste it in.")

    uploaded = st.file_uploader("Drop today's QA job circular PDF", type=["pdf"],
                                help='e.g. "3-6 Years - QA Jobs.pdf"')
    if uploaded is None or missing:
        return

    pdf_bytes = uploaded.getvalue()
    try:
        with st.spinner("Reading PDF and finding job postings..."):
            parsed = load_postings(pdf_bytes, settings["ignored"])
    except PipelineError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        logging.exception("Unexpected failure")
        st.error(f"Unexpected error while reading the PDF: {type(exc).__name__}: {exc}")
        return

    if parsed["failed_pages"]:
        st.warning(f"Could not read page(s) {parsed['failed_pages']}; they were skipped.")
    if parsed["empty_pages"]:
        st.caption(f"Pages with no text (images/blank): {parsed['empty_pages']}")
    postings = parsed["postings"]
    if not postings:
        st.warning("No job postings were found in this PDF. Each posting needs an HR email line to be detected.")
        return

    file_id = hashlib.sha256(pdf_bytes).hexdigest()
    if st.session_state.get("file_id") != file_id:
        for k in [k for k in st.session_state if str(k).startswith("applied_cb_")]:
            del st.session_state[k]
        st.session_state.update(file_id=file_id, applied={})

    postings = score_postings(postings, automation_resume, manual_resume, settings["my_years"])
    results: MatchResults = bucket_postings(postings, settings["threshold"], settings["allow_both"])

    st.success(f"**{uploaded.name}**: {parsed['pages']} pages, {len(postings)} job postings found.")

    tab_a, tab_m = st.tabs([f"🤖 Automation QA Matches ({len(results.automation_matches)})",
                            f"🧪 Manual QA Matches ({len(results.manual_matches)})"])
    with tab_a:
        render_bucket(results.automation_matches, "automation", "Automation QA matches",
                      settings["candidate_name"])
    with tab_m:
        render_bucket(results.manual_matches, "manual", "Manual QA matches",
                      settings["candidate_name"])

    with st.expander(f"All {len(postings)} parsed postings / JSON"):
        st.download_button("⬇️ Download matches JSON", data=json.dumps(results.to_dict(), indent=2),
                           file_name=f"qa_matches_{date.today().isoformat()}.json",
                           mime="application/json")
        all_df = pd.DataFrame([{
            "Company": p.company, "Role": p.role, "Type": p.job_type,
            "Automation %": p.automation_score, "Manual %": p.manual_score,
            "Experience": p.experience, "Location": p.location, "HR Email": p.hr_email,
            "Skills in posting": ", ".join(p.skills),
        } for p in postings])
        st.dataframe(all_df, hide_index=True, width="stretch")


@st.cache_data(show_spinner=False, max_entries=10)
def load_postings(pdf_bytes: bytes, ignored: frozenset[str]) -> dict:
    extraction = extract_pdf_text(pdf_bytes)
    return {
        "pages": len(extraction.pages),
        "failed_pages": extraction.failed_pages,
        "empty_pages": extraction.empty_pages,
        "postings": parse_postings(extraction, set(ignored)),
    }


def main() -> None:
    st.title("🎯 QA Job Circular Matcher")
    st.caption("Upload the daily circular to rank its postings against your Automation and Manual QA resumes. "
               "Runs fully offline - no API key needed.")
    settings = sidebar_settings()
    tab_match, tab_resumes = st.tabs(["🔍 Match Jobs", "📄 Resumes"])
    with tab_match:
        matcher_page(settings)
    with tab_resumes:
        resumes_page()


if __name__ == "__main__":
    main()
