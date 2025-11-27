import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests

NCBI_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_EMAIL = "your_email@example.com"
DATE_RANGE = ("2015/01/01", "2025/11/11")
SAVE_ROOT = r"D:\\文献阅读\\文献收集\\植物耐寒C"
MIN_TOTAL = 100


@dataclass
class SearchPlan:
    name: str
    query: str
    category: str
    target_count: int


@dataclass
class Article:
    pmid: str
    title: str
    journal: str
    pub_date: str
    category: str
    doi: Optional[str] = None
    pmcid: Optional[str] = None
    pdf_url: Optional[str] = None
    source: str = "PubMed"
    notes: str = ""
    filename: Optional[str] = None


class PubMedClient:
    def __init__(self, email: str, api_key: Optional[str] = None, delay: float = 0.34) -> None:
        self.email = email
        self.api_key = api_key
        self.delay = delay

    def _request(self, path: str, params: Dict[str, str]) -> requests.Response:
        merged = {"email": self.email, **params}
        if self.api_key:
            merged["api_key"] = self.api_key
        time.sleep(self.delay)
        resp = requests.get(f"{NCBI_EUTILS_BASE}/{path}", params=merged, timeout=30)
        resp.raise_for_status()
        return resp

    def search(self, query: str, retmax: int = 200) -> List[str]:
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(retmax),
            "retmode": "json",
        }
        data = self._request("esearch.fcgi", params).json()
        return data.get("esearchresult", {}).get("idlist", [])

    def fetch_details(self, pmids: Iterable[str]) -> Dict[str, dict]:
        id_str = ",".join(pmids)
        params = {
            "db": "pubmed",
            "id": id_str,
            "retmode": "xml",
        }
        resp = self._request("efetch.fcgi", params)
        return {pmid: rec for pmid, rec in self._parse_xml(resp.text).items()}

    @staticmethod
    def _parse_xml(xml_text: str) -> Dict[str, dict]:
        try:
            import xml.etree.ElementTree as ET
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("xml library missing") from exc

        root = ET.fromstring(xml_text)
        records: Dict[str, dict] = {}
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//PMID")
            pmid = pmid_el.text if pmid_el is not None else ""
            title_el = article.find(".//ArticleTitle")
            title = title_el.text if title_el is not None else ""
            journal_el = article.find(".//Journal/Title")
            journal = journal_el.text if journal_el is not None else ""
            pub_date_el = article.find(".//PubDate")
            pub_date = "".join(pub_date_el.itertext()) if pub_date_el is not None else ""
            pmcid = None
            doi = None
            for aid in article.findall(".//ArticleId"):
                id_type = aid.attrib.get("IdType")
                if id_type == "pmc":
                    pmcid = aid.text
                elif id_type == "doi":
                    doi = aid.text
            records[pmid] = {
                "title": title,
                "journal": journal,
                "pub_date": pub_date,
                "pmcid": pmcid,
                "doi": doi,
            }
        return records


class Downloader:
    def __init__(self, client: PubMedClient, save_root: str = SAVE_ROOT) -> None:
        self.client = client
        self.save_root = save_root
        os.makedirs(self.save_root, exist_ok=True)
        self.session = requests.Session()

    def _safe_filename(self, article: Article) -> str:
        base = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", article.title)[:120]
        return f"{article.category}_{article.pmid}_{base}.pdf"

    def _pmc_pdf(self, pmcid: str) -> str:
        trimmed = pmcid.replace("PMC", "") if pmcid.upper().startswith("PMC") else pmcid
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{trimmed}/pdf"

    def _crossref_pdf(self, doi: str) -> Optional[Tuple[str, str]]:
        url = f"https://api.crossref.org/works/{doi}"
        try:
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json().get("message", {})
            license_urls = [lic.get("URL") for lic in data.get("license", []) if lic.get("URL")]
            for link in data.get("link", []):
                if link.get("content-type") == "application/pdf" and link.get("URL"):
                    if license_urls or link.get("content-version") == "vor":
                        return link["URL"], ";".join(license_urls)
        except Exception:
            return None
        return None

    def download_article(self, article: Article) -> Article:
        filename = self._safe_filename(article)
        filepath = os.path.join(self.save_root, filename)
        if os.path.exists(filepath):
            article.filename = filepath
            return article

        pdf_url = None
        if article.pmcid:
            pdf_url = self._pmc_pdf(article.pmcid)
        elif article.doi:
            crossref_result = self._crossref_pdf(article.doi)
            if crossref_result:
                pdf_url, license_info = crossref_result
                article.notes = f"CrossRef license: {license_info}"

        if not pdf_url:
            article.notes = article.notes or "No open PDF link found"
            return article

        try:
            with self.session.get(pdf_url, timeout=40) as resp:
                if resp.status_code == 200 and "pdf" in resp.headers.get("Content-Type", "").lower():
                    with open(filepath, "wb") as fh:
                        fh.write(resp.content)
                    article.pdf_url = pdf_url
                    article.filename = filepath
                else:
                    article.notes = f"PDF download failed: HTTP {resp.status_code}"
        except Exception as exc:  # pragma: no cover
            article.notes = f"Download error: {exc}"
        return article


SEARCH_PLANS = [
    SearchPlan(
        name="Elymus bHLH cold",
        category="Elymus_bHLH_cold",
        target_count=60,
        query=(
            "(\"Elymus nutans\"[Title/Abstract] OR "
            "\"Elymus nutans Griseb\"[Title/Abstract] OR "
            "\"垂穗披碱草\"[Title/Abstract]) AND (\"bHLH\"[Title/Abstract] "
            "OR \"basic helix-loop-helix\"[Title/Abstract]) AND (\"cold tolerance\"[Title/Abstract] "
            "OR \"freezing tolerance\"[Title/Abstract] OR \"cold stress\"[Title/Abstract] "
            "OR \"low temperature\"[Title/Abstract] OR \"freezing stress\"[Title/Abstract] "
            "OR \"chilling stress\"[Title/Abstract]) AND (\"2015/01/01\"[Date - Publication] : "
            "\"2025/11/11\"[Date - Publication]) AND (Plants[MeSH Terms])"
        ),
    ),
    SearchPlan(
        name="Other plants bHLH cold",
        category="OtherPlants_bHLH_cold",
        target_count=60,
        query=(
            "(\"bHLH\"[Title/Abstract] OR \"basic helix-loop-helix\"[Title/Abstract]) AND "
            "(\"cold tolerance\"[Title/Abstract] OR \"freezing tolerance\"[Title/Abstract] OR "
            "\"cold stress\"[Title/Abstract] OR \"low temperature\"[Title/Abstract] OR "
            "\"freezing stress\"[Title/Abstract] OR \"chilling stress\"[Title/Abstract]) AND "
            "(\"Poaceae\"[MeSH Terms] OR \"Triticum aestivum\"[Title/Abstract] OR "
            "\"Hordeum vulgare\"[Title/Abstract] OR \"Oryza sativa\"[Title/Abstract] OR "
            "\"Brachypodium distachyon\"[Title/Abstract] OR \"Leymus\"[Title/Abstract] OR "
            "\"Arabidopsis thaliana\"[Title/Abstract]) AND (\"2015/01/01\"[Date - Publication] : "
            "\"2025/11/11\"[Date - Publication]) AND (Plants[MeSH Terms])"
        ),
    ),
    SearchPlan(
        name="Other cold tolerance genes",
        category="OtherGenes_cold_functional",
        target_count=120,
        query=(
            "(\"cold tolerance\"[Title/Abstract] OR \"freezing tolerance\"[Title/Abstract] OR "
            "\"cold stress\"[Title/Abstract] OR \"low temperature\"[Title/Abstract] OR "
            "\"freezing stress\"[Title/Abstract] OR \"chilling stress\"[Title/Abstract]) AND "
            "(\"ICE1\"[Title/Abstract] OR \"CBF\"[Title/Abstract] OR \"DREB\"[Title/Abstract] OR "
            "\"NAC\"[Title/Abstract] OR \"WRKY\"[Title/Abstract] OR \"MYB\"[Title/Abstract] OR "
            "\"bZIP\"[Title/Abstract] OR \"AP2/ERF\"[Title/Abstract] OR \"transcription factor\"[Title/Abstract]) "
            "AND (\"overexpression\"[Title/Abstract] OR \"knockout\"[Title/Abstract] OR "
            "\"RNA interference\"[Title/Abstract] OR \"gene silencing\"[Title/Abstract] OR "
            "\"mutant\"[Title/Abstract] OR \"CRISPR\"[Title/Abstract]) AND (Plants[MeSH Terms]) AND "
            "(\"2015/01/01\"[Date - Publication] : \"2025/11/11\"[Date - Publication])"
        ),
    ),
]


def plan_searches(min_total: int = MIN_TOTAL) -> List[SearchPlan]:
    plans: List[SearchPlan] = []
    accumulated = 0
    for plan in SEARCH_PLANS:
        plans.append(plan)
        accumulated += plan.target_count
        if accumulated >= min_total:
            break
    return plans


def chunked(iterable: List[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(iterable), size):
        yield iterable[idx : idx + size]


def collect_articles(client: PubMedClient, plan: SearchPlan, max_results: int = 300) -> List[Article]:
    pmids = client.search(plan.query, retmax=max_results)
    articles: List[Article] = []
    for batch in chunked(pmids, 50):
        details = client.fetch_details(batch)
        for pmid in batch:
            info = details.get(pmid)
            if not info:
                continue
            art = Article(
                pmid=pmid,
                title=info.get("title", ""),
                journal=info.get("journal", ""),
                pub_date=info.get("pub_date", ""),
                category=plan.category,
                doi=info.get("doi"),
                pmcid=info.get("pmcid"),
            )
            articles.append(art)
    return articles


def save_metadata(articles: List[Article], save_root: str) -> None:
    csv_path = os.path.join(save_root, "metadata.csv")
    fieldnames = [
        "pmid",
        "title",
        "journal",
        "pub_date",
        "category",
        "doi",
        "pmcid",
        "pdf_url",
        "filename",
        "source",
        "notes",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for art in articles:
            writer.writerow({
                "pmid": art.pmid,
                "title": art.title,
                "journal": art.journal,
                "pub_date": art.pub_date,
                "category": art.category,
                "doi": art.doi,
                "pmcid": art.pmcid,
                "pdf_url": art.pdf_url,
                "filename": art.filename,
                "source": art.source,
                "notes": art.notes,
            })


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download open-access cold tolerance gene papers (Windows).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="Contact email for NCBI E-utilities")
    parser.add_argument("--api-key", dest="api_key", default=None, help="NCBI API key to increase rate limits")
    parser.add_argument("--save-root", default=SAVE_ROOT, help="Directory for saving PDFs and metadata")
    parser.add_argument("--min-total", type=int, default=MIN_TOTAL, help="Minimum total papers to attempt")
    parser.add_argument("--retmax", type=int, default=400, help="Maximum results per query")
    args = parser.parse_args()

    client = PubMedClient(email=args.email, api_key=args.api_key)
    downloader = Downloader(client, save_root=args.save_root)

    selected_plans = plan_searches(args.min_total)
    all_articles: List[Article] = []

    for plan in selected_plans:
        print(f"Running search: {plan.name}")
        arts = collect_articles(client, plan, max_results=args.retmax)
        print(f"  Found {len(arts)} records for {plan.category}")
        for art in arts:
            downloader.download_article(art)
        all_articles.extend(arts)

    # Deduplicate by PMID
    unique: Dict[str, Article] = {}
    for art in all_articles:
        if art.pmid not in unique:
            unique[art.pmid] = art
    all_articles = list(unique.values())

    print(f"Total unique records collected: {len(all_articles)}")
    save_metadata(all_articles, downloader.save_root)
    print(f"Metadata saved to {os.path.join(downloader.save_root, 'metadata.csv')}")
    missing_pdf = [a for a in all_articles if not a.filename]
    if missing_pdf:
        print(f"PDF missing for {len(missing_pdf)} records; see notes column for reasons.")

    if len(all_articles) < args.min_total:
        print("Warning: fewer articles than requested; adjust queries or retmax to collect more.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
