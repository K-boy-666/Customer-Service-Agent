"""Customer-service scenarios that exercise FAQ RAG retrieval end to end."""

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


SCENARIOS = [
    {
        "name": "product_displayport_interface",
        "message": "\u6211\u770b\u4e0a\u4f60\u4eec\u90a3\u4e2a27\u5bf84K\u663e\u793a\u5668\u4e86\uff0c\u80fd\u4e0d\u80fd\u63a5DP\u63a5\u53e3\uff1f",
        "expected_category": "\u4ea7\u54c1\u54a8\u8be2",
        "expected_faq": "faq-043",
        "expected_reply_terms": ("DisplayPort", "HDMI", "USB-C"),
    },
    {
        "name": "delivery_eta_without_exact_keyword",
        "message": "\u5982\u679c\u6211\u4eca\u5929\u4e0b\u5355\uff0c\u5305\u88f9\u5927\u6982\u54ea\u5929\u80fd\u5230\u6211\u8fd9\u8fb9\uff1f",
        "expected_category": "\u7269\u6d41\u914d\u9001",
        "expected_faq": "faq-021",
        "expected_reply_terms": ("1-2\u5929", "3-5\u5929", "\u9884\u8ba1\u9001\u8fbe"),
    },
    {
        "name": "return_policy_natural_expression",
        "message": "\u4e1c\u897f\u5230\u4e86\u4ee5\u540e\u6211\u4e0d\u60f3\u7528\u4e86\u8fd8\u80fd\u9000\u5417\uff1f",
        "expected_category": "\u9000\u8d27\u653f\u7b56",
        "expected_faq": "faq-001",
        "expected_reply_terms": ("7\u5929", "\u9000", "\u5546\u54c1"),
    },
    {
        "name": "payment_failure_troubleshooting",
        "message": "\u6211\u4ed8\u6b3e\u4e00\u76f4\u5931\u8d25\uff0c\u6362\u4e86\u4e24\u6b21\u8fd8\u662f\u4ed8\u4e0d\u4e86\uff0c\u5e94\u8be5\u600e\u4e48\u529e\uff1f",
        "expected_category": "\u652f\u4ed8\u4e0e\u53d1\u7968",
        "expected_faq": "faq-033",
        "expected_reply_terms": ("\u4f59\u989d", "\u652f\u4ed8", "\u91cd\u65b0\u4e0b\u5355"),
    },
    {
        "name": "warranty_scope_for_broken_port",
        "message": "\u952e\u76d8\u63a5\u53e3\u7a81\u7136\u5931\u7075\u4e86\uff0c\u8fd9\u79cd\u95ee\u9898\u4fdd\u4fee\u7ba1\u4e0d\u7ba1\uff1f",
        "expected_category": "\u4fdd\u4fee\u6761\u6b3e",
        "expected_faq": "faq-038",
        "expected_reply_terms": ("\u4fdd\u4fee", "\u63a5\u53e3\u5931\u7075", "\u4eba\u4e3a\u635f\u574f"),
    },
]


class RagCustomerScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.retriever = FaqRetrievalService(FAQ_PATH, backend="lexical")
        self.runtime = CustomerServiceOrchestrator(
            tools=LocalCustomerServiceTools(faq_retriever=self.retriever)
        )

    def test_customer_messages_route_to_expected_faq_answers(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario["name"]):
                result = self.runtime.handle_message(
                    scenario["message"],
                    conversation_id=f"rag-{scenario['name']}",
                )
                self.assertEqual(result["status"], "success")
                self.assertIn("consultation-agent", result["dispatched_agents"])
                self.assertIn("search_faq", {call["tool"] for call in result["tool_calls"]})
                self.assertIn(
                    f"FAQ matched id={scenario['expected_faq']} category={scenario['expected_category']}",
                    result["agent_results"][0]["internal_notes"],
                )
                for term in scenario["expected_reply_terms"]:
                    self.assertIn(term, result["customer_reply"])

    def test_mcp_faq_tool_supports_same_customer_scenarios(self):
        import server_customer

        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario["name"]):
                raw = asyncio.run(server_customer.search_faq(scenario["message"], limit=1))
                data = json.loads(raw)
                self.assertEqual(data[0]["id"], scenario["expected_faq"])
                self.assertEqual(data[0]["category"], scenario["expected_category"])
                self.assertIn("relevance", data[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
