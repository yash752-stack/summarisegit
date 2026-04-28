from __future__ import annotations

from pathlib import Path

from app.services.analyzer import RepositoryAnalyzer



def test_repository_analyzer_builds_summary_and_impact(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "def calculate_payment():",
                "    return helper()",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "\n".join(
            [
                "from service import calculate_payment",
                "",
                "def run():",
                "    return calculate_payment()",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text("def test_payment():\n    assert True\n", encoding="utf-8")

    analyzer = RepositoryAnalyzer(tmp_path)
    report = analyzer.analyze()

    assert report["summary"]["total_files"] == 3
    assert report["summary"]["total_functions"] >= 3

    impact = analyzer.impact_analysis("calculate_payment")
    assert impact["direct_dependents"]
    assert "consumer.py" in impact["affected_files"]
