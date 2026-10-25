"""Regression checks for Google Alert link parsing and acknowledgement safety."""
import ast
from pathlib import Path
import unittest
import urllib.parse
from bs4 import BeautifulSoup

SOURCE = Path(__file__).resolve().parents[1].joinpath('energy_alert_cloud.py').read_text()
TREE = ast.parse(SOURCE)

class IntakeSafetyTests(unittest.TestCase):
    def test_google_redirect_is_parsed(self):
        fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'extract_article_links')
        scope = {'BeautifulSoup': BeautifulSoup, 'urllib': urllib}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<extract>', 'exec'), scope)
        alert = '<a href="https://www.google.com/url?sa=t&amp;url=https%3A%2F%2Fexample.com%2Fenergy-story">Energy story</a>'
        self.assertEqual(scope['extract_article_links'](alert), [{'headline': 'Energy story', 'url': 'https://example.com/energy-story'}])

    def test_unparsed_issues_are_not_closed(self):
        self.assertIn('if not articles:', SOURCE)
        self.assertIn('parsed_issues.append(issue)', SOURCE)
        self.assertIn('for issue in parsed_issues:\n    close_issue', SOURCE)
        self.assertNotIn('for issue in issues:\n    close_issue', SOURCE)

if __name__ == '__main__':
    unittest.main()
