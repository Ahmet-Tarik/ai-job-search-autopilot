"""Regression tests for the deterministic screening rules (job_assess.decide). No model calls.

Qwen proposes facts (an Assessment); decide() must only reject with verified, quoted evidence of a
hard reason. A wrong category, an ambiguous title, a low fit score or a fabricated quote must never
reject a job on their own: those cases end as "uncertain" and go to Claude.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_assess as ja  # noqa: E402


def make(**kw):
    base = dict(company="Acme", position="Software Engineer", category="junior", location="Istanbul, Türkiye",
                work_mode="unclear", remote_from_turkey="unknown", eligibility="unknown", eligibility_evidence=[],
                years_required=None, experience_evidence=None, languages_required=[], language_evidence=[],
                required_skills=[], preferred_skills=[], matched_skills=[], missing_skills=[], fit_score=50, summary="x")
    base.update(kw)
    return ja.Assessment(**base)


BLUEPYTHON = ("BluePython, finansal uygulamalar geliştiren bir şirkettir. Üniversitelerin Bilgisayar Mühendisliği "
              "bölümlerinden mezun, 1–3 yıl deneyimli, Yapay zeka uygulamaları geliştirmiş ya da finansal uygulamalar "
              "alanında deneyim sahibi.")


# ---- 1. "Uzman" titles -------------------------------------------------------------------------------
def test_bluepython_uzman_title_is_not_rejected():
    a = make(position="Uzman Yazılım Mühendisi", category="mid_or_senior", fit_score=30,
             experience_evidence="1–3 yıl deneyimli", years_required=1)
    r = ja.decide(a, BLUEPYTHON, "Uzman Yazılım Mühendisi", "Istanbul, Türkiye")
    assert r["outcome"] != "irrelevant", r
    assert r["outcome"] == "uncertain" and r["needs_claude_review"]


def test_uzman_title_alone_never_establishes_seniority():
    for title in ("Uzman Yazılım Mühendisi", "Yazılım Uzmanı", "Uzman Yardımcısı - Yazılım", "Software Specialist"):
        r = ja.decide(make(fit_score=10), "Python ile backend geliştirme.", title, "Ankara, Türkiye")
        assert r["outcome"] != "irrelevant", (title, r)


def test_uzman_title_with_verified_years_still_uses_the_years():
    text = "Yazılım geliştirme süreçlerinde en az 5 yıl deneyimli adaylar arıyoruz."
    a = make(experience_evidence="en az 5 yıl deneyimli", years_required=5)
    r = ja.decide(a, text, "Uzman Yazılım Mühendisi", "İzmir, Türkiye")
    assert r["outcome"] == "irrelevant" and r["hard_reasons"][0].startswith("senior")


def test_head_or_architect_titles_are_not_hard_rejections():
    for title in ("Head of Data Intern Program", "Software Architect (Junior Track)", "Solutions Architect"):
        r = ja.decide(make(fit_score=20), "We build APIs in Python.", title, "Istanbul")
        assert r["outcome"] != "irrelevant", (title, r)


def test_explicit_senior_title_still_rejected_but_not_with_junior_words_or_low_verified_years():
    assert ja.decide(make(), "Python APIs.", "Senior Backend Engineer", "Istanbul")["outcome"] == "irrelevant"
    assert ja.decide(make(), "Python APIs.", "Junior Engineer - Senior Team", "Istanbul")["outcome"] != "irrelevant"
    a = make(experience_evidence="0-2 years of experience")
    assert ja.decide(a, "We want 0-2 years of experience in Python.", "Senior Associate Developer", "Istanbul")["outcome"] != "irrelevant"


# ---- 2. Category from Qwen never rejects alone -------------------------------------------------------
def test_wrong_not_software_category_cannot_reject_a_software_job():
    # the 9B model labelled .NET roles "not_software" in the benchmark
    text = "Minimum 1 year of .NET development experience, SQL and REST APIs."
    for title in ("Software Engineer", "Uzman", "Analyst", "Specialist", "Associate"):
        r = ja.decide(make(category="not_software", fit_score=0), text, title, "Istanbul, Türkiye")
        assert r["outcome"] != "irrelevant", (title, r)


def test_not_software_needs_title_and_body_without_software_signals():
    text = "Warehouse process improvement, Excel reporting, lean manufacturing and shift planning."
    r = ja.decide(make(category="not_software", fit_score=5), text, "Operations Specialist", "Ankara")
    assert r["outcome"] == "irrelevant"
    r2 = ja.decide(make(category="not_software", fit_score=5), text + " Basic SQL is a plus.", "Operations Specialist", "Ankara")
    assert r2["outcome"] == "uncertain"


def test_category_and_threshold_never_turn_into_rejection():
    for cat in ("internship", "graduate_program", "new_grad", "junior", "mid_or_senior", "not_software"):
        r = ja.decide(make(category=cat, fit_score=0), "Python developer role building APIs.", "Developer", "Ankara")
        assert r["outcome"] == "uncertain", (cat, r)


# ---- 3. Fit score is never authoritative --------------------------------------------------------------
def test_low_fit_score_alone_is_uncertain_not_irrelevant():
    r = ja.decide(make(fit_score=0), "Junior Python developer, remote.", "Junior Python Developer", "Turkey (remote)")
    assert r["outcome"] == "uncertain" and r["needs_claude_review"]


# ---- 4. Evidence validation ---------------------------------------------------------------------------
def test_fabricated_ineligibility_quote_is_dropped_and_not_a_rejection():
    a = make(eligibility="ineligible", eligibility_evidence=["Only US citizens may apply"])
    r = ja.decide(a, "Remote role, we hire across Europe.", "Backend Developer", "Europe")
    assert r["outcome"] != "irrelevant" and r["eligibility"] == "unknown" and r["evidence_dropped"] == 1


def test_missing_information_is_unknown_not_ineligible():
    r = ja.decide(make(eligibility="ineligible"), "Backend developer in Lisbon.", "Backend Developer", "Lisbon, Portugal")
    assert r["eligibility"] == "unknown" and r["outcome"] != "irrelevant"


def test_quote_without_a_restriction_is_not_ineligibility():
    text = "Be part of our Alicante team and help shape the future."
    a = make(eligibility="ineligible", eligibility_evidence=["Be part of our Alicante team"])
    assert ja.decide(a, text, "Junior Developer", "Alicante, Spain")["outcome"] != "irrelevant"


def test_verified_restriction_is_a_rejection():
    text = "We are only considering candidates who currently have a valid work permit for the Netherlands and do not require sponsorship."
    a = make(eligibility="ineligible", eligibility_evidence=["only considering candidates who currently have a valid work permit for the Netherlands"])
    r = ja.decide(a, text, "Junior Software Developer", "Rotterdam, Netherlands")
    assert r["outcome"] == "irrelevant" and r["eligibility"] == "ineligible"


def test_job_in_turkey_is_never_ineligible():
    text = "Only candidates residing in Istanbul, Ankara, İzmir and Bursa will be considered."
    a = make(eligibility="ineligible", eligibility_evidence=["Only candidates residing in Istanbul, Ankara, İzmir and Bursa"])
    r = ja.decide(a, text, "Software Engineer", "Istanbul, Türkiye")
    assert r["eligibility"] == "eligible" and r["outcome"] != "irrelevant"


def test_html_wrapped_experience_requirement_is_verified():
    text = "What You Will Need: <li>Have <strong>3+ years of software development experience</strong> with PHP.</li>"
    a = make(experience_evidence="Have 3+ years of software development experience")
    r = ja.decide(a, text, "Backend Software Developer (PHP/Laravel)", "Turkey (remote)")
    assert r["evidence_dropped"] == 0 and r["outcome"] == "irrelevant"


def test_experience_ranges_use_lower_bound():
    for q, yrs in (("0-3 years of experience", 0), ("2-4 yıl deneyim", 2), ("en az 2, en fazla 5 yıl", 0), ("3+ years", 3)):
        assert ja.min_years(q) == yrs, q
    a = make(experience_evidence="1–3 yıl deneyimli")
    assert ja.decide(a, BLUEPYTHON, "Yazılım Mühendisi", "Istanbul")["outcome"] != "irrelevant"


# ---- 5. Languages -------------------------------------------------------------------------------------
def test_programming_languages_are_not_language_requirements():
    text = "Good command of various programming languages (such as Java, JavaScript, or C#)."
    a = make(languages_required=["Java", "JavaScript", "C#"], language_evidence=[text])
    assert ja.decide(a, text, "Software Engineer, Backend (New Grad)", "Istanbul")["outcome"] != "irrelevant"


def test_english_requirement_is_not_a_rejection():
    text = "İyi derecede yazılı ve sözlü İngilizce."
    a = make(languages_required=["İngilizce"], language_evidence=["İyi derecede yazılı ve sözlü İngilizce"])
    assert ja.decide(a, text, "Yazılım Mühendisi", "Ankara")["outcome"] != "irrelevant"


def test_verified_undeclared_language_is_a_rejection():
    text = "Required languages English B1 - Intermediate Ukrainian Native"
    a = make(languages_required=["Ukrainian"], language_evidence=["Ukrainian Native"])
    assert ja.decide(a, text, "Junior Backend Developer", "Remote Worldwide")["outcome"] == "irrelevant"
