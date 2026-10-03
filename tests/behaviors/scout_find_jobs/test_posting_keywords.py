"""0110.7 D-P2: the deterministic posting-keyword extractor (resume Skills tags + shipped alias table + title words).

Synthetic postings only.  Must/nice come from the sentence cue; matching is on word boundaries; aliases fold
to the table's name (k8s -> Kubernetes, golang -> Go); ``Go`` is never found inside ``Google`` or ``good``.
"""

from __future__ import annotations

from gigai.scout.posting_keywords import PostingKeywords, aliases_for, extract_keywords, mentions, skills_from_markdown, title_words

POSTING = """Senior Platform Engineer at Example Corp

About us: We use Google Cloud and have a good culture.

Requirements:
- You have run k8s clusters in production.
- Strong Terraform and AWS experience is required.
- Experience with golang.

Nice to have:
- Prometheus
- Experience with Datadog is a plus.
"""


def test_must_and_nice_lists_are_exact() -> None:
    kw = extract_keywords(POSTING, title="Senior Platform Engineer")
    assert kw == PostingKeywords(
        must=("GCP", "Kubernetes", "Terraform", "AWS", "Go"),
        nice=("Prometheus", "Datadog"),
        title=("senior", "platform", "engineer"),
    ), kw


def test_aliases_fold_to_the_table_name() -> None:
    kw = extract_keywords("You must know k8s and golang.")
    assert kw.must == ("Kubernetes", "Go")
    assert "k8s" in aliases_for("Kubernetes") and "golang" in aliases_for("go")  # case-insensitive lookup of the term
    assert mentions("ran EKS clusters", "Kubernetes") and mentions("wrote Golang services", "Go")


def test_go_is_not_matched_inside_other_words() -> None:
    for text in ("Google Cloud is good, let's go.", "We are going to Gojira with Mongo"):
        assert not mentions(text, "Go"), text
        assert "Go" not in extract_keywords(text).must + extract_keywords(text).nice, text
    assert mentions("Python and Go services", "Go") and mentions("Go, Rust", "Go")


def test_unreadable_word_boundaries_for_symbol_terms() -> None:
    assert mentions("We write C++ and C#.", "C++") and mentions("We write C++ and C#.", "C#")
    assert not mentions("Javascript frameworks", "Java")  # no partial-word hit
    assert mentions("CI/CD pipelines", "CI/CD") and mentions("GitHub Actions workflows", "CI/CD")


def test_resume_skill_tags_extend_the_vocabulary() -> None:
    kw = extract_keywords("Experience with Bazel and Fluent Bit is required. A plus: Temporal.", skills=["Fluent Bit", "Temporal", "K8s"])
    assert kw.must == ("Bazel", "Fluent Bit") and kw.nice == ("Temporal",)
    md = "# Jo\n\n## Skills\nPython · Fluent Bit · Go\n"
    assert skills_from_markdown(md) == ("Python", "Fluent Bit", "Go")
    assert skills_from_markdown("not a resume") == ()


def test_required_beats_a_nice_mention_of_the_same_term() -> None:
    kw = extract_keywords("Terraform is a plus. Terraform is required.")
    assert kw.must == ("Terraform",) and kw.nice == ()


def test_title_words_drop_filler() -> None:
    assert title_words("Staff Engineer, Platform and Reliability (II)") == ("staff", "engineer", "platform", "reliability")
