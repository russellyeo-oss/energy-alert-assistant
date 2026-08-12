import json
import os
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup
from openai import OpenAI


MODEL = "gpt-5-mini"
RESULT_PREFIX = "ENERGY ALERT RESULTS"

client = OpenAI()

github_token = os.environ["GITHUB_TOKEN"]
repository = os.environ["GITHUB_REPOSITORY"]

api_base = f"https://api.github.com/repos/{repository}"


def github_request(method, path, data=None):
    url = f"{api_base}{path}"

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "energy-alert-assistant",
    }

    body = None

    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read().decode("utf-8")

        if not content:
            return None

        return json.loads(content)


def get_open_alert_issues():
    issues = github_request(
        "GET",
        "/issues?state=open&per_page=100",
    )

    alert_issues = []

    for issue in issues:
        # Pull requests can also appear in the issues API.
        if "pull_request" in issue:
            continue

        title = issue.get("title", "")

        # Never reprocess our own results issue.
        if title.startswith(RESULT_PREFIX):
            continue

        alert_issues.append(issue)

    return alert_issues


def extract_article_links(html):
    soup = BeautifulSoup(html or "", "html.parser")

    articles = []
    seen_urls = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()

        parsed = urllib.parse.urlparse(href)

        if parsed.netloc not in (
            "www.google.com",
            "google.com",
            "www.google.com.au",
            "google.com.au",
        ):
            continue

        if parsed.path != "/url":
            continue

        query = urllib.parse.parse_qs(parsed.query)
        article_urls = query.get("url")

        if not article_urls:
            continue

        article_url = article_urls[0].strip()

        if article_url in seen_urls:
            continue

        seen_urls.add(article_url)

        domain = urllib.parse.urlparse(
            article_url
        ).netloc.lower()

        # Do not alert ENB to its own stories.
        if (
            domain == "energynewsbulletin.net"
            or domain.endswith(".energynewsbulletin.net")
        ):
            continue

        headline = tag.get_text(" ", strip=True)

        articles.append(
            {
                "headline": headline,
                "url": article_url,
            }
        )

    return articles


def filter_relevant_articles(articles):
    if not articles:
        return []

    numbered = []

    for number, article in enumerate(articles, start=1):
        numbered.append(
            f"{number}. HEADLINE: {article['headline']}\n"
            f"URL: {article['url']}"
        )

    article_text = "\n\n".join(numbered)

    prompt = f"""
You are an editorial news filter for Energy News Bulletin (ENB),
a specialist Australian business publication covering the energy industry.

Identify links that could represent useful NEWS LEADS for an Energy News Bulletin (ENB) journalist covering the AUSTRALIAN energy industry.

The central test is: could this reasonably lead to an ENB story about Australian energy?

KEEP genuine substantive new developments concerning:
- Australian oil and gas
- LNG projects, production and markets materially connected to Australia
- Australian upstream exploration and production
- Australian gas supply and gas markets
- Australian electricity and power generation
- Australian electricity markets
- Australian transmission and grids
- Australian renewables, solar and wind
- Australian batteries and energy storage
- Australian nuclear energy policy or projects
- Australian uranium developments where materially relevant to energy
- Australian hydrogen
- Australian carbon capture and storage
- Australian energy policy, regulation and government decisions
- environmental approvals affecting Australian energy projects
- major Australian energy infrastructure
- Australian energy companies and projects
- mergers, acquisitions and major corporate developments materially affecting Australian energy businesses
- material financial, operational or strategic developments at Australian energy companies
- overseas projects or developments involving Australian-headquartered energy companies where the development is material to that company
- major international developments ONLY where there is a clear and material consequence for the Australian energy industry, Australian energy supply, Australian energy prices, or a major Australian energy company

REJECT:
- overseas BP, ExxonMobil, Shell, Chevron or other international energy stories merely because those companies also operate in Australia
- international oil and gas discoveries, projects, contracts, disputes or corporate news with no material Australian connection
- incidental mentions of Australia or an Australian company
- routine share-price movements
- stories whose main development is simply that an energy company's share price rose or fell
- generic investment and stock-picking content
- broker ratings unless based on a genuinely new material company development
- valuation pages and financial ratios
- technical-analysis pages
- generic market round-ups
- static financial data
- "which stock is better?" articles without a new substantive development
- duplicated or substantially duplicated results
- Energy News Bulletin's own articles

Do NOT keep an article merely because the Google Alert search term is the name of a company relevant to ENB.

When uncertain, ask whether the underlying NEW DEVELOPMENT itself has a meaningful Australian energy angle. If it does not, reject it.

Return ONLY valid JSON in this exact format:

{{
  "relevant": [1, 3]
}}

If none are relevant:

{{
  "relevant": []
}}

ARTICLES:

{article_text}
"""

    response = client.responses.create(
        model=MODEL,
        tools=[
            {
                "type": "web_search"
            }
        ],
        input=prompt,
    )

    result_text = response.output_text.strip()

    if result_text.startswith("```"):
        result_text = result_text.replace("```json", "")
        result_text = result_text.replace("```", "")
        result_text = result_text.strip()

    result = json.loads(result_text)

    relevant_numbers = result.get("relevant", [])

    relevant_articles = []

    for number in relevant_numbers:
        if (
            isinstance(number, int)
            and 1 <= number <= len(articles)
        ):
            relevant_articles.append(
                articles[number - 1]
            )

    return relevant_articles


def create_results_issue(articles, processed_count):
    lines = [
        f"Processed {processed_count} Google Alert issue(s).",
        "",
        "## Relevant energy links",
        "",
    ]

    for article in articles:
        headline = article["headline"] or article["url"]
        url = article["url"]

        lines.append(f"- [{headline}]({url})")

    github_request(
    "POST",
    "/issues",
    {
        "title": (
            f"{RESULT_PREFIX} - "
            f"{len(articles)} relevant link(s)"
        ),
        "body": "\n".join(lines),
        "assignees": ["russellyeo-oss"],
    },
)


def close_issue(issue_number):
    github_request(
        "PATCH",
        f"/issues/{issue_number}",
        {
            "state": "closed"
        },
    )


issues = get_open_alert_issues()

if not issues:
    print("No alert issues waiting.")
    raise SystemExit


all_articles = []
seen_urls = set()

for issue in issues:
    body = issue.get("body", "")

    articles = extract_article_links(body)

    for article in articles:
        if article["url"] in seen_urls:
            continue

        seen_urls.add(article["url"])
        all_articles.append(article)


print(f"Alert issues waiting: {len(issues)}")
print(f"Unique candidate links: {len(all_articles)}")


relevant_articles = filter_relevant_articles(
    all_articles
)

print(
    f"Relevant links: {len(relevant_articles)}"
)


if relevant_articles:
    create_results_issue(
        relevant_articles,
        len(issues),
    )

    print("Results issue created.")

else:
    print("No relevant links found.")


# Only close the source issues after all processing
# above has completed successfully.
for issue in issues:
    close_issue(issue["number"])


print(
    f"Closed {len(issues)} processed source issue(s)."
)
