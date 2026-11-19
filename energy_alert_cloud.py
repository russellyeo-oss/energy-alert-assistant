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
    articles, seen_urls = [], set()
    google_hosts = {"www.google.com", "google.com", "www.google.com.au", "google.com.au"}
    navigation = {"facebook", "twitter", "flag as irrelevant", "see more results",
                  "edit this alert", "unsubscribe", "view all your alerts",
                  "rss", "receive this alert as rss feed", "send feedback"}
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        headline = tag.get_text(" ", strip=True)
        # A news lead must be identifiable from its clickable wording.
        if (not headline or headline.casefold() in navigation or
                len(headline.split()) < 3 or len(headline) < 18 or
                headline.lower().startswith(("http://", "https://", "www.")) or
                headline.casefold() in {"code of conduct", "register of lobbyists", "about the register", "read more here", "click here for details", "find out more", "view full article", "read full article"}):
            continue
        # Outlook can wrap the original Google redirect in Safe Links.
        for _ in range(3):
            parsed = urllib.parse.urlparse(href)
            host = (parsed.hostname or "").lower()
            params = urllib.parse.parse_qs(parsed.query)
            if host.endswith(".safelinks.protection.outlook.com"):
                values = params.get("url", [])
            elif host in google_hosts and parsed.path == "/url":
                values = params.get("url") or params.get("q") or []
            else:
                break
            if not values:
                break
            href = values[0].strip()
        parsed = urllib.parse.urlparse(href)
        domain = (parsed.hostname or "").lower()
        if parsed.scheme not in ("https", "http") or not domain or parsed.username or parsed.password:
            continue
        if (domain in google_hosts or domain.endswith(".google.com") or
                domain.endswith(".google.com.au") or domain == "energynewsbulletin.net" or
                domain.endswith(".energynewsbulletin.net") or
                domain in {"facebook.com", "twitter.com", "x.com", "t.co", "www.facebook.com", "www.twitter.com", "www.x.com"} or
                domain.endswith(".safelinks.protection.outlook.com")):
            continue
        # Reject static register/administration links, not reporting about them.
        path = parsed.path.lower()
        if ("lobbyist" in domain or
                (domain.endswith(".gov.au") and any(t in path for t in ("registeroflobbyists", "lobbyistsregister", "/lobbyists/"))) or
                path.rstrip("/").endswith(("/code-conduct", "/code-of-conduct", "/about-the-register.aspx"))):
            continue
        if not parsed.path.strip("/") or href in seen_urls:
            continue
        seen_urls.add(href)
        # Google Alerts commonly puts the publisher and excerpt in a nearby table cell.
        cell = tag.find_parent("td")
        context = cell.get_text(" ", strip=True)[:1800] if cell else ""
        article = {"headline": headline, "url": href}
        if context and context != headline:
            article["context"] = context
        articles.append(article)
    return articles


def filter_relevant_articles(articles):
    if not articles:
        return []

    numbered = []

    for number, article in enumerate(articles, start=1):
        numbered.append(
            f"{number}. HEADLINE: {article['headline']}\n"
            f"URL: {article['url']}\nEXCERPT: {article.get('context', '')}"
        )

    article_text = "\n\n".join(numbered)

    prompt = f"""
You are an editorial news-lead filter for Energy News Bulletin (ENB),
a specialist Australian business publication covering the energy industry.

Your job is NOT to decide whether ENB should ultimately publish a story.

Your job is to identify potentially useful NEWS LEADS that an ENB
journalist may want to inspect.

It is better to include a plausible energy news lead and let the
journalist reject it than to silently discard a potentially important
development.

CENTRAL TEST:

Could the headline, article, or underlying development reasonably
contain new information that could lead to an ENB story concerning
Australian energy, an Australian energy company, or a project/company
materially relevant to the Australian energy industry?

If YES or PLAUSIBLY YES, KEEP IT.

When genuinely uncertain, INCLUDE rather than exclude.

KEEP potential new developments concerning:

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
- Australian uranium developments relevant to energy
- Australian hydrogen
- Australian carbon capture and storage
- Australian energy policy, regulation and government decisions
- environmental approvals affecting Australian energy projects
- major Australian energy infrastructure
- Australian energy companies and projects
- mergers, acquisitions and corporate developments affecting Australian energy businesses
- financial, operational or strategic developments at Australian energy companies
- project milestones, delays, commissioning, startup, production, outages, approvals, contracts or disputes
- overseas projects or developments involving Australian-headquartered energy companies where the development could be material to that company
- major international developments where there could be a material consequence for Australian energy supply, prices, policy, projects or companies

IMPORTANT:

A headline does NOT need to prove that a major development has occurred.

If the headline plausibly indicates that an article contains new
information about a relevant company, project, policy or development,
KEEP IT so the journalist can inspect it.

For example, an article specifically discussing Santos's Barossa
project, its transition to production, project execution, startup,
schedule, costs or operational outlook is a potentially useful ENB
lead and should normally be KEPT.

Similarly, a headline framed as a question should not be rejected
merely because it is a question. Judge what underlying development
the article appears to concern.

REJECT obvious low-value material such as:

- static registers, codes of conduct, navigation pages, and reference links rather than actual news or new substantive announcements
- generic hyperlink labels that do not identify a story; never treat a geographic label or organisation name alone as a headline

- routine share-price movements with no underlying new development
- generic investment and stock-picking articles
- generic "why this stock is worth watching" articles
- generic "why is this stock in focus?" articles where there is no indication of a substantive new development
- valuation pages and financial-ratio pages
- technical-analysis pages
- generic market round-ups
- static financial data
- "which stock is better?" articles without a new substantive development
- broker ratings where there is no new underlying company development
- overseas BP, ExxonMobil, Shell, Chevron or other international energy stories with no meaningful Australian connection
- international projects, contracts or disputes with no meaningful Australian connection
- incidental mentions of Australia or an Australian company
- duplicated or substantially duplicated results
- Energy News Bulletin's own articles

Do NOT reject a potentially useful lead simply because the publisher
appears investment-focused or because the headline contains an ASX
ticker. Assess whether the article appears to contain a potentially
new substantive development.

Remember:

This is a NEWS-LEAD FILTER, not a final editorial gatekeeper.

FALSE NEGATIVES ARE MORE COSTLY THAN FALSE POSITIVES.

When uncertain between KEEP and REJECT, KEEP.

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

    # Explicit Australian energy project/company leads must survive model false negatives.
    # Only applies to already parsed descriptive article links.
    selected = {a["url"] for a in relevant_articles}
    for article in articles:
        headline = (article["headline"] + " " + article.get("context", "")).casefold()
        if any(term in headline for term in ("woodside energy", "scarborough lng", "browse gas", "barossa lng", "ichthys lng", "gorgon lng", "beetaloo gas", "australian gas", "australian electricity", "australian energy")):
            if article["url"] not in selected:
                relevant_articles.append(article)
                selected.add(article["url"])
    return relevant_articles


def create_results_issue(articles, processed_count):
    html_lines = [
        "<p><b>Relevant energy links</b></p>",
    ]

    for article in articles:
        headline = article["headline"] or article["url"]
        url = article["url"]

        html_lines.append(
            f'<p><a href="{url}">{headline}</a></p>'
        )

    html_lines.append("<hr>")

    if processed_count == 1:
        html_lines.append(
            "<p>Processed 1 Google Alert email.</p>"
        )
    else:
        html_lines.append(
            f"<p>Processed {processed_count} Google Alert emails.</p>"
        )

    github_request(
        "POST",
        "/issues",
        {
            "title": (
                f"{RESULT_PREFIX} - "
                f"{len(articles)} relevant link(s)"
            ),
            "body": "\n".join(html_lines),
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



def main():
    issues = get_open_alert_issues()

    if not issues:
        print("No alert issues waiting.")
        raise SystemExit


    all_articles = []
    seen_urls = set()
    parsed_issues = []

    for issue in issues:
        body = issue.get("body", "")

        articles = extract_article_links(body)
        if not articles:
            # Never close an input issue that has not actually been parsed.
            print("UNPARSED_SOURCE_ISSUE: %s retained" % issue["number"])
            continue
        parsed_issues.append(issue)

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
            len(parsed_issues),
        )

        print("Results issue created.")

    else:
        print("No relevant links found.")


    # Only close the source issues after all processing
    # above has completed successfully.
    for issue in parsed_issues:
        close_issue(issue["number"])


    print(
        f"Closed {len(parsed_issues)} parsed source issue(s); retained {len(issues) - len(parsed_issues)} unparsed issue(s)."
    )


if __name__ == '__main__':
    main()
