"""Category + urgency classification for all monitored email (deterministic, EN/TR).

Categories: job, work, payment, account, opportunity, irrelevant.
Urgency:
  urgent        -> Telegram alert now (interviews, assessments, offers, info requests,
                   deadlines, new assignments, payment problems, account-access issues)
  important     -> daily briefing (project updates, contract changes, announcements,
                   rejections, onboarding, invoices, new opportunities from known contacts)
  informational -> logged only (confirmations, routine notices, payments received)
  needs_review  -> briefing, flagged "unverified" (unknown senders with a credible job,
                   internship, freelance, recruitment or project message, or demanding urgent
                   action); never alerted, never trusted, never acted on
  irrelevant    -> ignored (promotions, advertising, newsletters, spam, unrelated personal mail)

Job-application logic (gmail_monitor.CLASSES) is reused unchanged for the job category.
"""
from __future__ import annotations

import re

import gmail_monitor as gm
from common import config

PROMO_SENDERS = ("newsletter@", "marketing@", "news@", "promo", "offers@", "deals@",
                 "jobalerts-noreply@linkedin.com")
PROMO_TEXT = re.compile(r"% ?off|\bsale\b|black friday|discount|coupon|limited time|indirim|kampanya|"
                        r"subscribe|webinar|summit|register now|kayıt ol", re.I)

PAYMENT = re.compile(r"payment|payout|paid|invoice|remittance|fatura|ödeme|hakediş|w-?8ben|tax form|"
                     r"bank details|iban", re.I)
PAYMENT_PROBLEM = re.compile(r"fail|declin|reject|on hold|delay|unable|could not|couldn't|missing|issue|problem|"
                             r"action required|update your (bank|payment)|başarısız|sorun|gecik", re.I)
PAYMENT_DONE = re.compile(r"(has been|was) (paid|sent|processed)|payment (sent|received|processed|completed)|"
                          r"you('ve| have) been paid|ödemeniz (yapıldı|gönderildi)", re.I)
ACCOUNT = re.compile(r"account|password|log ?in|sign[- ]?in|access|verify|verification|2fa|two-factor|"
                     r"security alert|hesab|şifre|giriş", re.I)
ACCOUNT_PROBLEM = re.compile(r"suspend|deactivat|disabled|locked|restricted|unable to (access|log)|lost access|"
                             r"action required|verify your (identity|account)|unusual (activity|sign)|"
                             r"askıya|kilitlen|erişim(iniz)? (kısıt|engel)", re.I)
CONTRACT = re.compile(r"contract|agreement|terms of (service|engagement)|\bnda\b|amendment|sözleşme", re.I)
CONTRACT_SIGN = re.compile(r"sign (by|before|the)|signature required|please sign|e-?sign|imzala", re.I)
ASSIGNMENT = re.compile(r"new (task|assignment|project|batch)s? (is |are )?(available|assigned)|you('ve| have) been "
                        r"(assigned|selected|added)|assigned to you|invited to (a |the )?(project|task)|"
                        r"yeni (görev|proje)", re.I)
DEADLINE = re.compile(r"deadline|due (by|on|date)|by (eod|end of day|tomorrow)|expires?|son tarih|"
                      r"action required|immediate(ly)? action|urgent|acil", re.I)
ONBOARDING = re.compile(r"onboarding|welcome to|getting started|orientation|training (module|required)|"
                        r"complete your (profile|setup)|başlangıç", re.I)
UPDATE = re.compile(r"update|announcement|change|transition|moving to|policy|reminder|new process|"
                    r"güncelleme|duyuru|değişiklik", re.I)
OPPORTUNITY = re.compile(r"opportunit|new project|apply (now|to|for)|we think you|great fit|invite you to join|"
                         r"get paid to|fırsat", re.I)
CONFIRM = re.compile(r"confirm(ed|ation)|received|thank(s| you)|receipt|onay", re.I)


# --- unknown senders ------------------------------------------------------------
# Email text is only pattern-matched here; nothing in an email is ever followed,
# opened, answered or trusted. Claims of urgency from unknown senders are flagged
# for the candidate, never acted on.
SPAM = re.compile(r"lottery|you('ve| have) won|winner|prize|jackpot|casino|bet(ting)?\b|crypto|bitcoin|forex|"
                  r"investment opportunit|guaranteed (income|return)|loan|kredi|bahis|çekiliş|kazandınız|"
                  r"gift ?card|hediye çeki|make \$?\d+.{0,20}(per|a) (day|week)|work from home and earn|"
                  r"no experience needed.{0,30}(earn|\$)|dating|viagra", re.I)
OPPORTUNITY_SIGNAL = re.compile(r"recruit|hiring|we are hiring|position|job (opening|offer|opportunit)|role\b|"
                                r"vacanc|internship|intern\b|staj|freelance|contract(or)? (role|position|work)|"
                                r"project (invitation|opportunit|proposal)|your (cv|resume|profile|background)|"
                                r"candidate|interview|iş (ilanı|fırsatı|teklifi)|pozisyon|işe alım|aday|mülakat|"
                                r"başvuru|serbest|proje (teklifi|daveti)", re.I)
URGENT_CLAIM = re.compile(r"urgent|asap|immediately|right away|within \d+ ?(hours|hrs|minutes)|today only|"
                          r"action required|final (notice|warning)|last chance|act now|verify your|confirm your "
                          r"(identity|account|details)|click (here|the link|below)|log ?in to|password|"
                          r"wire transfer|bank (details|account)|pay (a|the) fee|processing fee|acil|hemen|derhal", re.I)


def _unknown_sender(text: str, promo: bool) -> dict:
    """Unknown sender: spam/promo/personal -> ignore; credible opportunity -> needs_review;
    urgent demands -> needs_review with a suspicious flag (never trusted, never alerted)."""
    if promo or SPAM.search(text) or PROMO_TEXT.search(text):
        return {"category": "irrelevant", "kind": "promotion_or_spam", "urgency": "irrelevant", "contact": None}
    opportunity = OPPORTUNITY_SIGNAL.search(text)
    urgent_claim = URGENT_CLAIM.search(text)
    if urgent_claim:
        return {"category": "unknown", "kind": "unverified_urgent_request", "urgency": "needs_review",
                "contact": None, "flag": "unknown sender claims urgency - verify before acting"}
    if opportunity:
        return {"category": "unknown", "kind": "unverified_opportunity", "urgency": "needs_review",
                "contact": None, "flag": "unknown sender - verify the company before replying"}
    return {"category": "irrelevant", "kind": "unrelated", "urgency": "irrelevant", "contact": None}


def _work_contact(blob: str) -> str | None:
    for c in config().get("email", {}).get("work_contacts", []):
        if any(m.lower() in blob for m in c["match"]):
            return c["name"]
    return None


def classify_full(sender: str, subject: str, snippet: str, labels: list[str] | None,
                  job_row: dict | None) -> dict:
    """Return {category, kind, urgency, contact}."""
    text = f"{subject} {snippet}"
    blob = f"{sender} {subject} {snippet}".lower()
    s = sender.lower()
    contact = _work_contact(blob)
    pay_sender = any(p in s for p in config().get("email", {}).get("payment_senders", []))
    promo = "CATEGORY_PROMOTIONS" in (labels or []) or any(x in s for x in PROMO_SENDERS)

    # Irrelevant first, unless it comes from a known work contact or a tracked employer.
    if promo and not contact and not job_row:
        return {"category": "irrelevant", "kind": "promotion", "urgency": "irrelevant", "contact": None}

    # Job applications: unchanged logic, mapped to the new urgency scale.
    ats = any(x in s for x in gm.ATS_SENDERS)
    if job_row or (ats and not contact):
        kind = gm.classify(text)
        urgency = ("urgent" if kind in {"interview_invitation", "assessment", "offer", "info_request", "needs_review"}
                   else "informational" if kind == "application_confirmation"
                   else "important")
        return {"category": "job", "kind": kind, "urgency": urgency, "contact": None}

    known = bool(contact or pay_sender)
    if not known:
        return _unknown_sender(text, promo)

    if PAYMENT.search(text) or pay_sender:
        if PAYMENT_PROBLEM.search(text) and not PAYMENT_DONE.search(text):
            return {"category": "payment", "kind": "payment_problem", "urgency": "urgent", "contact": contact}
        if PAYMENT_DONE.search(text):
            return {"category": "payment", "kind": "payment_received", "urgency": "informational", "contact": contact}
        return {"category": "payment", "kind": "invoice_or_payment_notice", "urgency": "important", "contact": contact}
    if ACCOUNT.search(text) and ACCOUNT_PROBLEM.search(text):
        return {"category": "account", "kind": "account_access_issue", "urgency": "urgent", "contact": contact}
    if CONTRACT.search(text):
        urgent = CONTRACT_SIGN.search(text) or DEADLINE.search(text)
        return {"category": "work", "kind": "contract_update", "urgency": "urgent" if urgent else "important",
                "contact": contact}
    if ASSIGNMENT.search(text):
        return {"category": "work", "kind": "new_assignment", "urgency": "urgent", "contact": contact}
    if DEADLINE.search(text):
        return {"category": "work", "kind": "deadline", "urgency": "urgent", "contact": contact}
    if ONBOARDING.search(text):
        return {"category": "work", "kind": "onboarding", "urgency": "important", "contact": contact}
    if OPPORTUNITY.search(text):
        return {"category": "opportunity", "kind": "project_opportunity", "urgency": "important", "contact": contact}
    if ACCOUNT.search(text):
        return {"category": "account", "kind": "account_notice", "urgency": "important", "contact": contact}
    if UPDATE.search(text):
        return {"category": "work", "kind": "project_update", "urgency": "important", "contact": contact}
    if promo or PROMO_TEXT.search(text):
        return {"category": "irrelevant", "kind": "promotion", "urgency": "irrelevant", "contact": contact}
    if CONFIRM.search(text):
        return {"category": "work", "kind": "confirmation", "urgency": "informational", "contact": contact}
    return {"category": "work", "kind": "work_message", "urgency": "important", "contact": contact}
