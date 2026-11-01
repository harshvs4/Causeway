"""Re-check the quoted clauses against MAS's own published document.

    make verify-regulatory

The corpus in data/regulatory/ claims to quote a public MAS notice verbatim.
This is how that claim gets checked rather than trusted: it downloads the PDF
from the URL the corpus itself records, extracts the text, and asserts every
quoted clause appears in it character for character.

It needs the network and `pdftotext`, so it is not part of `make test` - a
reproducibility check that fails on a plane is a bad test. It is a check anybody
can run, which is the whole point. The alternative is a repository asserting it
quoted a regulator correctly and offering no way to find out.

Two normalisations are applied to the extracted text before matching, and both
are artefacts of the PDF rather than licence to edit the regulator's words:

  - Page headers. The extractor interleaves "Notice on Recommendations On
    Investment Products <n>" into the middle of sentences straddling a page
    break. Paragraph 11(h) is split exactly that way.
  - Footnote markers. The source renders "...rely on the recommendation1",
    where the 1 is a footnote reference and not part of the sentence. Every
    such elision is disclosed in the corpus beside the clause it affects.

Anything else failing to match is a transcription error, and the exit code says
so.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from engine.regulatory import CORPUS_PATH, load_corpus

USER_AGENT = "Mozilla/5.0 (compatible; Anchorline/1.0)"
TIMEOUT_SECONDS = 60

_PAGE_HEADER = re.compile(
    r"Notice on Recommendations On Investment Products\s+\d+", re.IGNORECASE
)
# A digit glued to the end of a word, immediately before punctuation or a space:
# a footnote reference, never part of the sentence.
_FOOTNOTE_MARKER = re.compile(r"(?<=[a-z])\d(?=[.,;\s])")


def normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = _PAGE_HEADER.sub(" ", text)
    text = _FOOTNOTE_MARKER.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_pdf(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        destination.write_bytes(response.read())


def extract_text(pdf: Path) -> str:
    if not shutil.which("pdftotext"):
        raise RuntimeError(
            "pdftotext not found. Install poppler (brew install poppler). This "
            "script shells out deliberately rather than adding a PDF dependency "
            "to a project whose engine is pandas and nothing else."
        )
    out = pdf.with_suffix(".txt")
    subprocess.run(
        ["pdftotext", "-layout", str(pdf), str(out)],
        check=True, capture_output=True,
    )
    return out.read_text()


def main() -> int:
    corpus = load_corpus()
    url = corpus["source_url"]
    print(f"corpus   {CORPUS_PATH}")
    print(f"notice   {corpus['notice']} - {corpus['title']}")
    print(f"recorded retrieved {corpus['retrieved']}, issued {corpus['issue_date']}")
    print(f"fetching {url}\n")

    with tempfile.TemporaryDirectory() as workspace:
        pdf = Path(workspace) / "notice.pdf"
        try:
            fetch_pdf(url, pdf)
        except Exception as exc:                        # noqa: BLE001 - reported
            print(f"FAILED to fetch: {exc}")
            return 2
        try:
            source = normalise(extract_text(pdf))
        except Exception as exc:                        # noqa: BLE001 - reported
            print(f"FAILED to extract: {exc}")
            return 2

    mismatches: list[tuple[str, str]] = []
    checked = 0
    for clause_id, clause in corpus["clauses"].items():
        for field in ("text", "preamble"):
            if field not in clause:
                continue
            checked += 1
            if normalise(str(clause[field])) not in source:
                mismatches.append((f"{clause_id}.{field}", str(clause[field])[:100]))

    # The provenance line itself: the easiest thing to get wrong and the most
    # misleading when it is. A search summary consulted while building the
    # corpus said this Notice was issued under section 58; it is section 67.
    if normalise(corpus["issued_under"]) not in source:
        mismatches.append(("issued_under", corpus["issued_under"]))

    print(f"checked  {checked} quoted passage(s) + the enabling provision")
    if mismatches:
        print(f"\nMISMATCHES: {len(mismatches)}")
        for where, snippet in mismatches:
            print(f"  {where}\n    {snippet}...")
        print("\nA mismatch is a transcription error, or the Notice was amended.")
        print("Read the document, fix the corpus, and update `retrieved`.")
        return 1

    print("\nevery quoted passage appears verbatim in the published Notice")
    return 0


if __name__ == "__main__":
    sys.exit(main())
