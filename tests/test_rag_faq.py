"""Tests for the FAQ RAG retrieval layer."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from kb_service import FaqRetrievalService
from orchestrator_runtime import CustomerServiceOrchestrator, LocalCustomerServiceTools


FAQ_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "faq.json")


class RagFaqTest(unittest.TestCase):
    def setUp(self) -> None:
        self.retriever = FaqRetrievalService(FAQ_PATH, backend="lexical")

    def test_semantic_like_faq_queries_hit_expected_categories(self):
        cases = [
            ("显示器支持DP接口吗", "产品咨询", "faq-043"),
            ("我的包裹什么时候到", "物流配送", None),
            ("不想用了能退吗", "退货政策", None),
            ("支付一直失败怎么办", "支付与发票", "faq-033"),
        ]
        for query, category, expected_id in cases:
            with self.subTest(query=query):
                rows = self.retriever.search(query, limit=3)
                self.assertTrue(rows)
                self.assertEqual(rows[0]["category"], category)
                if expected_id:
                    self.assertEqual(rows[0]["id"], expected_id)
                self.assertIn("relevance", rows[0])
                self.assertIn("retriever", rows[0])

    def test_category_filter_and_id_lookup_share_same_retriever(self):
        rows = self.retriever.search("支付一直失败怎么办", category="物流配送", limit=3)
        self.assertTrue(rows)
        self.assertTrue(all(row["category"] == "物流配送" for row in rows))
        entry = self.retriever.get_by_id("faq-043")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["category"], "产品咨询")
        self.assertGreater(len(self.retriever.categories()), 1)

    def test_orchestrator_consultation_uses_full_message_rag_query(self):
        tools = LocalCustomerServiceTools(faq_retriever=self.retriever)
        runtime = CustomerServiceOrchestrator(tools=tools)
        result = runtime.handle_message("显示器支持DP接口吗", conversation_id="rag-consultation")
        self.assertEqual(result["status"], "success")
        self.assertIn("consultation-agent", result["dispatched_agents"])
        self.assertIn("DisplayPort", result["customer_reply"])
        self.assertIn("search_faq", {call["tool"] for call in result["tool_calls"]})

    def test_mcp_search_faq_uses_rag_backend(self):
        import server_customer

        raw = asyncio.run(server_customer.search_faq("显示器支持DP接口吗", limit=1))
        data = json.loads(raw)
        self.assertEqual(data[0]["id"], "faq-043")
        self.assertIn("retriever", data[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
