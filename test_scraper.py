import unittest

from scrape_agri_psa import detect_language, extract_page, is_relevant, is_usable_text_block, record_id


class ExtractionTests(unittest.TestCase):
    def test_english_advisory_and_swahili_alternate(self):
        html = """
        <html lang="en"><head><title>Fall armyworm alert</title>
        <link rel="alternate" hreflang="sw" href="/sw/tahadhari" /></head>
        <body><main><h1>Fall armyworm alert</h1><p>
        Farmers should inspect maize crops weekly and report fall armyworm damage
        to agricultural extension officers.
        </p></main></body></html>
        """
        page = extract_page(html, "https://example.org/en/alert")
        self.assertEqual(page["alternates"], [("sw", "https://example.org/sw/tahadhari")])
        self.assertEqual(detect_language(page["blocks"][0], page["page_lang"]), ("English", "en"))
        self.assertTrue(is_relevant(page["title"], page["body_text"], {"kenya_context_required": False}, True))
        self.assertTrue(record_id("https://example.org/en/alert", "en", page["blocks"][0]).startswith("KAPSA-"))

    def test_unrelated_text_is_excluded(self):
        self.assertFalse(is_relevant("Office opening", "The office will close at five o'clock.", {"kenya_context_required": False}, False))

    def test_navigation_and_references_are_excluded(self):
        self.assertFalse(is_usable_text_block("Crops Fruits and Vegetables Beans (Revised) Maize (Revised) Potato (Revised) Pest Diseases"))
        self.assertFalse(is_usable_text_block("Spinach: Crop Protection Compendium. www.example.org ISBN: 1234-5678."))
        self.assertTrue(is_usable_text_block("Apply copper-based fungicides before flowering, during fruit formation, and after harvest."))


if __name__ == "__main__":
    unittest.main()
