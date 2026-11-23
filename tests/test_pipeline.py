import json
import unittest
from unittest.mock import Mock
from urllib.error import URLError

from triage.pipeline import CONTEXT_LIMIT, Ollama, context_for, encode_context, load_articles, triage


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.articles = load_articles()
        self.ticket = "Saya lupa sandi untuk masuk."
        self.model = Mock()

    def test_context_is_ranked_limited_and_excludes_retrieval_terms(self):
        context = context_for(self.ticket, self.articles)
        self.assertEqual([article["id"] for article in context], ["KB-01"])
        self.assertLessEqual(len(encode_context(context)), CONTEXT_LIMIT)
        self.assertNotIn("terms", context[0])

    def test_match_uses_word_boundaries(self):
        self.assertEqual(context_for("masukan pelanggan", self.articles), [])

    def test_readable_draft_uses_category_from_cited_article(self):
        self.model.generate.return_value = {"citation": "KB-01", "draft": "Gunakan Lupa sandi."}
        result = triage(self.ticket, self.articles, self.model)
        self.assertEqual((result["decision"], result["category"], result["citation"]),
                         ("draft", "akun", "KB-01"))

    def test_no_context_routes_to_review_without_call(self):
        result = triage("Apakah ada integrasi kalender?", self.articles, self.model)
        self.assertEqual(result["reason"], "no_context")
        self.model.generate.assert_not_called()

    def test_unsupported_citation_and_schema_errors_route_to_review(self):
        for answer in ({"citation": "KB-04", "draft": "Jawaban."},
                       {"citation": "KB-01", "draft": ""},
                       {"citation": "KB-01", "draft": 123},
                       {"citation": "KB-01"}, None):
            with self.subTest(answer=answer):
                self.model.generate.return_value = answer
                result = triage(self.ticket, self.articles, self.model)
                self.assertEqual((result["decision"], result["reason"], result["draft"]),
                                 ("review", "invalid_model_response", None))

    def test_model_can_abstain(self):
        self.model.generate.return_value = {"citation": "", "draft": ""}
        self.assertEqual(triage(self.ticket, self.articles, self.model)["reason"], "model_abstained")

    def test_network_failures_and_bad_json_route_to_review(self):
        for error, reason in (
            (TimeoutError(), "model_unavailable"),
            (URLError("connection refused"), "model_unavailable"),
            (json.JSONDecodeError("bad JSON", "?", 0), "invalid_model_response"),
        ):
            with self.subTest(error=error):
                self.model.generate.side_effect = error
                self.assertEqual(triage(self.ticket, self.articles, self.model)["reason"], reason)

    def test_model_client_sends_ticket_as_data_and_requests_json(self):
        model = Ollama("http://127.0.0.1:18634")
        model.request = Mock(return_value={"message": {"content": '{"citation":"","draft":""}'}})
        context = context_for(self.ticket, self.articles)
        model.generate(self.ticket, context)
        route, request = model.request.call_args.args
        self.assertEqual(route, "/api/chat")
        self.assertFalse(request["stream"])
        self.assertEqual(request["format"]["type"], "object")
        self.assertEqual(json.loads(request["messages"][1]["content"]),
                         {"ticket": self.ticket, "references": context})


if __name__ == "__main__":
    unittest.main()
