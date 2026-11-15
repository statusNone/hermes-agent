"""Focused checks for the exported local ContextEngine result observer."""
import unittest

from agent.conversation_loop import _safe_cleanup_categories, _turn_result_projection


class TurnResultObserverTests(unittest.TestCase):
    def test_projection_is_allowlisted_and_detached(self):
        result = {
            "messages": [{"role": "user", "content": "private prompt"}],
            "final_response": "private answer",
            "error": "provider prose",
            "completed": True,
            "failed": False,
            "turn_id": "turn-1",
            "api_calls": 2,
            "cleanup_errors": ["persist_session: provider prose"],
        }
        observed = _turn_result_projection(result)
        self.assertEqual(
            observed,
            {"completed": True, "failed": False, "turn_id": "turn-1", "api_calls": 2,
             "cleanup_errors": ["persist_session"]},
        )
        observed["cleanup_errors"].append("mutated")
        self.assertEqual(result["cleanup_errors"], ["persist_session: provider prose"])

    def test_cleanup_categories_drop_unstructured_content(self):
        self.assertEqual(_safe_cleanup_categories(["one: detail", 3, "", "bad value"]), ["one"])


if __name__ == "__main__":
    unittest.main()
