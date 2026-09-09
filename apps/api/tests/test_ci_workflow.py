"""第 34 刀：CI 工作流是门禁，不是文档。未设测试库 URL 的路径必须失败。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_requires_postgres_url() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "SUITE_TEST_DATABASE_URL" in text
    assert "skip-green" in text
    assert "pgvector/pgvector:pg16" in text
    assert "uv run pytest" in text
    assert "uv run ruff check" in text
    assert "npm run build" in text
