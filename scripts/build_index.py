"""Warm or validate the FAQ RAG index.

Run after faq.json changes, or before deployment to confirm the selected
retrieval backend can build successfully.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, os.path.join(str(ROOT), "src"))

from kb_service import get_faq_retriever


def main() -> None:
    faq_path = ROOT / "faq.json"
    retriever = get_faq_retriever(str(faq_path))
    print(f"Indexed {len(retriever.entries)} FAQ entries with {retriever.active_backend}")


if __name__ == "__main__":
    main()
