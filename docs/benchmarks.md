# Local-model benchmarks

All numbers come from real job postings on one machine (MacBook Pro M1 Pro, 32 GB). Labels were reviewed
from quotes in the posting text and from independent Claude scoring; no model graded its own output.
Posting-level data stays private, and only aggregates are published here.

## Metrics that matter

The pipeline must **never reject a suitable job** (false negative). Keeping an unsuitable job (false positive)
only costs Claude tokens, because Claude reviews everything that is not rejected.

## 1. Rule development (qwen3.5:35b, 32 postings)

| Run | Suitable jobs rejected | Unsuitable kept as relevant | Eligibility accuracy | Valid JSON | Median time |
|---|---|---|---|---|---|
| First rules | **4 of 15 (27%)** | 0 | 52% | 97% | 16.7 s |
| After fixing the rules | 0 | 0 | 94% | 100% | 13.4 s |

The first run failed for three reasons:
- **Programming languages counted as spoken languages.** "Java, JavaScript or C#" was read as a language requirement.
- **Quotes without a restriction treated as ineligibility.** "join our Alicante team" and "3+ years" were both accepted as eligibility restrictions.
- **Jobs in the candidate's own country left as `unknown`.**

All three were rule bugs, not model bugs. They were fixed in the deterministic layer. Because the second
run used the same postings the rules were tuned on, it overstates real accuracy, so a second, unseen test
followed.

## 2. Model choice on 47 unseen postings (9B vs 35B)

None of these postings had been used for tuning. Of the 47, 14 were suitable, 10 had a genuine hard reason,
and the rest were lower-fit jobs without a hard reason.

| Metric | qwen3.5:9b | qwen3.5:35b |
|---|---|---|
| Valid JSON | 100% | 100% |
| Suitable jobs rejected | **0 of 14** | **0 of 14** |
| Unsuitable jobs kept as relevant | 0 | 0 |
| Hard cases caught | 7 of 10 | 7 of 10 (same) |
| Unjustified rejections | 1 (rule bug, fixed since) | 1 (same) |
| Eligibility accuracy | 90.9% | 90.9% |
| Role-category accuracy | 76% | 96% |
| Tech-keyword recall | 95.6% | 96.4% |
| Extracted skills present in the posting | 99.4% | 99.2% |
| Unverifiable quotes discarded | 16 | 11 |
| Median / worst time per posting | 18.3 s / 31.6 s | 14.2 s / 23.4 s |
| Resident model size | **7.8 GB** | 22.1 GB |
| Lowest free memory during the run | **28%** | 7% |
| Swap during the run | no growth | 1.5 → 9.9 GB |

**Decision:** 9B. Its keep/reject decisions were the same as 35B's, and it leaves the Mac usable. 35B is
faster per posting (a mixture-of-experts model with about 3B active parameters) but pushes the machine
into heavy swap.

Two changes followed from 9B's weaker category accuracy:
- A wrong category can no longer cause a rejection. "Not software" also needs a title without tech terms
  *and* a posting body with no software signals.
- Fit scores barely correlated with Claude's (Spearman 0.17 for 9B, 0.04 for 35B), so they are not used
  for ranking.

## 3. Ongoing production monitoring

`local_screen.py` logs every decision with its reasons, category, discarded quotes, time, free memory and
swap. `local_audit.py` compares sampled rejections with Claude's scores, and `local_report.py --days 7`
summarises both. Two audited false negatives within 14 days switch local screening off automatically.
