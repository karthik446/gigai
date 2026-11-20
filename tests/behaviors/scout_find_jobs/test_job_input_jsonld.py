"""0.3.4 (gigai-9j8): a script-only job page is read for the schema.org ``JobPosting`` it carries.

A job board rendered by JavaScript serves a page whose visible text is a
"please enable JavaScript" line, but ships the posting for search engines as
a ``<script type="application/ld+json">`` ``JobPosting`` (or, on a Next.js
page, the same object inside the React Flight chunks the client script
builds that tag from).  ``resolve_job`` reads it after every board path has
had its turn and before giving up: ``fetch_kind="jsonld"``, the description
through ``html_to_text``, title/company/location from the object.  Every
network call goes through ``httpx.MockTransport``; the pages are synthetic.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessmentBody
from gigai.scout.find_jobs.contracts import FindJobsContractError, Verdict, normalize_url
from gigai.scout.find_jobs import job_input
from gigai.scout.find_jobs.job_input import MIN_POSTING_TEXT_CHARS, resolve_job
from gigai.scout.find_jobs.job_source import ATS_FETCH_KINDS, stored_ats_posting
from gigai.scout.quick_assess import list_quick_assessments, posting_requirements_unreadable, run_quick_assessment

_URL = "https://jobs.example.com/view/abc123XYZ/staff-platform-engineer-at-example-robotics?utm_source=feed"
_PAGE_TITLE = "Staff Platform Engineer - Example Robotics | Jobs by Example"

_DESCRIPTION_HTML = (
    "<p>Example Robotics builds fleet software for warehouse robots and is hiring a Staff Platform Engineer "
    "to own the services every robot talks to.</p>"
    "<h3>What you will do</h3>"
    "<ul><li>Design and operate the gRPC services that schedule, update and monitor a fleet of several thousand robots.</li>"
    "<li>Own reliability for those services: tracing, alerting and the on-call rotation for what you build.</li>"
    "<li>Review designs across the platform group and mentor engineers joining it.</li></ul>"
    "<h3>Requirements</h3>"
    "<ul><li>Seven or more years building production backend systems in Go or Python.</li>"
    "<li>Depth in PostgreSQL and message queues; comfort debugging distributed systems under load.</li>"
    "<li>Clear written communication with hardware and operations teams.</li></ul>"
    "<p>Example Robotics is unable to sponsor work visas for this position.</p>"
)
_SHORT_DESCRIPTION_HTML = "<p>Example Robotics is hiring a Staff Platform Engineer. Apply on our site.</p>"


def _posting(**overrides: object) -> dict:
    posting: dict = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Staff Platform Engineer",
        "description": _DESCRIPTION_HTML,
        "datePosted": "2026-11-01T00:00:00Z",
        "employmentType": "FULL_TIME",
        "hiringOrganization": {"@type": "Organization", "name": "Example Robotics", "url": "https://example.com"},
        "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Denver", "addressRegion": "CO", "addressCountry": "US"}},
        "applicantLocationRequirements": {"@type": "Country", "name": "United States"},
        "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"@type": "QuantitativeValue", "minValue": 180000, "maxValue": 210000, "unitText": "YEAR"}},
    }
    posting.update(overrides)
    return posting


def _script_only_page(*blocks: str, title: str = _PAGE_TITLE, body: str = "") -> str:
    """A Workable-shaped script-only page: ld+json ``blocks`` in the head, a ``<noscript>`` line and an empty root."""

    head = "".join(f'<script type="application/ld+json">{block}</script>' for block in blocks)
    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{title}</title>{head}"
        '<script src="/static/js/app.js"></script></head>'
        f'<body><noscript>You need to enable JavaScript to run this app.</noscript><div id="root"></div>{body}</body></html>'
    )


def _flight_page(*literals: str) -> str:
    """A Next.js-shaped page: the posting only inside ``self.__next_f.push`` string chunks, no ld+json tag."""

    pushes = "".join(f"<script>self.__next_f.push([1,{literal}])</script>" for literal in literals)
    return (
        f"<!doctype html><html><head><title>{_PAGE_TITLE}</title></head><body><div id=\"__next\"></div>"
        '<script src="/_next/static/chunks/main.js" async=""></script>'
        f'<script>self.__next_f=self.__next_f||[]</script>{pushes}</body></html>'
    )


class _Site:
    """A recording MockTransport serving ONE page at ``_URL``."""

    def __init__(self, page: str) -> None:
        self.page = page
        self.requests: list[str] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=True, max_redirects=5)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.host}{request.url.path}")
        return httpx.Response(200, text=self.page, headers={"content-type": "text/html; charset=utf-8"}, request=request)


def _resolve(page: str, url: str = _URL):
    site = _Site(page)
    with site.client() as client:
        resolved = resolve_job(AssessJobInput(job_url=url), client=client)
    assert site.requests == [f"GET {httpx.URL(url).host}{httpx.URL(url).path}"]  # the one page fetch, nothing else
    return resolved


def _unavailable(page: str) -> FindJobsContractError:
    site = _Site(page)
    with site.client() as client, pytest.raises(FindJobsContractError) as excinfo:
        resolve_job(AssessJobInput(job_url=_URL), client=client)
    assert site.requests == [f"GET {httpx.URL(_URL).host}{httpx.URL(_URL).path}"]
    return excinfo.value


_TODAYS_MESSAGE = (
    "the page at this URL carries no posting text (a script-only page, or a posting that is no longer listed); "
    "pass --job-text with the posting text instead"
)


# --- the happy path --------------------------------------------------------------------------------


def test_script_only_page_with_a_jobposting_resolves_as_jsonld() -> None:
    resolved = _resolve(_script_only_page(json.dumps(_posting())))

    assert resolved.fetch_kind == "jsonld"
    assert (resolved.title, resolved.company, resolved.location) == ("Staff Platform Engineer", "Example Robotics", "Denver, CO, US, United States")
    assert len(resolved.text) >= MIN_POSTING_TEXT_CHARS
    assert "Design and operate the gRPC services" in resolved.text and "<" not in resolved.text
    assert resolved.text.endswith("Salary: 180000 - 210000 USD per year")
    assert resolved.job_identity == resolved.normalized_url == normalize_url(_URL)  # the URL rules of today
    assert resolved.source_url == _URL
    assert resolved.text_sha256 == digest_imported_bytes(resolved.text.encode("utf-8"))


def test_nothing_from_the_object_is_ever_an_address() -> None:
    """Safety: URL-shaped fields in the object are text at most; no second request is made for any of them."""

    page = _script_only_page(json.dumps(_posting(url="https://evil.example.net/x", hiringOrganization={"name": "Example Robotics", "url": "https://evil.example.net/org"})))
    resolved = _resolve(page)  # ``_resolve`` asserts the single request
    assert resolved.company == "Example Robotics"


# --- where the object sits ------------------------------------------------------------------------


def test_jobposting_inside_graph_is_found() -> None:
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "Example Robotics"}, _posting()]}
    resolved = _resolve(_script_only_page(json.dumps(graph)))
    assert resolved.fetch_kind == "jsonld" and resolved.title == "Staff Platform Engineer"


def test_jobposting_inside_a_top_level_list_is_found() -> None:
    resolved = _resolve(_script_only_page(json.dumps([{"@type": "WebSite", "name": "Jobs"}, _posting()])))
    assert resolved.fetch_kind == "jsonld" and resolved.company == "Example Robotics"


def test_type_given_as_a_list_counts() -> None:
    resolved = _resolve(_script_only_page(json.dumps(_posting(**{"@type": ["Thing", "JobPosting"]}))))
    assert resolved.fetch_kind == "jsonld"


def test_the_first_block_not_being_a_jobposting_is_skipped() -> None:
    crumbs = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": []})
    resolved = _resolve(_script_only_page(crumbs, json.dumps(_posting())))
    assert resolved.fetch_kind == "jsonld" and resolved.title == "Staff Platform Engineer"


def test_type_attribute_case_other_attributes_and_wrappers_are_tolerated() -> None:
    block = json.dumps(_posting())
    for script in (
        f'<script id="jp" type=\'APPLICATION/LD+JSON\' data-x="1">{block}</script>',
        f'<script type=application/ld+json>{block}</script >',
        f'<script type="application/ld+json"><!--\n{block}\n--></script>',
        f'<script type="application/ld+json">//<![CDATA[\n{block}\n//]]></script>',
    ):
        page = _script_only_page().replace("</head>", f"{script}</head>")
        assert _resolve(page).fetch_kind == "jsonld", script[:40]


# --- the description's shapes ------------------------------------------------------------------------


def test_entity_escaped_description_html_is_decoded_like_a_boards() -> None:
    escaped = _DESCRIPTION_HTML.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    resolved = _resolve(_script_only_page(json.dumps(_posting(description=escaped))))
    assert resolved.fetch_kind == "jsonld"
    assert "Design and operate the gRPC services" in resolved.text and "&lt;" not in resolved.text and "<" not in resolved.text


def test_plain_text_description_is_kept_as_is() -> None:
    plain = "Example Robotics is hiring.\n\n" + ("We build fleet software for warehouse robots and need a platform engineer. " * 8)
    resolved = _resolve(_script_only_page(json.dumps(_posting(description=plain))))
    assert resolved.fetch_kind == "jsonld" and resolved.text.startswith("Example Robotics is hiring.")


def test_a_description_under_the_minimum_is_still_job_text_unavailable() -> None:
    error = _unavailable(_script_only_page(json.dumps(_posting(description=_SHORT_DESCRIPTION_HTML))))
    assert error.code == "job_text_unavailable" and str(error) == _TODAYS_MESSAGE


# --- malformed and wrong-typed objects ----------------------------------------------------------------


def test_malformed_json_falls_through_to_todays_error() -> None:
    error = _unavailable(_script_only_page('{"@type": "JobPosting", "title": '))
    assert error.code == "job_text_unavailable" and str(error) == _TODAYS_MESSAGE


def test_malformed_first_block_then_a_valid_second_block() -> None:
    resolved = _resolve(_script_only_page('{"@type": "JobPosting", "title": ', json.dumps(_posting())))
    assert resolved.fetch_kind == "jsonld" and resolved.title == "Staff Platform Engineer"


@pytest.mark.parametrize(
    "overrides, expected_company, expected_location",
    [
        ({"hiringOrganization": 42, "jobLocation": "Denver", "applicantLocationRequirements": None}, "", "Denver"),
        ({"hiringOrganization": "Example Robotics", "jobLocation": [{"address": {"addressLocality": "Denver", "addressRegion": 7}}, {"address": "Boulder, CO"}, None, 3]}, "Example Robotics", "Denver, Boulder, CO, United States"),
        ({"hiringOrganization": {"name": ["x"]}, "jobLocation": {"@type": "Place"}, "applicantLocationRequirements": [{"name": "Canada"}, {"name": None}, "Mexico"]}, "", "Canada, Mexico"),
        ({"title": None, "hiringOrganization": None, "jobLocation": None, "applicantLocationRequirements": {"name": 5}, "baseSalary": "a lot"}, "", ""),
    ],
)
def test_wrong_types_in_org_and_location_fields_are_skipped_never_raised(overrides: dict, expected_company: str, expected_location: str) -> None:
    resolved = _resolve(_script_only_page(json.dumps(_posting(**overrides))))
    assert resolved.fetch_kind == "jsonld"
    assert (resolved.company, resolved.location) == (expected_company, expected_location)
    assert resolved.title == ("Staff Platform Engineer" if "title" not in overrides else _PAGE_TITLE)  # the page's own title when the object has none
    if overrides.get("baseSalary") == "a lot":
        assert "Salary:" not in resolved.text


def _salary_line(base_salary: object) -> str:
    text = _resolve(_script_only_page(json.dumps(_posting(baseSalary=base_salary)))).text
    last = text.splitlines()[-1]
    return last if last.startswith("Salary:") else ""


def test_salary_line_only_from_plain_scalars() -> None:
    assert _salary_line({"currency": "EUR", "value": {"value": 95000, "unitText": "YEAR"}}) == "Salary: 95000 EUR per year"
    assert _salary_line({"currency": ["USD"], "value": {"minValue": "180k", "maxValue": True}}) == ""


def test_one_sided_salary_ranges_read_as_bounds() -> None:
    assert _salary_line({"currency": "USD", "value": {"minValue": 100000, "unitText": "YEAR"}}) == "Salary: from 100000 USD per year"
    assert _salary_line({"currency": "USD", "value": {"maxValue": 120000}}) == "Salary: up to 120000 USD"
    assert _salary_line({"currency": "USD", "value": {"value": 100000, "minValue": 90000}}) == "Salary: from 90000 USD"  # a range bound outranks a lone figure


@pytest.mark.parametrize(
    "amounts, expected",
    [
        ({"minValue": float("nan"), "maxValue": float("inf")}, ""),
        ({"minValue": -5, "maxValue": -1}, ""),
        ({"value": 0}, ""),
        ({"value": 1e16}, ""),
        ({"value": 10**40}, ""),
        ({"minValue": 10**9, "maxValue": 10**9 + 1}, "Salary: from 1000000000 USD"),  # the absurd bound alone is dropped
        ({"minValue": 0.1 + 0.2, "maxValue": 0.7, "unitText": "HOUR"}, "Salary: 0.3 - 0.7 USD per hour"),
        ({"minValue": 45.0, "maxValue": 52.5, "unitText": "HOUR"}, "Salary: 45 - 52.5 USD per hour"),
        ({"value": 123456.789}, "Salary: 123456.79 USD"),
        ({"value": True}, ""),
    ],
)
def test_salary_figures_are_finite_positive_bounded_and_rounded(amounts: dict, expected: str) -> None:
    # ``json.loads`` accepts NaN/Infinity, so a page can carry them; the line never does.
    assert _salary_line({"currency": "USD", "value": amounts}) == expected


def test_location_reads_country_objects_and_caps_the_list() -> None:
    country = {"@type": "Country", "name": "US"}
    posting = _posting(
        jobLocation=[{"address": {"addressLocality": "Denver", "addressRegion": "CO", "addressCountry": country}}, {"address": {"addressCountry": country}}],
        applicantLocationRequirements=[{"name": "US"}] + [{"name": f"Country {index}"} for index in range(12)] + [{"name": "Country 3"}],
    )
    resolved = _resolve(_script_only_page(json.dumps(posting)))
    assert resolved.location == "Denver, CO, US, US, " + ", ".join(f"Country {index}" for index in range(8)) + ", ..."
    # Exactly ten distinct parts: no ellipsis.
    ten = _resolve(_script_only_page(json.dumps(_posting(jobLocation=None, applicantLocationRequirements=[{"name": f"C{index}"} for index in range(10)]))))
    assert ten.location == ", ".join(f"C{index}" for index in range(10))


# --- hostile pages: never a raise, never a hang ---------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        "[" * 1000 + "]" * 1000,
        '{"@graph":' * 1000 + "{}" + "}" * 1000,
        "[" * 100_000 + "]" * 100_000,
        json.dumps(_posting())[:-1] + ', "identifier": ' + "[" * 100_000 + "]" * 100_000 + "}",
    ],
    ids=["list-1000-deep", "graph-1000-deep", "list-100000-deep", "field-100000-deep"],
)
def test_deep_nesting_falls_through_to_todays_error(block: str) -> None:
    error = _unavailable(_script_only_page(block))
    assert error.code == "job_text_unavailable" and str(error) == _TODAYS_MESSAGE


def _chain_page(tail: str, size: int = 2 * 1024 * 1024) -> str:
    """The reader's nested-chain Flight page: 20 type matches, each with 16 untried ``{`` whose parse would run to the
    end of an unterminated 2 MiB list — the case a decode count alone does not bound."""

    unit = '{"a":' * 16 + '{"@type":"JobPosting","b":'
    head = "<script>self.__next_f.push([1," + json.dumps(unit * 20 + "[")[:-1]
    room = size - len(head) - 20
    return head + tail * (room // len(tail)) + '"])</script>'


def test_hostile_pages_do_bounded_work_and_fall_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thousands of ld+json tags, unterminated tags, a 2 MiB whitespace block, a Flight chunk repeating the type, nested
    chains: at most ``_LD_JSON_BLOCKS`` blocks parsed, push literals decoded in one forward pass (never the same text
    twice), at most ``_FLIGHT_TYPE_MATCHES * _FLIGHT_OBJECT_STARTS`` object decodes that together consume at most the
    scan budget (plus the one decode that overran it); and a generous wall-clock bound against a hang."""

    loads: list[str] = []
    literal_starts: list[int] = []
    object_starts: list[int] = []
    object_chars: list[int] = []

    class _Counting:
        def __init__(self, page: str) -> None:
            self.page = page

        def raw_decode(self, text: str, index: int = 0):
            if text is self.page:
                literal_starts.append(index)
                return json.JSONDecoder().raw_decode(text, index)
            object_starts.append(index)
            try:
                node, end = json.JSONDecoder().raw_decode(text, index)
            except json.JSONDecodeError as exc:
                object_chars.append(exc.pos - index)
                raise
            object_chars.append(end - index)
            return node, end

    real_loads = json.loads

    def counting_loads(text: str, *args, **kwargs):
        loads.append(text)
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(job_input.json, "loads", counting_loads)
    two_mib = 2 * 1024 * 1024
    pages = {
        "tags": ('<script type="application/ld+json">{}</script>' * 45_000)[:two_mib],
        "unterminated": ('<script type="application/ld+json">' * 60_000)[:two_mib],
        "no-gt": ("<script type=" * 160_000)[:two_mib],
        "whitespace": '<script type="application/ld+json">' + " " * (two_mib - 50) + "</script>",
        "flight-dup-keys": "<script>self.__next_f.push([1," + json.dumps("1c:{" + ", ".join('"@type": "JobPosting"' for _ in range(20_000)) + "}") + "])</script>",
        "flight-unterminated": ('self.__next_f.push([1,"' * 90_000)[:two_mib],
        "chain-ints": _chain_page("1,"),
        "chain-floats": _chain_page("1.5,"),
        "chain-objects": _chain_page("{},"),
        "chain-strings": _chain_page('"a",'),
    }
    scan_budget = (job_input._FLIGHT_SCAN_FACTOR + 1) * two_mib + job_input._FLIGHT_SCAN_SLACK
    for name, page in pages.items():
        assert len(page) <= two_mib, name  # at the fetch cap, where the reader timed the old code in minutes
        monkeypatch.setattr(job_input, "_JSON_DECODER", _Counting(page))
        loads.clear(), literal_starts.clear(), object_starts.clear(), object_chars.clear()
        started = time.perf_counter()
        assert job_input._jsonld_job(page) is None, name  # (some of these pages are visible text to ``resolve_job``, so the reader is called directly)
        assert len(loads) <= job_input._LD_JSON_BLOCKS, name
        assert literal_starts == sorted(set(literal_starts)), name  # one forward pass
        assert len(object_starts) <= job_input._FLIGHT_TYPE_MATCHES * job_input._FLIGHT_OBJECT_STARTS, (name, len(object_starts))
        assert sum(object_chars) <= scan_budget, (name, sum(object_chars))  # bytes, not only decodes
        assert time.perf_counter() - started < 10.0, name


def test_flight_type_given_as_a_list_counts_too() -> None:
    posting = json.dumps(_posting()).replace('"@type": "JobPosting"', '"@type": ["Thing", "JobPosting"]')
    assert '"@type": ["Thing", "JobPosting"]' in posting
    resolved = _resolve(_flight_page(json.dumps("1c:" + posting)))
    assert resolved.fetch_kind == "jsonld" and resolved.title == "Staff Platform Engineer"


@pytest.mark.parametrize(
    "padding",
    [" " * 500, "\n" * 500, "&nbsp;" * 500, ("&nbsp;" * 5 + "&#8203;") * 100],
    ids=["spaces", "newlines", "nbsp-entities", "nbsp-and-zero-width-entities"],
)
def test_whitespace_padding_does_not_make_a_short_description_a_posting(padding: str) -> None:
    """The 400-char gate is on the collapsed text (a zero-width space is not whitespace to ``str.split`` and still
    counts, as on the generic path); the text returned for a real posting is still kept as-is."""

    short = "Example Robotics is hiring." + padding + "Apply on our site."
    error = _unavailable(_script_only_page(json.dumps(_posting(description=short))))
    assert error.code == "job_text_unavailable"
    kept = _resolve(_script_only_page(json.dumps(_posting(description=_DESCRIPTION_HTML.replace("</p>", "</p>" + padding, 1)))))
    assert kept.fetch_kind == "jsonld" and "\n" in kept.text


def _with_lone_surrogate(block: str) -> str:
    """The block with the marker swapped for the JSON escape of a lone high surrogate (what ``JSON.stringify`` emits
    for a string cut mid-emoji); ``json.loads`` yields a str Python cannot UTF-8-encode."""

    return block.replace("@@", "\\ud800")


def test_lone_surrogates_in_the_object_become_question_marks_never_an_encode_error() -> None:
    block = _with_lone_surrogate(json.dumps(_posting(
        title="Staff @@ Engineer", description=_DESCRIPTION_HTML + "<p>Ship it @@</p>",
        hiringOrganization={"name": "Example @@ Robotics"}, jobLocation={"address": {"addressLocality": "Den@@ver"}}, applicantLocationRequirements=None,
        baseSalary={"currency": "US@@", "value": {"value": 1000}},
    )))
    resolved = _resolve(_script_only_page(block))

    assert resolved.fetch_kind == "jsonld"
    assert (resolved.title, resolved.company, resolved.location) == ("Staff ? Engineer", "Example ? Robotics", "Den?ver")
    assert "Ship it ?" in resolved.text and resolved.text.endswith("Salary: 1000 US?")
    for value in (resolved.title, resolved.company, resolved.location, resolved.text):
        value.encode("utf-8")  # a normal posting: the digest and the store can take it
    assert resolved.text_sha256 == digest_imported_bytes(resolved.text.encode("utf-8"))


def test_lone_surrogate_in_a_flight_chunk_is_sanitised_too() -> None:
    posting = json.dumps(_posting(description=_DESCRIPTION_HTML + "<p>Ship it @@</p>", jobLocation=None, applicantLocationRequirements=None))
    literal = json.dumps("1c:" + posting).replace("@@", "\\\\ud800")  # the escape survives the literal's own encoding
    resolved = _resolve(_flight_page(literal))
    assert resolved.fetch_kind == "jsonld" and "Ship it ?" in resolved.text
    resolved.text.encode("utf-8")


def test_turkish_dotted_i_before_or_inside_the_tag_does_not_shift_the_block() -> None:
    """``str.lower`` turns ``İ`` into two code points: offsets from a lowercased copy would miss the block."""

    block = json.dumps(_posting())
    for page in (
        _script_only_page(block, title="İstanbul Ofisi - İş İlanı | Example"),
        _script_only_page().replace("</head>", f'<meta name="x" content="İİİ"><script data-x="İ" type="application/ld+json">{block}</script></head>'),
        _script_only_page().replace("</head>", f'<SCRIPT TYPE="Application/LD+JSON">{block}</ScRiPt></head>'),
    ):
        resolved = _resolve(page)
        assert resolved.fetch_kind == "jsonld" and resolved.title == "Staff Platform Engineer"


# --- unchanged paths ------------------------------------------------------------------------------------


def test_a_page_with_enough_visible_text_stays_generic_even_with_ld_json() -> None:
    page = _script_only_page(json.dumps(_posting()), body=f"<main><h1>Staff Platform Engineer</h1>{_DESCRIPTION_HTML}</main>")
    resolved = _resolve(page)
    assert resolved.fetch_kind == "generic"
    assert resolved.title == _PAGE_TITLE and resolved.company == "" and resolved.location == ""
    assert "Salary:" not in resolved.text


def test_no_ld_json_at_all_is_todays_error_byte_for_byte() -> None:
    error = _unavailable(_script_only_page())
    assert error.code == "job_text_unavailable" and str(error) == _TODAYS_MESSAGE
    # A JobPosting with no description at all is no posting either.
    error = _unavailable(_script_only_page(json.dumps(_posting(description=None))))
    assert error.code == "job_text_unavailable" and str(error) == _TODAYS_MESSAGE


# --- a Next.js page: the object inside the Flight chunks ---------------------------------------------------


def test_jobposting_in_next_flight_chunks_split_across_pushes_is_found() -> None:
    posting = json.dumps(_posting(jobLocation=None, applicantLocationRequirements=[{"@type": "Country", "name": "India"}, {"@type": "Country", "name": "Japan"}]))
    cut = len(posting) // 2
    flight_head = '1:HL["/_next/static/css/app.css","style"]\n' + f"1c:T{len(posting.encode('utf-8')):x},"
    page = _flight_page(
        json.dumps('0:["$","$L1",null,{"children":[]}]\n'),
        json.dumps("1a:bad"),
        json.dumps(flight_head + posting[:cut]),
        json.dumps(posting[cut:] + "\n"),
        json.dumps('1d:["$","$L2",null,{"id":"jobPosting-jsonld","type":"application/ld+json","children":"$1c"}]\n'),
    )
    resolved = _resolve(page)

    assert resolved.fetch_kind == "jsonld"
    assert (resolved.title, resolved.company, resolved.location) == ("Staff Platform Engineer", "Example Robotics", "India, Japan")
    assert "Design and operate the gRPC services" in resolved.text and "<" not in resolved.text


def test_flight_chunks_without_a_usable_jobposting_are_todays_error() -> None:
    for literals in (
        ('"0:[\\"$\\",\\"$L1\\"]\\n"',),  # no posting at all
        ('"1c:{\\"@type\\":\\"JobPosting\\",\\"title\\":"', '"\\"x\\""'),  # truncated object, never closed
        ("'not a json literal'",),  # a literal the regex does not read
        (json.dumps('1c:{"@type":"JobPosting","title":"x","description":"' + _SHORT_DESCRIPTION_HTML + '"}'),),  # too short
    ):
        error = _unavailable(_flight_page(*literals))
        assert error.code == "job_text_unavailable" and str(error) == _TODAYS_MESSAGE, literals


# --- fetch_kind="jsonld" through assess (no model, no provider) ------------------------------------------------


def test_jsonld_is_a_postings_own_text_for_the_requirement_gate() -> None:
    from tests.behaviors.scout_find_jobs.test_quick_assess import _NO_STATED

    assert "jsonld" in ATS_FETCH_KINDS
    body = AssessmentBody.from_json(json.loads(_NO_STATED) | {"suggestions": [], "questions": []})
    assert posting_requirements_unreadable("Short. Menu", body, "jsonld") is False  # like a board's text
    assert posting_requirements_unreadable("Short. Menu", body, "generic") is True


def test_jsonld_job_flows_through_assess_like_a_boards_posting(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The generic-only refusals do not fire; the verdict and the posting text are stored under the URL identity;
    a stored jsonld posting is the one a later generic refetch falls back to (``stored_ats_posting``)."""

    from tests.behaviors.scout_find_jobs.test_quick_assess import _GOOD_MATCH, _RESUME, _config_with_ollama, _install
    from tests.support.scout_profile_fixtures import build_gig_with_resume

    monkeypatch.delenv("JEV_API_KEY", raising=False)
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME)
    binding, _asked = _install(monkeypatch, [_GOOD_MATCH])
    # A bullets-only description: ``has_posting_body`` would refuse it as a generic page; a posting's own text is assessed.
    bullets = "\n".join(f"- Item {index}" for index in range(60))
    job = _resolve(_script_only_page(json.dumps(_posting(description=bullets))))
    assert job.fetch_kind == "jsonld"

    response = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL)), home_root=fx.home_root, target=fx.target,
        config=_config_with_ollama(fx.home_root), resolved_job=job,
    )

    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert len(binding.port.prompts) == 1 and bullets in binding.port.prompts[0]
    assert response.job.fetch_kind == "jsonld" and response.posting_text == job.text  # not pasted: the text is kept
    stored = list_quick_assessments(fx.home_root, fx.target)
    assert len(stored) == 1 and stored[0].job.fetch_kind == "jsonld" and stored[0].job.job_identity == normalize_url(_URL)
    fallback = stored_ats_posting(fx.home_root, fx.target, normalize_url(_URL))
    assert fallback is not None and fallback.fetch_kind == "jsonld" and fallback.text == job.text
