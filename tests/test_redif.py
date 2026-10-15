import unittest
from pathlib import Path

from app.redif import parse_file, parse_redif
from app.store import catalog_for


class RedifTests(unittest.TestCase):
    def test_multiline_fields_and_repeated_authors(self):
        source = """Template-Type: ReDIF-Article 1.0
Author-Name: A. Researcher
Author-X-Name-First: A.
Author-Name: B. Researcher
Title: Peace &amp; security
Abstract:
  First line with a colon: still abstract.
 second line.
Volume: 2
Issue: 3
Year: 2002
Pages: 9-11
X-DOI: 10.1234/test
Handle: RePEc:taf:defpea:v:2:y:2002:i:3:p:9-11
"""
        article = parse_redif(source)[0]
        self.assertEqual(article["authors"], ["A. Researcher", "B. Researcher"])
        self.assertEqual(article["title"], "Peace & security")
        self.assertEqual(article["abstract"], "First line with a colon: still abstract. second line.")
        self.assertEqual(article["repec_url"], "https://ideas.repec.org/a/taf/defpea/v2y2002i3p9-11.html")

    def test_publisher_archive_is_complete_and_groupable(self):
        articles = parse_file(Path(__file__).resolve().parents[1] / "data/defpea.redif")
        self.assertGreaterEqual(len(articles), 1000)
        self.assertEqual(len({item["handle"] for item in articles}), len(articles))
        catalog = catalog_for(articles)
        self.assertEqual(catalog["volumes"][0]["number"], "37")
        self.assertEqual(catalog["volumes"][-1]["number"], "8")
        self.assertEqual(sum(issue["count"] for volume in catalog["volumes"] for issue in volume["issues"]), len(articles))
        volume_21 = next(volume for volume in catalog["volumes"] if volume["number"] == "21")
        self.assertEqual(volume_21["issues"][-1]["number"], "5-6")
        newest = catalog["volumes"][0]
        issue = newest["issues"][-1]["number"]
        records = [a for a in catalog["articles"] if a["volume"] == newest["number"] and a["issue"] == issue]
        self.assertEqual(len(records), newest["issues"][-1]["count"])
        self.assertTrue(all(item["volume"] == newest["number"] and item["issue"] == issue for item in records))


if __name__ == "__main__":
    unittest.main()
